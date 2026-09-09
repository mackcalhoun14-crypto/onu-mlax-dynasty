import os
import json
import re
import time
import requests

# Bulletproof fallback for blank GitHub Secrets
LEAGUE_ID = os.environ.get("SLEEPER_LEAGUE_ID")
if not LEAGUE_ID or LEAGUE_ID.strip() == "":
    LEAGUE_ID = "1312162066798231552"

GROQ_KEY = os.environ.get("GROQ_API_KEY")

def call_ai(prompt):
    if not GROQ_KEY:
        print("CRITICAL ERROR: GROQ_API_KEY environment variable is not set.")
        return None

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {
                "role": "system",
                "content": "You are a sharp, factual NFL fantasy football commissioner. Never hallucinate facts, injuries, or player tenure. Always refer to fantasy franchises by their actual team names, never as generic placeholders like Team A or Team B."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.25
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "").strip()
            elif resp.status_code == 429:
                print("Rate limited by Groq, waiting 10s...")
                time.sleep(10)
            else:
                print(f"Groq API Error {resp.status_code}: {resp.text}")
                time.sleep(3)
        except Exception as e:
            print(f"AI call exception: {e}")
            time.sleep(3)
    return None

def parse_ai_forecast(ai_text, team_a_name, team_b_name):
    proj_a = 115.4
    proj_b = 109.8
    win_a = "54%"
    win_b = "46%"

    if not ai_text:
        return proj_a, proj_b, win_a, win_b, f"Matchup preview for {team_a_name} vs {team_b_name} pending lineup confirmation."

    # 1. Extract numeric projections
    for line in ai_text.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("PROJ_TEAM_A:"):
            val = line_clean.replace("PROJ_TEAM_A:", "").replace("pts", "").strip()
            nums = re.findall(r"\d+\.?\d*", val)
            if nums: proj_a = float(nums[0])
        elif line_clean.startswith("PROJ_TEAM_B:"):
            val = line_clean.replace("PROJ_TEAM_B:", "").replace("pts", "").strip()
            nums = re.findall(r"\d+\.?\d*", val)
            if nums: proj_b = float(nums[0])
        elif line_clean.startswith("WINPCT_TEAM_A:"):
            win_a = line_clean.replace("WINPCT_TEAM_A:", "").strip()
        elif line_clean.startswith("WINPCT_TEAM_B:"):
            win_b = line_clean.replace("WINPCT_TEAM_B:", "").strip()

    # 2. Extract clean preview, discarding scratchpad
    try:
        preview_start = ai_text.index("**🥊 Tale of the Tape:**")
        preview_body = ai_text[preview_start:].strip()
    except ValueError:
        preview_body = ai_text.strip()

    # 3. Deterministic Safety Net: Replace any lingering generic tokens with actual team names
    preview_body = re.sub(r'\bTeam\s+A\b', team_a_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+B\b', team_b_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+1\b', team_a_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+2\b', team_b_name, preview_body, flags=re.IGNORECASE)
        
    return proj_a, proj_b, win_a, win_b, preview_body

def run():
    try:
        print(f"0. Using League ID: {LEAGUE_ID}")
        print("1. Fetching Sleeper NFL State...")
        state_res = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
        state = state_res.json() if state_res.status_code == 200 else {}
        week = state.get("week", 1)
        if week < 1:
            week = 1

        print("2. Fetching League Data from Sleeper...")
        users_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users", timeout=15)
        rosters_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters", timeout=15)
        
        matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}", timeout=15)
        matchups = matchups_res.json() if matchups_res.status_code == 200 else []
        if not matchups and week > 1:
            week = 1
            matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}", timeout=15)
            matchups = matchups_res.json() if matchups_res.status_code == 200 else []

        players_res = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=30)

        users = users_res.json() if users_res.status_code == 200 else []
        rosters = rosters_res.json() if rosters_res.status_code == 200 else []
        players = players_res.json() if players_res.status_code == 200 else {}

        if not isinstance(matchups, list):
            matchups = []

        user_map = {u["user_id"]: (u.get("metadata", {}) or {}).get("team_name") or u.get("display_name") for u in users}
        
        roster_map = {}
        if isinstance(rosters, list) and len(rosters) > 0:
            for r in rosters:
                roster_map[r["roster_id"]] = {
                    "name": user_map.get(r["owner_id"], f"Team {r['roster_id']}"),
                    "wins": (r.get("settings", {}) or {}).get("wins", 0),
                    "losses": (r.get("settings", {}) or {}).get("losses", 0)
                }
        
        if not roster_map:
            print("Warning: Rosters endpoint empty. Generating safety mock rosters...")
            roster_map = {
                1: {"name": "ONU Dynasty 1", "wins": 0, "losses": 0},
                2: {"name": "Midfield Maestro", "wins": 0, "losses": 0},
                3: {"name": "Attack Wing", "wins": 0, "losses": 0},
                4: {"name": "Clear Defenders", "wins": 0, "losses": 0}
            }

        existing_trades_map = {}
        if os.path.exists("data/trades.json"):
            try:
                with open("data/trades.json", "r") as f:
                    old_data = json.load(f)
                    for t in old_data.get("trades", []):
                        existing_trades_map[t["transaction_id"]] = t
            except Exception:
                pass

        # --- PART 1: MATCHUPS ---
        games = {}
        
        if matchups and isinstance(matchups, list) and len(matchups) > 0:
            for m in matchups:
                m_id = m.get("matchup_id")
                if not m_id:
                    continue
                if m_id not in games:
                    games[m_id] = []

                starters = []
                for p_id in m.get("starters", []):
                    p = players.get(str(p_id)) or {}
                    p_name = p.get("full_name") or str(p_id)
                    p_pos = p.get("position") or "FLEX"
                    p_team = p.get("team") or "FA"
                    
                    years_exp = p.get("years_exp")
                    if years_exp is None or years_exp == 0:
                        exp_tag = "Rookie"
                    else:
                        exp_tag = f"{years_exp}y veteran"
                        
                    starters.append(f"{p_name} ({p_pos}, NFL: {p_team}, {exp_tag})")

                t_info = roster_map.get(m["roster_id"], {"name": f"Team {m['roster_id']}", "wins": 0, "losses": 0})
                games[m_id].append({
                    "roster_id": m["roster_id"],
                    "team_name": t_info["name"],
                    "record": f"{t_info['wins']}-{t_info['losses']}",
                    "starters": starters
                })

        if not games and roster_map:
            print("Sleeper returned 0 official matchups. Forcing default head-to-head pairings from rosters...")
            sorted_rosters = list(roster_map.items())
            for i in range(0, len(sorted_rosters), 2):
                r1_id, r1_data = sorted_rosters[i]
                if i + 1 < len(sorted_rosters):
                    r2_id, r2_data = sorted_rosters[i+1]
                else:
                    r2_id, r2_data = ("BYE", {"name": "Bye Week", "wins": 0, "losses": 0})
                
                m_id = (i // 2) + 1
                games[m_id] = [
                    {"roster_id": r1_id, "team_name": r1_data["name"], "record": f"{r1_data['wins']}-{r1_data['losses']}", "starters": ["Roster confirmed - Starters pending"]},
                    {"roster_id": r2_id, "team_name": r2_data["name"], "record": f"{r2_data['wins']}-{r2_data['losses']}", "starters": ["Roster confirmed - Starters pending"]}
                ]

        print(f"Generating weekly forecast predictions for {len(games)} matchups via Groq...")
        final_matchups = []
        for game_id, teams in games.items():
            if len(teams) != 2:
                continue
            t_a, t_b = teams[0], teams[1]

            prompt = f"""You are the lead fantasy football analyst for the 'ONU MLax Dynasty League'. Write an analytical, sharp pregame preview for Week {week}.

Franchises:
- Franchise 1: '{t_a['team_name']}' ({t_a['record']}) | Starters: {', '.join(t_a['starters'])}
- Franchise 2: '{t_b['team_name']}' ({t_b['record']}) | Starters: {', '.join(t_b['starters'])}

MANDATORY EDITORIAL RULES:
1. NEVER write 'Team A', 'Team B', 'Team 1', or 'Team 2'. Always use the actual team names: '{t_a['team_name']}' and '{t_b['team_name']}'.
2. Every player includes their experience tag. Never refer to a player as a rookie unless explicitly marked 'Rookie'.
3. Projections: Assign realistic, distinct scores between 94.0 and 136.0 reflecting roster ceiling. Win probabilities must sum to 100%. Avoid generic duplicate scorelines.
4. Stick strictly to provided NFL team tags. Do not hallucinate real-life team trades or changes.

Output Format:
[SCRATCHPAD]
Confirm actual team names: '{t_a['team_name']}' and '{t_b['team_name']}'.
[END SCRATCHPAD]

PROJ_TEAM_A: [Score for {t_a['team_name']}]
PROJ_TEAM_B: [Score for {t_b['team_name']}]
WINPCT_TEAM_A: [Win % for {t_a['team_name']}]
WINPCT_TEAM_B: [Win % for {t_b['team_name']}]

**🥊 Tale of the Tape:**
[1-2 punchy sentences breaking down the macro roster matchup, using '{t_a['team_name']}' and '{t_b['team_name']}']

**🔥 The X-Factors:**
- {t_a['team_name']}: [Name one primary starter from their lineup and analyze why they drive this team's ceiling]
- {t_b['team_name']}: [Name one primary starter from their lineup and analyze why they drive this team's ceiling]

**🔮 The Verdict:**
[{t_a['team_name']} or {t_b['team_name']}] defeats [{t_b['team_name']} or {t_a['team_name']}], [Projected Score]-[Projected Score], driven by [1 decisive tactical reason]."""

            raw_ai = call_ai(prompt)
            proj_a, proj_b, win_a, win_b, clean_preview = parse_ai_forecast(raw_ai, t_a['team_name'], t_b['team_name'])

            t_a["projected"] = proj_a
            t_a["win_prob"] = win_a
            t_b["projected"] = proj_b
            t_b["win_prob"] = win_b

            final_matchups.append({
                "matchup_id": game_id,
                "team_a": t_a,
                "team_b": t_b,
                "preview": clean_preview
            })
            time.sleep(1)

        os.makedirs("data", exist_ok=True)
        with open("data/matchups.json", "w") as f:
            json.dump({"week": week, "matchups": final_matchups}, f, indent=2)

        # --- PART 2: TRADES ---
        executed_trades = []
        for w in range(max(1, week - 1), week + 1):
            try:
                tx_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/transactions/{w}", timeout=15)
                tx_data = tx_res.json() if tx_res.status_code == 200 else []
                if not isinstance(tx_data, list):
                    continue
            except Exception:
                continue

            for tx in tx_data:
                if tx.get("type") == "trade" and tx.get("status") == "complete":
                    tx_id = tx.get("transaction_id")
                    if tx_id in existing_trades_map:
                        executed_trades.append(existing_trades_map[tx_id])
                        continue

                    r_ids = tx.get("roster_ids", [])
                    if len(r_ids) != 2:
                        continue

                    r1, r2 = r_ids[0], r_ids[1]
                    t1_name = roster_map.get(r1, {}).get("name", f"Team {r1}")
                    t2_name = roster_map.get(r2, {}).get("name", f"Team {r2}")

                    adds = tx.get("adds") or {}
                    t1_receives = [(players.get(str(p_id)) or {}).get("full_name") or str(p_id) for p_id, r_dest in adds.items() if r_dest == r1]
                    t2_receives = [(players.get(str(p_id)) or {}).get("full_name") or str(p_id) for p_id, r_dest in adds.items() if r_dest == r2]

                    for pick in tx.get("draft_picks", []):
                        pick_desc = f"{pick.get('season')} Round {pick.get('round')}"
                        if pick.get("owner_id") == r1:
                            t1_receives.append(pick_desc)
                        elif pick.get("owner_id") == r2:
                            t2_receives.append(pick_desc)

                    trade_prompt = f"""You are the commissioner of the 'ONU MLax Dynasty League'.
Audit this trade:
Franchise 1: '{t1_name}' receives {', '.join(t1_receives) or 'Nothing'}
Franchise 2: '{t2_name}' receives {', '.join(t2_receives) or 'Nothing'}

Format output exactly as:
GRADE_{t1_name}: [Grade]
GRADE_{t2_name}: [Grade]
WINNER: [Winner Team Name]
ANALYSIS:
[1 short paragraph evaluation]"""

                    audit_text = call_ai(trade_prompt) or f"GRADE_{t1_name}: B\nGRADE_{t2_name}: B\nWINNER: Even Trade\nANALYSIS:\nEvaluation pending."
                    executed_trades.append({
                        "transaction_id": tx_id,
                        "week": w,
                        "team_1": {"name": t1_name, "receives": t1_receives},
                        "team_2": {"name": t2_name, "receives": t2_receives},
                        "audit": audit_text
                    })
                    time.sleep(1)

        with open("data/trades.json", "w") as f:
            json.dump({"total_trades": len(executed_trades), "trades": executed_trades}, f, indent=2)
        print("Pipeline execution complete.")

    except Exception as e:
        print(f"CRITICAL PIPELINE FAILURE: {str(e)}")
        raise

if __name__ == "__main__":
    run()
