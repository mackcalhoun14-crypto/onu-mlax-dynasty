import os
import json
import re
import time
import requests
import asyncio
import edge_tts
import xml.etree.ElementTree as ET

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

def generate_voiceover(text, output_path):
    # Strip markdown headers/emojis so the TTS reads cleanly
    clean_text = re.sub(r'[\*#_🥊🔥🔮🤖]', '', text)
    clean_text = clean_text.replace("Tale of the Tape:", "").replace("The X-Factors:", "").replace("The Verdict:", "")

    # Deep, professional broadcaster voice from Azure Neural
    voice = "en-US-ChristopherNeural" 
    
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        asyncio.run(edge_tts.Communicate(clean_text, voice).save(output_path))
        return True
    except Exception as e:
        print(f"TTS Generation failed: {e}")
        return False

def calculate_team_projection(starter_ids, projections):
    total = 0.0
    for pid in starter_ids:
        if str(pid) == "0": continue
        p_data = projections.get(str(pid), {})
        
        if "stats" in p_data:
            pts = p_data["stats"].get("pts_half_ppr") or p_data["stats"].get("pts_ppr") or p_data["stats"].get("pts_std") or 0.0
        else:
            pts = p_data.get("pts_half_ppr") or p_data.get("pts_ppr") or p_data.get("pts_std") or 0.0
            
        total += float(pts)
        
    if total == 0.0 and starter_ids:
        total = 110.0 + (len(starter_ids) * 0.1)
    return round(total, 1)

def get_win_prob(proj_a, proj_b):
    if proj_a == 0 and proj_b == 0:
        return 50, 50
    prob_a = 1 / (1 + 10 ** ((proj_b - proj_a) / 33))
    pct_a = round(prob_a * 100)
    pct_b = 100 - pct_a
    return pct_a, pct_b

def format_starters(starter_ids, players):
    starters = []
    for p_id in starter_ids:
        if str(p_id) == "0": continue
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
    return starters if starters else ["Roster building phase - Starters pending"]

def parse_ai_forecast(ai_text, team_a_name, team_b_name):
    if not ai_text:
        return f"Matchup preview for {team_a_name} vs {team_b_name} pending lineup confirmation."

    try:
        preview_start = ai_text.index("**🥊 Tale of the Tape:**")
        preview_body = ai_text[preview_start:].strip()
    except ValueError:
        preview_body = ai_text.strip()

    preview_body = re.sub(r'\bTeam\s+A\b', team_a_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+B\b', team_b_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+1\b', team_a_name, preview_body, flags=re.IGNORECASE)
    preview_body = re.sub(r'\bTeam\s+2\b', team_b_name, preview_body, flags=re.IGNORECASE)
        
    return preview_body

