import os
import json
import time
import re
import requests

LEAGUE_ID = "1312162066798231552"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def run():
    if not GEMINI_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    print("Fetching Sleeper state...")
    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    week = state.get("week", 1)

    print(f"Fetching Week {week} matchups and rosters...")
    users = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users").json()
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters").json()
    matchups = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}").json()
    players = requests.get("https://api.sleeper.app/v1/players/nfl").json()

    user_map = {}
    for u in users:
        team_name = u.get("metadata", {}).get("team_name") or u.get("display_name")
        user_map[u["user_id"]] = team_name

    roster_map = {}
    for r in rosters:
        roster_map[r["roster_id"]] = {
            "name": user_map.get(r["owner_id"], f"Team {r['roster_id']}"),
            "wins": r.get("settings", {}).get("wins", 0),
            "losses": r.get("settings", {}).get("losses", 0)
        }

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

        team_info = roster_map.get(m["roster_id"], {"name": f"Team {m['roster_id']}", "wins": 0, "losses": 0})
        games[m_id].append({
            "roster_id": m["roster_id"],
            "team_name": team_info["name"],
            "record": f"{team_info['wins']}-{team_info['losses']}",
            "points": m.get("points", 0),
            "starters": starters
        })

    gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_KEY.strip()
    }

    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
    ]

    final_matchups = []

    for game_id, teams in games.items():
        if len(teams) != 2:
            continue
        t_a, t_b = teams[0], teams[1]
        print(f"Generating punchy JSON preview for Matchup #{game_id}: {t_a['team_name']} vs {t_b['team_name']}...")

        prompt = f"""You are the sharp, brutally concise commissioner of the 'ONU MLax Dynasty League'.
Analyze this Week {week} fantasy matchup and respond ONLY with a raw JSON object (no markdown, no extra commentary).

Matchup:
- Team A: {t_a['team_name']} (Record: {t_a['record']})
  Starters: {', '.join(t_a['starters'][:7])}
- Team B: {t_b['team_name']} (Record: {t_b['record']})
  Starters: {', '.join(t_b['starters'][:7])}

Respond with this exact JSON structure:
{{
  "headline": "A punchy 3 to 6 word title for this clash",
  "clash": "Maximum 2 sentences explaining the core positional or roster mismatch.",
  "team_a_xfactor": "Player Name: Exactly 1 sentence on why they make or break Team A.",
  "team_b_xfactor": "Player Name: Exactly 1 sentence on why they make or break Team B.",
  "team_a_proj": 128.5,
  "team_b_proj": 119.2,
  "favorite": "Name of favored team",
  "spread": "-9.3",
  "verdict": "Exactly 1 punchline sentence predicting how and why the winner seals the game."
}}"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "safetySettings": safety_settings
        }

        preview_data = {
            "headline": "Head-to-Head Clash",
            "clash": "Lineups are locked in for a classic divisional brawl.",
            "team_a_xfactor": "Key Starter: High-upside weekly flex.",
            "team_b_xfactor": "Key Starter: High-upside weekly flex.",
            "team_a_proj": 120.0,
            "team_b_proj": 120.0,
            "favorite": t_a['team_name'],
            "spread": "EVEN",
            "verdict": "This matchup will be decided in the fourth quarter on Sunday."
        }

        for attempt in range(3):
            try:
                resp = requests.post(gemini_url, headers=headers, json=payload, timeout=20)
                if resp.status_code == 200:
                    raw_text = resp.json().get("candidates", [])[0]["content"]["parts"][0]["text"].strip()
                    # Strip any accidental ```json code blocks
                    cleaned = re.sub(r"^```(?:json)?\n?", "", raw_text, flags=re.IGNORECASE)
                    cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                    parsed = json.loads(cleaned)
                    preview_data = parsed
                    break
                elif resp.status_code == 429:
                    print(f"Rate limit 429. Waiting 5s (attempt {attempt+1})...")
                    time.sleep(5)
                else:
                    print(f"Error {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"Exception during parse: {e}")
                time.sleep(3)

        final_matchups.append({
            "matchup_id": game_id,
            "team_a": t_a,
            "team_b": t_b,
            "preview_data": preview_data
        })

        time.sleep(3)

    os.makedirs("data", exist_ok=True)
    with open("data/matchups.json", "w") as f:
        json.dump({"week": week, "matchups": final_matchups}, f, indent=2)

    print("Success: Generated punchy matchup artifacts.")

if __name__ == "__main__":
    run()
