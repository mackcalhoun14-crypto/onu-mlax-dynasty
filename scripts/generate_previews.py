import os
import json
import re
import time
import requests
import xml.etree.ElementTree as ET

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
                "content": "You are a ruthless, elite analytical fantasy football commissioner and sharp tactician. Reject surface-level filler, hypothetical injury worries for healthy players, and past-tense/offseason news.\nSTRICT ABSOLUTE RULES:\n1) POSITIONAL INTEGRITY: Never assign receiving target shares, slot stats, or pass-catching metrics to Quarterbacks (QBs throw passes; they do not command target shares). Never assign absurd wide-receiver slot metrics (e.g., 55% slot share) to Running Backs.\n2) TEAM ACCURACY: NEVER state that an NFL player is playing against their own real-life NFL team. Verify actual player real-life teams from their roster tags and match them strictly against opposing defenses.\n3) ACTIVE STARTERS ONLY: Ignore all bench injuries. Only discuss injuries if an active starting lineup player has a verified, current-week active designation (questionable, doubtful, IR) in the news feed. Disregard past-tense, historical, or offseason recovery blurbs entirely.\n4) NO CONFLATION: Never connect players who share a last name. Focus strictly on rostered players."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.20
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
                time.sleep(10)
            else:
                time.sleep(3)
        except Exception:
            time.sleep(3)
    return None

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
        starters.append(f"{p_name} ({p_pos}, {p_team})")
    return starters if starters else ["Starters pending"]

def format_bench(roster_player_ids, starter_ids, players):
    bench = []
    starter_set = set(str(sid) for sid in starter_ids)
    for pid in roster_player_ids:
        if str(pid) in starter_set or str(pid) == "0": continue
        p = players.get(str(pid)) or {}
        p_name = p.get("full_name") or str(pid)
        p_pos = p.get("position") or "FLEX"
        p_team = p.get("team") or "FA"
        bench.append(f"{p_name} ({p_pos}, {p_team})")
    return bench if bench else ["No bench players listed"]

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
    return preview_body

