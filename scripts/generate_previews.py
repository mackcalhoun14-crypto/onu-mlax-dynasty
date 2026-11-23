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
                "content": (
                    "You are a ruthless, elite analytical fantasy football commissioner and sharp tactician. "
                    "Reject surface-level filler, hypothetical injury worries, and past-tense/offseason news.\n"
                    "ABSOLUTE STRICT RULES:\n"
                    "1) POSITIONAL ACCURACY & ROLES: Quarterbacks (QBs) are passers and scramblers; they NEVER command receiving target shares, slot stats, or pass-catching metrics. Running Backs (RBs) handle backfield touches and carries; they do NOT command wide receiver slot stats or target share metrics.\n"
                    "2) OPPONENT VERIFICATION: NEVER state or imply that an NFL player is playing against their own real-life NFL team. Cross-reference the player's real-life team abbreviation with the opposing defense they are facing. A player on team X can never play against team X.\n"
                    "3) ACTIVE STARTERS ONLY: Ignore bench injuries entirely. Only discuss injuries if an active starting lineup player has a verified, current-week game designation (questionable, doubtful, IR) in the news feed. Disregard all historical or past-tense blurbs.\n"
                    "4) NO CONFLATION: Never connect players who share a last name. Focus strictly on rostered players."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.10
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
        if str(pid) == "0":
            continue
        p_data = projections.get(str(pid), {})
        if "stats" in p_data:
            pts = p_data["stats"].get("pts_half_ppr") or p_data["stats"].get("pts_ppr") or p_data["stats"].get("pts_std") or 0.0
        else:
            pts = p_data.get("pts_half_ppr") or p_data.get("pts_ppr") or p_data.get("pts_std") or 0.0
        total += float(pts)
    if total == 0.0:
        return None
    return round(total, 1)

def get_win_prob(proj_a, proj_b):
    if proj_a is None or proj_b is None:
        return None, None
    prob_a = 1 / (1 + 10 ** ((proj_b - proj_a) / 33))
    pct_a = round(prob_a * 100)
    pct_b = 100 - pct_a
    return pct_a, pct_b

def format_starters(starter_ids, players):
    starters = []
    for p_id in starter_ids:
        if str(p_id) == "0":
            continue
        p = players.get(str(p_id))
        if not p:
            continue
        starters.append(f"{p.get('full_name', '?')} ({p.get('position', '?')}, {p.get('team', '?')})")
    return starters

def format_roster_players(roster_ids, players):
    names = []
    for p_id in roster_ids:
        if str(p_id) == "0":
            continue
        p = players.get(str(p_id))
        if not p:
            continue
        names.append(p.get("full_name", "?"))
    return names

def parse_rss_news(rss_content):
    try:
        root = ET.fromstring(rss_content)
        news_items = []
        for item in root.findall(".//item")[:15]:
            title = item.findtext("title", "")
            desc = item.findtext("description", "")
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            desc = desc.split("...")[0][:200]
            if title and desc:
                news_items.append({"title": title.strip(), "summary": desc})
        return news_items
    except Exception as e:
        print(f"RSS Parsing Error: {e}")
        return []

def fetch_news():
    try:
        url = "https://www.espn.com/espn/rss/nfl/news"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(resp.content)
        news_items = []
        for item in root.findall(".//item")[:15]:
            title = item.findtext("title", "")
            desc = item.findtext("description", "")
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            desc = desc.split("...")[0][:200]
            if title and desc:
                news_items.append({"title": title.strip(), "summary": desc})
        return news_items
    except Exception as e:
        print(f"News feed error: {e}")
        return []

# ---------------------------------------------------------------------------
# TRADE AUDIT DESK (completed trades only)
#
# Fetches completed Sleeper trade transactions for weeks 1..current week,
# generates a strict-format AI audit for each one, validates the audit
# before writing, and writes data/trades.json for trades.html. A trade audit
# is never allowed to invent a player, use a numeric grade, or say the
# evaluation is pending: invalid AI output is retried, then replaced by a
# deterministic factual fallback built only from the assets that moved.
# ---------------------------------------------------------------------------

VALID_TRADE_GRADES = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]
_TRADE_GRADE_RANK = {g: i for i, g in enumerate(VALID_TRADE_GRADES)}


