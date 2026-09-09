import os
import json
import time
import requests

LEAGUE_ID = os.environ.get("SLEEPER_LEAGUE_ID", "1312162066798231552")
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
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }

    for attempt in range(3):
        try:
            print(f"Calling Groq API (Attempt {attempt + 1})...")
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            print(f"Groq API Response Status: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "").strip()
            elif resp.status_code == 429:
                print("Rate limited (429). Pausing 10s...")
                time.sleep(10)
            else:
                print(f"Groq API Error [{resp.status_code}]: {resp.text}")
        except Exception as e:
            print(f"Request exception encountered: {e}")
            time.sleep(3)
    return None

def run():
    print("1. Fetching Sleeper NFL State...")
    state_res = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
    state = state_res.json() if state_res.status_code == 200 else {}
    week = state.get("week", 1)
    print(f"Current active week: {week}")

    print("2. Fetching League Users, Rosters, Matchups, and Players...")
    users_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users", timeout=15)
    rosters_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters", timeout=15)
    matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}", timeout=15)
    players_res = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=30)

    users = users_res.json() if users_res.status_code == 200 else []
    rosters = rosters_res.json() if rosters_res.status_code == 200 else []
    matchups = matchups_res.json() if matchups_res.status_code == 200 else []
    players = players_res.json() if players_res.status_code == 200 else {}

    if not isinstance(matchups, list):
        print(f"Warning: Matchups endpoint returned non-list data: {matchups}")
        matchups = []

    # Fixed syntax here: closed with ')' instead of ']'
    user_map = {u["user_id"]: (u.get("metadata", {}) or {}).get("team_name") or u.get("display_name") for u in users}
    roster_map = {}
    for r in rosters:
        roster_map[r["roster_id"]] = {
            "name": user_map.get(r["owner_id"], f"Team {r['roster_id']}"),
            "wins": (r.get("settings", {}) or {}).get("wins", 0),
            "losses": (r.get("settings", {}) or {}).get("losses", 0)
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
            starters.append(f"{p_name} ({p_pos})")

        t_info = roster_map.get(m["roster_id"], {"name": f"Team {m['roster_id']}", "wins": 0, "losses": 0})
        games[m_id].append({
            "roster_id": m["roster_id"],
            "team_name": t_info["name"],
            "record": f"{t_info['wins']}-{t_info['losses']}",
            "points": m.get("points", 0),
            "starters": starters
        })

    print(f"Found {len(games)} head-to-head matchups. Generating unique AI previews via Groq...")
    final_matchups = []
    for game_id, teams in games.items():
        if len(teams) != 2:
            continue
        t_a, t_b = teams[0], teams[1]
        print(f"Processing Matchup #{game_id}: {t_a['team_name']} vs {t_b['team_name']}")

        prompt = f"""You are the sharp commissioner of the 'ONU MLax Dynasty League'.
Write a concise, high-energy 2-paragraph matchup preview for Week {week} featuring these exact teams.

Matchup:
- Team 1: {t_a['team_name']} (Record: {t_a['record']})
  Starters: {', '.join(t_a['starters'][:7]) if t_a['starters'] else 'None set'}
- Team 2: {t_b['team_name']} (Record: {t_b['record']})
  Starters: {', '.join(t_b['starters'][:7]) if t_b['starters'] else 'None set'}

Requirements:
1. Paragraph 1: Analyze the specific positional advantages and starter firepower for each side based on their listed players.
2. Paragraph 2: Highlight a key player matchup or volatility factor and predict who takes home the win.
Tone: Sharp, analytical fantasy analyst. No corporate fluff. Make it distinct and tailored to these exact teams."""

        ai_text = call_ai(prompt) or f"Matchup breakdown for {t_a['team_name']} vs {t_b['team_name']} pending lineup confirmation."
        
        final_matchups.append({
            "matchup_id": game_id,
            "team_a": t_a,
            "team_b": t_b,
            "preview": ai_text
        })
        time.sleep(2)

    os.makedirs("data", exist_ok=True)
    with open("data/matchups.json", "w") as f:
        json.dump({"week": week, "matchups": final_matchups}, f, indent=2)
    print("Successfully wrote fresh data/matchups.json")

    # --- PART 2: TRADES ---
    print("Auditing completed trades...")
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
                    print(f"Skipping re-audit for existing Trade ID: {tx_id}")
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

                print(f"Auditing New Trade ID: {tx_id} ({t1_name} <-> {t2_name})")
                trade_prompt = f"""You are the sharp commissioner of the 'ONU MLax Dynasty League'.
Audit this trade executed in Week {w}:

Team 1: {t1_name}
Receives: {', '.join(t1_receives) or 'Nothing'}

Team 2: {t2_name}
Receives: {', '.join(t2_receives) or 'Nothing'}

Context: 12-team dynasty league moving to Superflex in 2027.
Task:
1. Assign a letter grade (A+ to F) for each team.
2. Declare the winner of the trade.
3. Write a 2-paragraph audit covering immediate lineup ceiling and multi-year dynasty impact.

Format output exactly as:
GRADE_{t1_name}: [Grade]
GRADE_{t2_name}: [Grade]
WINNER: [Winner Team Name]
ANALYSIS:
[Your 2-paragraph audit]"""

                audit_text = call_ai(trade_prompt) or f"GRADE_{t1_name}: B\nGRADE_{t2_name}: B\nWINNER: Even Trade\nANALYSIS:\nTrade evaluation pending."
                executed_trades.append({
                    "transaction_id": tx_id,
                    "week": w,
                    "team_1": {"name": t1_name, "receives": t1_receives},
                    "team_2": {"name": t2_name, "receives": t2_receives},
                    "audit": audit_text
                })
                time.sleep(2)

    with open("data/trades.json", "w") as f:
        json.dump({"total_trades": len(executed_trades), "trades": executed_trades}, f, indent=2)
    print("Successfully wrote data/trades.json")
    print("Pipeline script execution complete.")

if __name__ == "__main__":
    run()