def run():
    try:
        print(f"0. Using League ID: {LEAGUE_ID}")
        state_res = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15)
        state = state_res.json() if state_res.status_code == 200 else {}
        week = state.get("week", 1)
        season = state.get("season", "2026")
        season_type = state.get("season_type", "regular")
        if week < 1: week = 1

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

        print("3. Fetching Master News Pool from 5 RSS Feeds...")
        feed_urls = [
            "https://www.rotowire.com/rss/news.php?sport=NFL",
            "https://www.espn.com/espn/rss/nfl/news",
            "https://profootballtalk.nbcsports.com/feed/",
            "https://sports.yahoo.com/nfl/rss",
            "https://www.sbnation.com/rss/nfl/index.xml"
        ]
        
        global_news = []
        for url in feed_urls:
            try:
                rss_res = requests.get(url, timeout=10)
                if rss_res.status_code == 200:
                    root = ET.fromstring(rss_res.content)
                    for item in root.findall('.//item'):
                        title = item.find('title').text if item.find('title') is not None else ""
                        desc = item.find('description').text if item.find('description') is not None else ""
                        clean_desc = re.sub(r'<[^>]+>', '', desc)
                        entry = f"{title}: {clean_desc}"
                        if entry not in global_news:
                            global_news.append(entry)
            except Exception as e:
                print(f"Skipping feed {url} due to error: {e}")

        user_map = {u["user_id"]: (u.get("metadata", {}) or {}).get("team_name") or u.get("display_name") for u in users}
        roster_map = {}
        power_standings = []
        
        if isinstance(rosters, list) and len(rosters) > 0:
            for r in rosters:
                r_id = r["roster_id"]
                name = user_map.get(r["owner_id"], f"Team {r_id}")
                settings = r.get("settings", {}) or {}
                wins = settings.get("wins", 0)
                losses = settings.get("losses", 0)
                ties = settings.get("ties", 0)
                fpts = (settings.get("fpts", 0) or 0) + ((settings.get("fpts_decimal", 0) or 0) / 100.0)
                
                roster_map[r_id] = {
                    "name": name, 
                    "wins": wins, 
                    "losses": losses, 
                    "fpts": round(fpts, 1),
                    "players": r.get("players", [])
                }
                
                power_score = round((wins * 2) + (fpts / 20.0), 1)
                power_standings.append({
                    "roster_id": r_id,
                    "name": name,
                    "record": f"{wins}-{losses}" + (f"-{ties}" if ties > 0 else ""),
                    "fpts": round(fpts, 1),
                    "power_score": power_score
                })

        power_standings.sort(key=lambda x: x["power_score"], reverse=True)
        for idx, team in enumerate(power_standings):
            team["playoff_status"] = "In The Hunt" if idx < 6 else "Chasing"
            team["magic_number"] = max(0, 9 - int(team["record"].split("-")[0]))

        os.makedirs("data", exist_ok=True)
        with open("data/standings.json", "w") as f:
            json.dump({"week": week, "standings": power_standings}, f, indent=2)

        # --- MATCHUPS ---
        games = {}
        if matchups:
            for m in matchups:
                m_id = m.get("matchup_id")
                if not m_id: continue
                if m_id not in games: games[m_id] = []
                starter_ids = m.get("starters", [])
                starters = format_starters(starter_ids, players)

                r_data = roster_map.get(m["roster_id"], {})
                t_name = r_data.get("name", f"Team {m['roster_id']}")
                r_players = r_data.get("players", [])
                bench = format_bench(r_players, starter_ids, players)

                games[m_id].append({
                    "roster_id": m["roster_id"],
                    "team_name": t_name,
                    "record": f"{r_data.get('wins', 0)}-{r_data.get('losses', 0)}",
                    "fpts": r_data.get("fpts", 0.0),
                    "starters": starters,
                    "bench": bench,
                    "starter_ids": starter_ids,
                    "roster_player_ids": r_players
                })

        final_matchups = []
        for game_id, teams in games.items():
            if len(teams) != 2: continue
            t_a, t_b = teams[0], teams[1]
            t_a["projected"] = calculate_team_projection(t_a["starter_ids"], projections)
            t_b["projected"] = calculate_team_projection(t_b["starter_ids"], projections)
            pct_a, pct_b = get_win_prob(t_a["projected"], t_b["projected"])
            t_a["win_prob"] = f"{pct_a}%"
            t_b["win_prob"] = f"{pct_b}%"

            all_matchup_pids = t_a["roster_player_ids"] + t_b["roster_player_ids"]
            matchup_news = []
            
            for pid in all_matchup_pids:
                p_obj = players.get(str(pid), {})
                p_full_name = p_obj.get("full_name", "")
                if not p_full_name: continue
                
                for news in global_news:
                    if p_full_name.lower() in news.lower() and news not in matchup_news:
                        matchup_news.append(f"[{p_full_name}]: {news}")

            news_block = ""
            if matchup_news:
                news_block = "\n[VERIFIED CURRENT-WEEK STARTER HEALTH & INJURY NEWS]:\n" + "\n".join([f"- {n}" for n in matchup_news[:6]]) + "\n"

            prompt = f"""You are an elite analytical fantasy football commissioner for the 'ONU MLax Dynasty League'. Write a high-level, uncompromising Week {week} tactical preview.

Franchise A: '{t_a['team_name']}'
- Record: {t_a['record']} | Total Season FPts: {t_a['fpts']} | Week Proj: {t_a['projected']} pts
- Starting Lineup: {', '.join(t_a['starters'])}
- Dynasty Bench Options: {', '.join(t_a['bench'])}

Franchise B: '{t_b['team_name']}'
- Record: {t_b['record']} | Total Season FPts: {t_b['fpts']} | Week Proj: {t_b['projected']} pts
- Starting Lineup: {', '.join(t_b['starters'])}
- Dynasty Bench Options: {', '.join(t_b['bench'])}

{news_block}

CRITICAL ANALYTICAL RULES:
1. TACTICAL METRICS: Evaluate leverage based on true positional roles: high-value touch volume (red-zone usage), target share concentration for pass-catchers, passing volume/efficiency for QBs, structural defensive weaknesses, and game-script efficiency. 
2. STRICT POSITIONAL REALITY: Ensure QBs are discussed as passers/scramblers (never receiving target shares), and RBs/WRs are discussed in their proper offensive roles. 
3. ACTIVE STARTERS & CLEAN ROSTERS: Ignore bench injuries. Only discuss injuries if an active starter has a verified current-week game designation in the news block above. If a starter is healthy, focus entirely on structural matchup dynamics—do not invent speculative injury worries.
4. TEAM ALIGNMENT CHECK: Verify player real-life team abbreviations from the starter lists. Never state a player is playing against their own real-life team.
5. NAMES: Always use '{t_a['team_name']}' and '{t_b['team_name']}'.

Format Output Exactly As:
**🥊 Tale of the Tape:**
[1-2 sharp sentences analyzing structural projection gaps and competitive landscape]

**🔥 The X-Factors:**
- {t_a['team_name']}: [Deliver a tactical breakdown of a core starter's matchup advantage, target/touch volume, or verified starter injury status]
- {t_b['team_name']}: [Deliver a tactical breakdown of a core starter's matchup advantage, target/touch volume, or verified starter injury status]

**🔮 The Verdict:**
[Winner] defeats [Loser], {t_a['projected']} to {t_b['projected']}, driven by [1 elite tactical or efficiency-driven reason]."""

            raw_ai = call_ai(prompt)
            clean_preview = parse_ai_forecast(raw_ai, t_a['team_name'], t_b['team_name'])
            final_matchups.append({"matchup_id": game_id, "team_a": t_a, "team_b": t_b, "preview": clean_preview})
            time.sleep(1)

        with open("data/matchups.json", "w") as f:
            json.dump({"week": week, "matchups": final_matchups}, f, indent=2)
        print("Pipeline execution complete.")

    except Exception as e:
        print(f"CRITICAL PIPELINE FAILURE: {str(e)}")
        raise

if __name__ == "__main__":
    run()