def _trade_pick_label(pick):
    return f"{pick.get('season')} Round {pick.get('round')}"


def _trade_is_pick_label(asset):
    return isinstance(asset, str) and len(asset) >= 4 and asset[:4].isdigit() and " Round " in asset


def _trade_receives(txn, roster_id, players):
    """Assets a roster received in a trade: player names, then pick labels."""
    received = []
    adds = txn.get("adds") or {}
    for pid, dest in adds.items():
        if dest == roster_id:
            p = players.get(str(pid)) or {}
            received.append(p.get("full_name") or str(pid))
    for pick in txn.get("draft_picks") or []:
        if pick.get("owner_id") == roster_id:
            received.append(_trade_pick_label(pick))
    return received


def _trade_fallback_audit(team1_name, team1_assets, team2_name, team2_assets):
    """Deterministic factual audit, used only when AI output fails validation.

    Grades come from a simple asset score (players 5 pts; picks R1=4, R2=3,
    R3=2, R4+=1). The analysis only restates the assets each side received,
    so it can never invent a player or a pick.
    """
    def score(assets):
        total = 0.0
        for asset in assets:
            if _trade_is_pick_label(asset):
                try:
                    rnd = int(asset.split(" Round ")[1])
                except Exception:
                    rnd = 4
                total += {1: 4.0, 2: 3.0, 3: 2.0}.get(rnd, 1.0)
            else:
                total += 5.0
        return total

    s1 = score(team1_assets)
    s2 = score(team2_assets)
    diff = abs(s1 - s2)
    if diff < 0.6:
        g1 = g2 = "B"
        winner = "Even Trade"
    elif diff < 3.0:
        g1, g2 = ("B+", "B-") if s1 > s2 else ("B-", "B+")
        winner = team1_name if s1 > s2 else team2_name
    elif diff < 6.0:
        g1, g2 = ("A-", "C+") if s1 > s2 else ("C+", "A-")
        winner = team1_name if s1 > s2 else team2_name
    else:
        g1, g2 = ("A", "C") if s1 > s2 else ("C", "A")
        winner = team1_name if s1 > s2 else team2_name

    def describe(name, assets):
        if not assets:
            return f"{name} received no players or picks in this trade."
        return f"{name} received {', '.join(assets)}."

    if winner == "Even Trade":
        verdict = "On the assets exchanged, the deal is broadly even."
    else:
        verdict = f"On the assets exchanged, {winner} comes out ahead."
    analysis = f"{describe(team1_name, team1_assets)} {describe(team2_name, team2_assets)} {verdict}"
    return (f"GRADE_{team1_name}: {g1}\n"
            f"GRADE_{team2_name}: {g2}\n"
            f"WINNER: {winner}\n"
            f"ANALYSIS:\n{analysis}")


