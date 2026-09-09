import os
import json
import requests

LEAGUE_ID = "1312162066798231552"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

def run():
    if not GEMINI_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    week = state.get("week", 1)

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

    # Uses the exact endpoint and header authentication from your cURL quickstart
    gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_KEY.strip()
    }
    final_matchups = []

    for game_id, teams in games.items():
        if len(teams) != 2:
            continue
        t_a, t_b = teams[0], teams[1]
        print(f"Generating preview for {t_a['team_name']} vs {t_b['team_name']}...")

        prompt = f"""You are the sharp, witty commissioner of the 'ONU MLax Dynasty League'.
Write a concise, high-energy 2-paragraph matchup preview for Week {week}.

Matchup:
- {t_a['team_name']} (Record: {t_a['record']})
  Starters: {', '.join(t_a['starters'][:7])}
- {t_b['team_name']} (Record: {t_b['record']})
  Starters: {', '.join(t_b['starters'][:7])}

Requirements:
1. Paragraph 1: Break down the primary positional clash (e.g. ground volume vs. perimeter air attack).
2. Paragraph 2: Name one volatile flex/X-Factor player on each roster and predict the winning team with a projected score.
Tone: Sharp, analytical dynasty analyst. No corporate fluff."""

        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(gemini_url, headers=headers, json=payload)
        
        ai_text = "Preview pending final lineup locks."
        if resp.status_code == 200:
            try:
                ai_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                pass
        else:
            print(f"Gemini API Error [{resp.status_code}]: {resp.text}")

        final_matchups.append({
            "matchup_id": game_id,
            "team_a": t_a,
            "team_b": t_b,
            "preview": ai_text
        })

    os.makedirs("data", exist_ok=True)
    with open("data/matchups.json", "w") as f:
        json.dump({"week": week, "matchups": final_matchups}, f, indent=2)

if __name__ == "__main__":
    run()