def run():
    try:
        print(f"0. Using League ID: {LEAGUE_ID}")
        print("1. Fetching Sleeper NFL State...")
        state_res = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
        state = state_res.json() if state_res.status_code == 200 else {}
        week = state.get("week", 1)
        season = state.get("season", "2026")
        season_type = state.get("season_type", "regular")
        if week < 1: week = 1

        print("2. Fetching League Data & Official Sleeper Projections...")
        users_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users", timeout=15)
        rosters_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters", timeout=15)
        
        matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}", timeout=15)
        matchups = matchups_res.json() if matchups_res.status_code == 200 else []
        if not matchups and week > 1:
            week = 1
            matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}", timeout=15)
            matchups = matchups_res.json() if matchups_res.status_code == 200 else []

        players_res = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=30)
        projections_res = requests.get(f"https://api.sleeper.app/v1/projections/nfl/{season_type}/{season}/{week}", timeout=30)

        users = users_res.json() if users_res.status_code == 200 else []
        rosters = rosters_res.json() if rosters_res.status_code == 200 else []
        players = players_res.json() if players_res.status_code == 200 else {}
        projections = projections_res.json() if projections_res.status_code == 200 else {}

        if not isinstance(matchups, list): matchups = []

        print("3. Fetching Latest NFL News via RSS...")
        global_news = []
        try:
            rss_res = requests.get("https://www.espn.com/espn/rss/nfl/news", timeout=10)
            if rss_res.status_code == 200:
                root = ET.fromstring(rss_res.content)
                for item in root.findall('./channel/item'):
                    title = item.find('title').text if item.find('title') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    desc_clean = re.sub(r'<[^>]+>', '', desc)
                    global_news.append(f"{title}: {desc_clean}")
        except Exception as e:
            print(f"Failed to fetch RSS feed: {e}")

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
            except Exception: pass

        # --- PART 1: MATCHUPS ---
        games = {}
        
        if matchups and isinstance(matchups, list) and len(matchups) > 0:
            for m in matchups:
                m_id = m.get("matchup_id")
                if not m_id: continue
                if m_id not in games: games[m_id] = []

                starter_ids = m.get("starters", [])
                starters = format_starters(starter_ids, players)

                t_info = roster_map.get(m["roster_id"], {"name": f"Team {m['roster_id']}", "wins": 0, "losses": 0})
                games[m_id].append({
                    "roster_id": m["roster_id"],
                    "team_name": t_info["name"],
                    "record": f"{t_info['wins']}-{t_info['losses']}",
                    "starters": starters,
                    "starter_ids": starter_ids
                })

        if not games and roster_map:
            sorted_rosters = list(roster_map.items())
            for i in range(0, len(sorted_rosters), 2):
                r1_id, r1_data = sorted_rosters[i]
                r1_roster = next((r for r in rosters if r["roster_id"] == r1_id), {})
                r1_starter_ids = r1_roster.get("starters", [])
                r1_starters = format_starters(r1_starter_ids, players)

                if i + 1 < len(sorted_rosters):
                    r2_id, r2_data = sorted_rosters[i+1]
                    r2_roster = next((r for r in rosters if r["roster_id"] == r2_id), {})
                    r2_starter_ids = r2_roster.get("starters", [])
                    r2_starters = format_starters(r2_starter_ids, players)
                else:
                    r2_id, r2_data = ("BYE", {"name": "Bye Week", "wins": 0, "losses": 0})
                    r2_starter_ids = []
                    r2_starters = ["BYE"]
                
                m_id = (i // 2) + 1
                games[m_id] = [
                    {"roster_id": r1_id, "team_name": r1_data["name"], "record": f"{r1_data['wins']}-{r1_data['losses']}", "starters": r1_starters, "starter_ids": r1_starter_ids},
                    {"roster_id": r2_id, "team_name": r2_data["name"], "record": f"{r2_data['wins']}-{r2_data['losses']}", "starters": r2_starters, "starter_ids": r2_starter_ids}
                ]

        print(f"Generating weekly forecast predictions and audio for {len(games)} matchups...")
        final_matchups = []
        for game_id, teams in games.items():
            if len(teams) != 2: continue
            t_a, t_b = teams[0], teams[1]

            t_a["projected"] = calculate_team_projection(t_a["starter_ids"], projections)
            t_b["projected"] = calculate_team_projection(t_b["starter_ids"], projections)
            pct_a, pct_b = get_win_prob(t_a["projected"], t_b["projected"])
            t_a["win_prob"] = f"{pct_a}%"
            t_b["win_prob"] = f"{pct_b}%"

            # Parse relevant news for this specific matchup
            matchup_news = []
            for starter in t_a['starters'] + t_b['starters']:
                player_name = starter.split(" (")[0].strip()
                for news_item in global_news:
                    if player_name in news_item and news_item not in matchup_news:
                        matchup_news.append(news_item)
            
            news_context = ""
            if matchup_news:
                news_context = "\n[LATEST NEWS ALERTS FOR ACTIVE STARTERS]\n" + "\n".join([f"- {n}" for n in matchup_news]) + "\n"

            prompt = f"""You are the lead fantasy football analyst for the 'ONU MLax Dynasty League'. Write an analytical, sharp pregame preview for Week {week}.

Franchises:
- Franchise 1: '{t_a['team_name']}' ({t_a['record']}) | Proj: {t_a['projected']} pts | Starters: {', '.join(t_a['starters'])}
- Franchise 2: '{t_b['team_name']}' ({t_b['record']}) | Proj: {t_b['projected']} pts | Starters: {', '.join(t_b['starters'])}
{news_context}
MANDATORY EDITORIAL RULES:
1. NEVER write 'Team A', 'Team B', 'Team 1', or 'Team 2'. Always use the actual team names: '{t_a['team_name']}' and '{t_b['team_name']}'.
2. Every player includes their experience tag. Never refer to a player as a rookie unless explicitly marked 'Rookie'.
3. Do not invent your own projected scores; reference the {t_a['projected']} and {t_b['projected']} projected points provided above.
4. Stick strictly to provided NFL team tags. Do not hallucinate real-life team trades or changes.
5. If [LATEST NEWS ALERTS] are provided, explicitly reference how those injuries, rumors, or game-time decisions impact the game script.

Output Format:
[SCRATCHPAD]
Confirm actual team names: '{t_a['team_name']}' and '{t_b['team_name']}'.
[END SCRATCHPAD]

**🥊 Tale of the Tape:**
[1-2 punchy sentences breaking down the macro roster matchup, using '{t_a['team_name']}' and '{t_b['team_name']}']

**🔥 The X-Factors:**
- {t_a['team_name']}: [Name one primary starter from their lineup and analyze why they drive this team's ceiling, factoring in any news alerts]
- {t_b['team_name']}: [Name one primary starter from their lineup and analyze why they drive this team's ceiling, factoring in any news alerts]

**🔮 The Verdict:**
[{t_a['team_name']} or {t_b['team_name']}] defeats [{t_b['team_name']} or {t_a['team_name']}], {t_a['projected']} to {t_b['projected']} (or vice versa), driven by [1 decisive tactical reason]."""

            raw_ai = call_ai(prompt)
            clean_preview = parse_ai_forecast(raw_ai, t_a['team_name'], t_b['team_name'])

            # Generate the MP3
            audio_rel_path = f"data/audio/matchup_{game_id}.mp3"
            audio_success = generate_voiceover(clean_preview, audio_rel_path)

            final_matchups.append({
                "matchup_id": game_id,
                "team_a": t_a,
                "team_b": t_b,
                "preview": clean_preview,
                "audio_url": audio_rel_path if audio_success else None
            })
            time.sleep(1)

        os.makedirs("data", exist_ok=True)
        with open("data/matchups.json", "w") as f:
            json.dump({"week": week, "matchups": final_matchups}, f, indent=2)

        # --- PART 2: TRADES ---
        # (Trade processing logic remains unchanged)
        executed_trades = []
        for w in range(max(1, week - 1), week + 1):
            try:
                tx_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/transactions/{w}", timeout=15)
                tx_data = tx_res.json() if tx_res.status_code == 200 else []
                if not isinstance(tx_data, list): continue
            except Exception: continue

            for tx in tx_data:
                if tx.get("type") == "trade" and tx.get("status") == "complete":
                    tx_id = tx.get("transaction_id")
                    if tx_id in existing_trades_map:
                        executed_trades.append(existing_trades_map[tx_id])
                        continue

                    r_ids = tx.get("roster_ids", [])
                    if len(r_ids) != 2: continue

                    r1, r2 = r_ids[0], r_ids[1]
                    t1_name = roster_map.get(r1, {}).get("name", f"Team {r1}")
                    t2_name = roster_map.get(r2, {}).get("name", f"Team {r2}")

                    adds = tx.get("adds") or {}
                    t1_receives = [(players.get(str(p_id)) or {}).get("full_name") or str(p_id) for p_id, r_dest in adds.items() if r_dest == r1]
                    t2_receives = [(players.get(str(p_id)) or {}).get("full_name") or str(p_id) for p_id, r_dest in adds.items() if r_dest == r2]

                    for pick in tx.get("draft_picks", []):
                        pick_desc = f"{pick.get('season')} Round {pick.get('round')}"
                        if pick.get("owner_id") == r1: t1_receives.append(pick_desc)
                        elif pick.get("owner_id") == r2: t2_receives.append(pick_desc)

                    trade_prompt = f"""You are the commissioner of the 'ONU MLax Dynasty League'. Audit this trade:
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