def parse_trade_audit(audit, team1_name, team2_name, traded_player_names, all_player_names, player_facts=None):
    """Parse and strictly validate a trade audit.

    Required shape (no extra lines):
        GRADE_<team 1 name>: <letter grade A+..F>
        GRADE_<team 2 name>: <letter grade A+..F>
        WINNER: <team 1 name | team 2 name | Even Trade>
        ANALYSIS:
        <2-3 sentences mentioning only traded players/picks>
    The winner must hold the better grade (or grades must be equal for
    Even Trade), the text must contain no "pending" placeholder, and the
    analysis must not name any player who was not part of the trade. It must
    also not name a player at a wrong position, call a non-rookie a
    rookie, state a wrong age, or use unsupported hype: such claims are
    checked against the Sleeper facts for the traded players.
    Returns (grade1, grade2, winner, analysis) or None when invalid.
    """
    if not audit or not isinstance(audit, str):
        return None
    if "pending" in audit.lower():
        return None
    lines = [ln.strip() for ln in audit.strip().split("\n") if ln.strip() != ""]
    if len(lines) < 4:
        return None
    m1 = re.match(r"^GRADE_" + re.escape(team1_name) + r":\s*([A-F][+-]?)$", lines[0])
    m2 = re.match(r"^GRADE_" + re.escape(team2_name) + r":\s*([A-F][+-]?)$", lines[1])
    if not m1 or not m2:
        return None
    g1, g2 = m1.group(1), m2.group(1)
    if g1 not in _TRADE_GRADE_RANK or g2 not in _TRADE_GRADE_RANK:
        return None
    wm = re.match(r"^WINNER:\s*(.+)$", lines[2])
    if not wm:
        return None
    winner = wm.group(1).strip()
    if winner not in (team1_name, team2_name, "Even Trade"):
        return None
    r1, r2 = _TRADE_GRADE_RANK[g1], _TRADE_GRADE_RANK[g2]
    if winner == team1_name and not r1 < r2:
        return None
    if winner == team2_name and not r2 < r1:
        return None
    if winner == "Even Trade" and r1 != r2:
        return None
    if not lines[3].startswith("ANALYSIS:"):
        return None
    analysis = (lines[3][len("ANALYSIS:"):].strip() + "\n" + "\n".join(lines[4:])).strip()
    if not analysis or "GRADE_" in analysis or "pending" in analysis.lower() or any(p in analysis.lower() for p in ("significant long-term upside", "emerged as a primary rusher", "clear asset", "superior piece", "franchise quarterback", "elite", "clear winner", "team control", "contract year")):
        return None
    # Positions, ages, and experience are checked against the Sleeper
    # facts for the traded players. A wrong position, a "rookie" who is
    # not one, or an age no traded player has rejects the audit.
    if ("rookie" in analysis.lower() and 0 not in {f.get("years_exp") for f in (player_facts or {}).values() if isinstance(f.get("years_exp"), int)}) or [pos for pos, words in {"QB": ["quarterback", "passer"], "RB": ["running back", "tailback", "halfback", "rusher"], "WR": ["wide receiver", "wideout", "receiver"], "TE": ["tight end"], "K": ["kicker"]}.items() if pos not in {f.get("position") for f in (player_facts or {}).values() if f.get("position")} and any(w in analysis.lower() for w in words)]:
        return None
    flat = analysis.lower().replace("\u2011", "-").replace("\u2010", "-")
    if [int(a) for a in re.findall(r"\b(\d{1,2})(?:-year-old| years? old)\b", flat) if int(a) not in {f.get("age") for f in (player_facts or {}).values() if isinstance(f.get("age"), int)}]:
        return None
    # No invented players: every full player name in the analysis must be a
    # player who actually changed teams in this trade.
    for name in all_player_names:
        if name and name in analysis and name not in traded_player_names:
            return None
    return g1, g2, winner, analysis


