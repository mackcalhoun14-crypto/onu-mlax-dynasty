import os
import json
import time
import requests

LEAGUE_ID = os.environ.get("SLEEPER_LEAGUE_ID", "1312162066798231552")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def call_gemini(prompt, headers, url, safety_settings):
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": safety_settings
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            elif resp.status_code == 429:
                print(f"Rate limited (429). Pausing 6s...")
                time.sleep(6)
            else:
                print(f"Gemini API Error [{resp.status_code}]: {resp.text}")
        except Exception as e:
            print(f"Gemini request exception: {e}")
            time.sleep(3)
    return None

def run():
    if not GEMINI_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    print("Fetching Sleeper NFL State...")
    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    week = state.get("week", 1)

    print(f"Fetching League Data for Week {week}...")
    users = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users").json()
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters").json()
    matchups = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}").json()
    players = requests.get("https://api.sleeper.app/v1/players/nfl").json()

    user_map = {u["user_id"]: u.get("metadata", {}).get("team_name") or u.get("display_name") for u in users}
    roster_map = {}
    for r in rosters:
        roster_map[r["roster_id"]] = {
            "name": user_map.get(r["owner_id"], f"Team {r['roster_id']}"),
            "wins": r.get("settings", {}).get("wins", 0),
            "losses": r.get("settings", {}).get("losses", 0)
        }

    gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY.strip()}
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
    ]

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
            p = players.get(str(p_id), {})
            starters.append(f"{p.get('full_name', 'Player')} ({p.get('position', 'FLEX')})")

        t_info = roster_map.get(m["roster_id"], {"name": f"Team {m['roster_id']}", "wins": 0, "losses": 0})
        games[m_id].append({
            "roster_id": m["roster_id"],
            "team_name": t_info["name"],
            "record": f"{t_info['wins']}-{t_info['losses']}",
            "points": m.get("points", 0),
            "starters": starters
        })

    final_matchups = []
    for game_id, teams in games.items():
        if len(teams) != 2:
            continue
        t_a, t_b = teams[0], teams[1]
        print(f"Generating preview for #{game_id}: {t_a['team_name']} vs {t_b['team_name']}...")

        prompt = f"""You are the sharp, witty commissioner of the 'ONU MLax Dynasty League'.
Write a concise, high-energy 2-paragraph matchup preview for Week {week}.

Matchup:
- {t_a['team_name']} (Record: {t_a['record']})
  Starters: {', '.join(t_a['starters'][:7])}
- {t_b['team_name']} (Record: {t_b['record']})
  Starters: {', '.join(t_b['starters'][:7])}

Requirements:
1. Paragraph 1: Break down the primary positional battle (e.g. backfield volume vs. perimeter air attack).
2. Paragraph 2: Name one volatile flex/X-Factor player on each squad and predict the winner with a final score.
Tone: Sharp, analytical dynasty analyst. No corporate filler."""

        ai_text = call_gemini(prompt, headers, gemini_url, safety_settings) or "Preview pending final lineup locks."
        final_matchups.append({
            "matchup_id": game_id,
            "team_a": t_a,
            "team_b": t_b,
            "preview": ai_text
        })
        time.sleep(3)

    os.makedirs("data", exist_ok=True)
    with open("data/matchups.json", "w") as f:
        json.dump({"week": week, "matchups": final_matchups}, f, indent=2)

    # --- PART 2: IN-SEASON TRADES ---
    print("Auditing in-season trades across completed weeks...")
    executed_trades = []
    for w in range(1, week + 1):
        tx_data = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/transactions/{w}").json()
        for tx in tx_data:
            if tx.get("type") == "trade" and tx.get("status") == "complete":
                tx_id = tx.get("transaction_id")
                r_ids = tx.get("roster_ids", [])
                if len(r_ids) != 2:
                    continue

                r1, r2 = r_ids[0], r_ids[1]
                t1_name = roster_map.get(r1, {}).get("name", f"Team {r1}")
                t2_name = roster_map.get(r2, {}).get("name", f"Team {r2}")

                # Calculate what each team received
                adds = tx.get("adds") or {}
                t1_receives = [players.get(str(p_id), {}).get("full_name", p_id) for p_id, r_dest in adds.items() if r_dest == r1]
                t2_receives = [players.get(str(p_id), {}).get("full_name", p_id) for p_id, r_dest in adds.items() if r_dest == r2]

                for pick in tx.get("draft_picks", []):
                    pick_desc = f"{pick.get('season')} Round {pick.get('round')}"
                    if pick.get("owner_id") == r1:
                        t1_receives.append(pick_desc)
                    elif pick.get("owner_id") == r2:
                        t2_receives.append(pick_desc)

                print(f"Auditing trade: {t1_name} <-> {t2_name}...")
                trade_prompt = f"""You are the sharp, analytical commissioner of the 'ONU MLax Dynasty League'.
Audit this in-season trade executed in Week {w}:

Team 1: {t1_name}
Receives: {', '.join(t1_receives) if t1_receives else 'Nothing'}

Team 2: {t2_name}
Receives: {', '.join(t2_receives) if t2_receives else 'Nothing'}

Context: 12-team dynasty fantasy football league shifting to Superflex in 2027.
Task:
1. Assign a letter grade (A+ to F) for each team.
2. Declare the winner of the trade.
3. Provide a sharp, 2-paragraph audit breaking down the win-now vs. long-term dynasty value for both sides.

Format output as:
GRADE_{t1_name}: [Grade]
GRADE_{t2_name}: [Grade]
WINNER: [Winner Team Name]
ANALYSIS:
[Your 2-paragraph audit]"""

                audit_text = call_gemini(trade_prompt, headers, gemini_url, safety_settings) or "Trade audit pending commissioner review."

                executed_trades.append({
                    "transaction_id": tx_id,
                    "week": w,
                    "team_1": {"name": t1_name, "receives": t1_receives},
                    "team_2": {"name": t2_name, "receives": t2_receives},
                    "audit": audit_text
                })
                time.sleep(3)

    with open("data/trades.json", "w") as f:
        json.dump({"total_trades": len(executed_trades), "trades": executed_trades}, f, indent=2)

    print("Pipeline execution complete.")

if __name__ == "__main__":
    run()