def generate_trade_audit(team1_name, team1_assets, team2_name, team2_assets, traded_player_names, all_player_names, player_facts=None):
    """Generate one validated trade audit (AI with retries, factual fallback)."""
    assets1 = ", ".join(team1_assets) if team1_assets else "nothing"
    assets2 = ", ".join(team2_assets) if team2_assets else "nothing"
    prompt = (
        "You are an elite analytical fantasy football commissioner grading a completed dynasty trade "
        "in the ONU MLax Dynasty League. Grade ONLY the assets listed below. "
        "Do not mention any player or draft pick that is not listed below.\n\n"
        f"{team1_name} received: {assets1}\n"
        f"{team2_name} received: {assets2}\n\n"
        "Respond with EXACTLY these lines and nothing else:\n"
        f"GRADE_{team1_name}: <letter grade>\n"
        f"GRADE_{team2_name}: <letter grade>\n"
        f"WINNER: <{team1_name} | {team2_name} | Even Trade>\n"
        "ANALYSIS:\n"
        "[2-3 sentences. Describe only the listed players and picks and judge which side got more dynasty value.]\n\n"
        "STRICT RULES:\n"
        "- Letter grades only: A+, A, A-, B+, B, B-, C+, C, C-, D+, D, D-, F. Never use numbers.\n"
        "- Each grade line must be formatted exactly as shown, with NO space before the colon.\n"
        "- The WINNER must be the team with the better (higher) grade. Use Even Trade only when the grades are equal.\n"
        "- Never write \"Evaluation pending\", \"pending review\", or any placeholder. Always give real grades.\n"
        "- Never invent players or picks. A side that received only draft picks received only draft picks.\n"
        "- The current NFL season is 2026. Do not call a player a rookie unless he entered the NFL in 2026. "
        f"A 2027 draft pick is about one year away, a 2028 pick about two years away, and a 2029 pick about three years away; never describe a future pick as four years away. Player facts from Sleeper (ground truth JSON; use ONLY these facts for each player): {json.dumps(player_facts or {})} Note: 'years_exp' counts completed NFL seasons. A player with years_exp 0 is a rookie in the 2026 season; years_exp 2 means he is in his 3rd season. GROUND RULES: Describe each player ONLY with the Sleeper facts in this prompt (position, NFL team, age, completed NFL seasons). Do not assign a role or depth-chart status (starter, backup, depth, primary, rotational, franchise, proven starter). Do not state contract years, years of team control, free-agency timing, stats, rankings, target shares, or snap counts. Do not use hype: no 'significant long-term upside', 'emerged as a primary rusher', 'clear asset', 'superior piece', 'franchise quarterback', 'elite', or 'clear winner'. Prefer measured, factual phrasing."
    )
    for _attempt in range(3):
        raw = call_ai(prompt)
        parsed = parse_trade_audit(raw, team1_name, team2_name, traded_player_names, all_player_names, player_facts) if raw else None
        if parsed:
            g1, g2, winner, analysis = parsed
            return (f"GRADE_{team1_name}: {g1}\n"
                    f"GRADE_{team2_name}: {g2}\n"
                    f"WINNER: {winner}\n"
                    f"ANALYSIS:\n{analysis}")
        time.sleep(1)
    print(f"AI audit failed validation for {team1_name} vs {team2_name}; using factual fallback.")
    return _trade_fallback_audit(team1_name, team1_assets, team2_name, team2_assets)


def fetch_completed_trades(league_id, max_week, roster_map, players):
    """Fetch completed Sleeper trade transactions for weeks 1..max_week.

    Returns (trades, all_player_names). Each trade has transaction_id, week,
    team_1/team_2 {name, receives}, plus an internal _traded_player_names set
    used only for audit validation.
    """
    all_player_names = {p.get("full_name") for p in players.values()
                        if isinstance(p, dict) and p.get("full_name")}
    seen = set()
    trades = []
    for wk in range(1, max_week + 1):
        try:
            res = requests.get(
                f"https://api.sleeper.app/v1/league/{league_id}/transactions/{wk}",
                timeout=20,
            )
            txns = res.json() if res.status_code == 200 else []
        except Exception as e:
            print(f"Could not fetch transactions for week {wk}: {e}")
            continue
        if not isinstance(txns, list):
            continue
        for txn in txns:
            if txn.get("type") != "trade" or txn.get("status") != "complete":
                continue
            tid = str(txn.get("transaction_id"))
            if tid in seen:
                continue
            seen.add(tid)
            roster_ids = txn.get("roster_ids") or []
            if len(roster_ids) != 2:
                print(f"Skipping trade {tid}: {len(roster_ids)} teams (only 2-team trades are graded).")
                continue
            r1, r2 = roster_ids[0], roster_ids[1]
            name1 = (roster_map.get(r1, {}).get("name") or f"Team {r1}").strip()
            name2 = (roster_map.get(r2, {}).get("name") or f"Team {r2}").strip()
            assets1 = _trade_receives(txn, r1, players)
            assets2 = _trade_receives(txn, r2, players)
            traded_names = {a for a in assets1 + assets2 if not _trade_is_pick_label(a)}; traded_facts = {p.get("full_name"): {"position": p.get("position"), "team": p.get("team"), "age": p.get("age"), "years_exp": p.get("years_exp")} for p in players.values() if isinstance(p, dict) and p.get("full_name") in traded_names}
            trades.append({
                "transaction_id": tid,
                "week": wk,
                "team_1": {"name": name1, "receives": assets1},
                "team_2": {"name": name2, "receives": assets2},
                "_traded_player_names": traded_names, "_traded_player_facts": traded_facts,
            })
    trades.sort(key=lambda t: (t["week"], t["transaction_id"]))
    return trades, all_player_names


def generate_trade_audits(max_week, roster_map, players):
    """Regenerate data/trades.json from Sleeper transactions for weeks 1..max_week."""
    trades, all_player_names = fetch_completed_trades(LEAGUE_ID, max_week, roster_map, players)
    print(f"Found {len(trades)} completed trades across weeks 1..{max_week}.")
    out_trades = []
    for trade in trades:
        t1 = trade["team_1"]
        t2 = trade["team_2"]
        audit = generate_trade_audit(
            t1["name"], t1["receives"], t2["name"], t2["receives"],
            trade["_traded_player_names"], all_player_names, trade.get("_traded_player_facts", {}),
        )
        time.sleep(1)
        out_trades.append({
            "transaction_id": trade["transaction_id"],
            "week": trade["week"],
            "team_1": t1,
            "team_2": t2,
            "audit": audit,
        })
    # Newest week first so the desk leads with the latest deals.
    out_trades.sort(key=lambda t: (t["week"], t["transaction_id"]), reverse=True)
    os.makedirs("data", exist_ok=True)
    with open("data/trades.json", "w") as f:
        json.dump({"total_trades": len(out_trades), "trades": out_trades}, f, indent=2)
    print(f"Wrote data/trades.json with {len(out_trades)} trades.")

def run():
    state = requests.get("https://api.sleeper.app/v1/state/nfl").json()
    week = state.get("week", 1)
    trade_max_week = week if isinstance(week, int) and week >= 1 else 1
    users_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/users")
    users = users_res.json()
    user_map = {}
    for u in users:
        user_map[u["user_id"]] = {
            "display_name": u.get("display_name", "Unknown"),
            "team_name": u.get("metadata", {}).get("team_name") or u.get("display_name", "Unknown")
        }

    rosters_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/rosters")
    rosters = rosters_res.json()
    roster_map = {}
    for r in rosters:
        u = user_map.get(r["owner_id"], {})
        roster_map[r["roster_id"]] = {
            "name": u.get("team_name", "Unknown"),
            "record": f"{r.get('settings', {}).get('wins', 0)}-{r.get('settings', {}).get('losses', 0)}",
            "fpts": r.get("settings", {}).get("fpts", 0),
            "starters": r.get("starters", []),
            "players": r.get("players", []),
        }

    matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}")
    matchups = matchups_res.json()
    if not matchups and week > 1:
        week = 1
        matchups_res = requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{week}")
        matchups = matchups_res.json()

    players_res = requests.get("https://api.sleeper.app/v1/players/nfl")
    players = players_res.json()
    projections_res = requests.get(f"https://api.sleeper.app/v1/projections/nfl/{state.get('season', 2026)}/{week}")
    projections = projections_res.json()
    if not isinstance(projections, dict):
        projections = {}
    news_items = fetch_news()
    news_block = "\n".join(f"- {item['title']}: {item['summary']}" for item in news_items) if news_items else "No major breaking news this week."
    standings_list = sorted(roster_map.values(), key=lambda x: x["fpts"], reverse=True)

    try:
        os.makedirs("data", exist_ok=True)
        with open("data/standings.json", "w") as f:
            json.dump({"week": week, "standings": standings_list}, f, indent=2)

        results = []
        pairs = {}
        for m in matchups:
            pairs.setdefault(m["matchup_id"], []).append(m)

        for mid, pair in pairs.items():
            if len(pair) != 2:
                continue
            t_a = roster_map.get(pair[0]["roster_id"])
            t_b = roster_map.get(pair[1]["roster_id"])
            if not t_a or not t_b:
                continue

            proj_a = calculate_team_projection(t_a["starters"], projections)
            proj_b = calculate_team_projection(t_b["starters"], projections)
            pct_a, pct_b = get_win_prob(proj_a, proj_b)

            a_starters_str = ", ".join(format_starters(t_a["starters"], players))
            b_starters_str = ", ".join(format_starters(t_b["starters"], players))
            a_roster_str = ", ".join(format_roster_players(t_a["players"], players))
            b_roster_str = ", ".join(format_roster_players(t_b["players"], players))

            prompt = f"""You are an elite fantasy football analyst writing for the "ONU MLax Dynasty League" Week {week} matchup preview. Use the CURRENT WEEK and ROSTERS data below. League: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX, 1 K, 1 DEF. PPR scoring.

{news_block}

Matchup:
{(t_a['name'] + ' (' + t_a['record'] + ') [projected ' + str(proj_a) + ' pts, ' + str(pct_a) + '% win chance] vs ' + t_b['name'] + ' (' + t_b['record'] + ') [projected ' + str(proj_b) + ' pts, ' + str(pct_b) + '% win chance]') if proj_a is not None and proj_b is not None else 'No projected scores are available for this matchup. Do not invent, estimate, or cite a projected score or a win chance.'}

{t_a['name']} starters:
{a_starters_str}
{t_b['name']} starters:
{b_starters_str}
{t_a['name']} roster (bench incl.):
{a_roster_str}
{t_b['name']} roster (bench incl.):
{b_roster_str}

CRITICAL ANALYTICAL RULES:
1. STRICT POSITIONAL ROLES: Discuss QBs strictly as passers and rushers (never assign them target share or pass-catching metrics). RBs handle backfield touches and carries; they do NOT command wide receiver slot stats or target share metrics. WRs are pass-catchers, TEs handle tight end targets.
2. TEAM ALIGNMENT & OPPONENTS: Strictly verify player team abbreviations from the starting lineups and roster tags. Never state a player is playing against their own NFL team. Never discuss players not rostered.
3. ACTIVE STARTERS ONLY: Ignore bench injuries. Only discuss injuries if an active starter has a verified, active current-week game designation (questionable, doubtful, IR) in the news feed. Disregard all historical or past-tense blurbs.
4. NAMES: Always use '{t_a['name']}' and '{t_b['name']}'. 5. ONLY USE SUPPLIED FACTS: Use only the starters, records, and news items above. Never invent a player's stats, ranking, target share, snap count, depth-chart status, or a projected score. If a fact is not supplied, do not state it.

Format Output Exactly As:
**🥊 Tale of the Tape:**
[1-2 sharp sentences on how the two starting lineups compare and where the matchup will be decided]

**🔥 The X-Factors:**
- {t_a['name']}: [A tactical breakdown of a core starter's matchup, using only the starters and records above.]
- {t_b['name']}: [A tactical breakdown of a core starter's matchup, using only the starters and records above.]

**🔮 The Verdict:**
{f'[Winner] defeats [Loser], {proj_a} to {proj_b}, driven by [1 elite tactical or efficiency-based reason].' if proj_a is not None and proj_b is not None else '[Winner] has the edge over [Loser], driven by [1 factual reason drawn only from the starters and records above].'}"""

            analysis = call_ai(prompt) or f"{t_a['name']} ({t_a['record']}) faces {t_b['name']} ({t_b['record']}) in Week {week}. Starters: {a_starters_str} vs {b_starters_str}. No preview text was generated for this matchup."
            time.sleep(1)
            results.append({
                "matchup_id": mid,
                "team_a": {
                    "roster_id": pair[0]["roster_id"],
                    "team_name": t_a["name"],
                    "record": t_a["record"],
                    "projected": proj_a,
                    "win_pct": pct_a,
                    "starters": format_starters(t_a["starters"], players),
                    "bench": format_roster_players(t_a["players"], players),
                },
                "team_b": {
                    "roster_id": pair[1]["roster_id"],
                    "team_name": t_b["name"],
                    "record": t_b["record"],
                    "projected": proj_b,
                    "win_pct": pct_b,
                    "starters": format_starters(t_b["starters"], players),
                    "bench": format_roster_players(t_b["players"], players),
                },
                "analysis": analysis
            })

        with open("data/matchups.json", "w") as f:
            json.dump({"week": week, "matchups": results}, f, indent=2)

    except Exception as e:
        print(f"CRITICAL ERROR IN RUN: {e}")
        raise

    # --- Trade Audit Desk (completed trades only) ---
    # Runs after matchups/standings are saved, and never fails the run.
    try:
        generate_trade_audits(trade_max_week, roster_map, players)
    except Exception as te:
        print(f"Trade audit generation failed (matchups/standings already saved): {te}")

    print("Preview generation complete.")

if __name__ == "__main__":
    run()
