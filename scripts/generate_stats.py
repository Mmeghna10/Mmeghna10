"""Generate self-hosted GitHub stats cards (SVG) for the profile README.

No third-party services, no dependencies: reads the GitHub GraphQL API with a
token and writes three SVG files into ./assets:

    assets/stats.svg          headline numbers + contributions ring
    assets/languages.svg      top languages across your own repos
    assets/contributions.svg  last-year contribution heatmap + streaks

Usage (locally):   GH_TOKEN=xxxx python scripts/generate_stats.py
In GitHub Actions: see .github/workflows/stats.yml
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

USERNAME = os.environ.get("GH_USERNAME", "Mmeghna10")
DISPLAY_NAME = os.environ.get("GH_DISPLAY_NAME", "Meghna")
OUT_DIR = Path(__file__).resolve().parent.parent / "assets"

# Languages that are mostly markup/config and would drown out real code.
EXCLUDED_LANGUAGES = {"HTML", "CSS", "SCSS", "Procfile", "Dockerfile", "Shell", "Batchfile", "PowerShell"}
TOP_LANGUAGES = 6

# ---- theme (matches the README header) ----
BG = "#0B1220"
BORDER = "#1E293B"
TITLE = "#60A5FA"
TEXT = "#E2E8F0"
MUTED = "#94A3B8"
ACCENT = "#2563EB"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Arial, sans-serif"
HEAT = ["#16213A", "#1E3A8A", "#1D4ED8", "#3B82F6", "#93C5FD"]


# --------------------------------------------------------------------------- data

def graphql(query: str, variables: dict) -> dict:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN (or GITHUB_TOKEN) to a GitHub token.")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-stats-generator"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]


MAIN_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $cursor) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""


def collect() -> dict:
    repos, cursor, user = [], None, None
    while True:
        user = graphql(MAIN_QUERY, {"login": USERNAME, "cursor": cursor})["user"]
        page = user["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    # All-time commits: the API only allows one-year windows, so walk the years.
    commits = 0
    start = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    while start < now:
        end = min(start + dt.timedelta(days=365), now)
        cc = graphql(YEAR_QUERY, {"login": USERNAME, "from": start.isoformat(), "to": end.isoformat()})
        commits += cc["user"]["contributionsCollection"]["totalCommitContributions"]
        start = end

    langs: dict[str, dict] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name in EXCLUDED_LANGUAGES:
                continue
            entry = langs.setdefault(name, {"size": 0, "color": edge["node"]["color"] or MUTED})
            entry["size"] += edge["size"]

    days = [d for w in user["contributionsCollection"]["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    return {
        "stars": sum(r["stargazerCount"] for r in repos),
        "repos": user["repositories"]["totalCount"],
        "commits": commits,
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "contributed_to": user["contributionsCollection"]["totalRepositoriesWithContributedCommits"],
        "year_total": user["contributionsCollection"]["contributionCalendar"]["totalContributions"],
        "weeks": user["contributionsCollection"]["contributionCalendar"]["weeks"],
        "days": days,
        "languages": langs,
    }


def streaks(days: list[dict]) -> tuple[int, int]:
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] > 0 else 0
        longest = max(longest, run)
    # Current streak: today may not have a contribution yet, so allow it to be empty.
    current = 0
    tail = days[:-1] if days and days[-1]["contributionCount"] == 0 else days
    for d in reversed(tail):
        if d["contributionCount"] == 0:
            break
        current += 1
    return current, longest


# --------------------------------------------------------------------------- svg helpers

def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def frame(width: int, height: int, title: str, body: str, label: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">
<title>{esc(label)}</title>
<style>
  text {{ font-family: {FONT}; }}
  .title {{ font-size: 18px; font-weight: 600; fill: {TITLE}; }}
  .label {{ font-size: 14px; fill: {MUTED}; }}
  .value {{ font-size: 14px; font-weight: 700; fill: {TEXT}; }}
  .small {{ font-size: 11px; fill: {MUTED}; }}
  .fade {{ animation: fade .6s ease-out; }}
  @keyframes fade {{ from {{ opacity: 0; }} }}
  @keyframes grow {{ from {{ stroke-dashoffset: var(--len); }} }}
  @media (prefers-reduced-motion: reduce) {{ .fade {{ animation: none; }} }}
</style>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>
<text x="25" y="38" class="title">{esc(title)}</text>
{body}
</svg>
"""


# Octicon-style 16px icons (paths from GitHub's open-source Octicons, MIT).
ICONS = {
    "star": "M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Z",
    "commit": "M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 0-1.5h3.32a4.002 4.002 0 0 1 7.86 0h3.32a.75.75 0 0 1 0 1.5Zm-1.43-.75a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z",
    "pr": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z",
    "issue": "M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z",
    "repo": "M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z",
    "people": "M2 5.5a3.5 3.5 0 1 1 5.898 2.549 5.508 5.508 0 0 1 3.034 4.084.75.75 0 1 1-1.482.235 4 4 0 0 0-7.9 0 .75.75 0 0 1-1.482-.236A5.507 5.507 0 0 1 3.102 8.05 3.493 3.493 0 0 1 2 5.5ZM11 4a3.001 3.001 0 0 1 2.22 5.018 5.01 5.01 0 0 1 2.56 3.012.749.749 0 0 1-.885.954.752.752 0 0 1-.549-.514 3.507 3.507 0 0 0-2.522-2.372.75.75 0 0 1-.574-.73v-.352a.75.75 0 0 1 .416-.672A1.5 1.5 0 0 0 11 5.5.75.75 0 0 1 11 4Zm-5.5-.5a2 2 0 1 0-.001 3.999A2 2 0 0 0 5.5 3.5Z",
}


# --------------------------------------------------------------------------- cards

def stats_card(d: dict) -> str:
    rows = [
        ("star", "Total stars earned", d["stars"]),
        ("commit", "Total commits", d["commits"]),
        ("pr", "Pull requests", d["prs"]),
        ("issue", "Issues opened", d["issues"]),
        ("repo", "Repositories", d["repos"]),
        ("people", "Contributed to (last year)", d["contributed_to"]),
        ("people", "Followers", d["followers"]),
    ]
    # Show the five strongest non-zero numbers (a row of zeros looks worse than no row).
    rows = [r for r in rows if r[2] > 0][:5]
    body = []
    for i, (icon, label, value) in enumerate(rows):
        y = 70 + i * 25
        body.append(
            f'<g class="fade" style="animation-delay:{150 + i * 120}ms" transform="translate(25,{y})">'
            f'<path transform="translate(0,-12) scale(0.95)" fill="{TITLE}" d="{ICONS[icon]}"/>'
            f'<text x="26" y="0" class="label">{esc(label)}</text>'
            f'<text x="255" y="0" class="value" text-anchor="end">{esc(fmt(value))}</text></g>'
        )
    # contributions ring
    r, cx, cy = 46, 380, 102
    length = 2 * 3.14159 * r
    body.append(
        f'<g class="fade" style="animation-delay:200ms">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BORDER}" stroke-width="8"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{ACCENT}" stroke-width="8" stroke-linecap="round" '
        f'stroke-dasharray="{length:.1f}" stroke-dashoffset="0" transform="rotate(-90 {cx} {cy})" '
        f'style="--len:{length:.1f};animation:grow 1.2s ease-out"/>'
        f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" style="font-size:24px;font-weight:700;fill:{TEXT}">{esc(fmt(d["year_total"]))}</text>'
        f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" class="small">contributions</text>'
        f'<text x="{cx}" y="{cy + 70}" text-anchor="middle" class="small">in the last year</text></g>'
    )
    return frame(495, 195, f"{DISPLAY_NAME}'s GitHub Stats", "\n".join(body), f"{DISPLAY_NAME}'s GitHub stats")


def languages_card(d: dict) -> str:
    ranked = sorted(d["languages"].items(), key=lambda kv: kv[1]["size"], reverse=True)
    grand = sum(v["size"] for _, v in ranked) or 1
    # Drop languages under 1% so the card is not cluttered with stray files.
    langs = [kv for kv in ranked if kv[1]["size"] / grand >= 0.01][:TOP_LANGUAGES]
    total = sum(v["size"] for _, v in langs) or 1
    width, bar_w = 350, 300
    body = [f'<clipPath id="bar"><rect x="25" y="58" width="{bar_w}" height="10" rx="5"/></clipPath><g clip-path="url(#bar)">']
    x = 25.0
    for name, v in langs:
        w = bar_w * v["size"] / total
        body.append(f'<rect x="{x:.2f}" y="58" width="{w + 0.5:.2f}" height="10" fill="{v["color"]}"/>')
        x += w
    body.append("</g>")
    for i, (name, v) in enumerate(langs):
        col, row = i % 2, i // 2
        gx, gy = 25 + col * 160, 98 + row * 28
        pct = 100 * v["size"] / total
        body.append(
            f'<g class="fade" style="animation-delay:{150 + i * 120}ms" transform="translate({gx},{gy})">'
            f'<circle cx="5" cy="-5" r="5" fill="{v["color"]}"/>'
            f'<text x="16" y="0" class="value" style="font-weight:600">{esc(name)}</text>'
            f'<text x="135" y="0" class="label" text-anchor="end">{pct:.1f}%</text></g>'
        )
    if not langs:
        body.append(f'<text x="25" y="100" class="label">No language data yet</text>')
    return frame(width, 195, "Most Used Languages", "\n".join(body), "Most used languages")


def contributions_card(d: dict) -> str:
    weeks = d["weeks"]
    cell, gap = 11, 3
    step = cell + gap
    left, top = 40, 78
    width = left + len(weeks) * step + 25
    counts = sorted(c["contributionCount"] for c in d["days"] if c["contributionCount"] > 0)

    def level(n: int) -> int:
        if n == 0 or not counts:
            return 0
        for i, q in enumerate((0.25, 0.5, 0.75), start=1):
            if n <= counts[int(q * (len(counts) - 1))]:
                return i
        return 4

    current, longest = streaks(d["days"])
    body = [
        f'<text x="{width - 25}" y="38" text-anchor="end" class="label">'
        f'<tspan class="value">{esc(d["year_total"])}</tspan> contributions · '
        f'current streak <tspan class="value">{current}</tspan> · '
        f'longest <tspan class="value">{longest}</tspan> days</text>'
    ]
    last_month = None
    for wi, week in enumerate(weeks):
        x = left + wi * step
        first = dt.date.fromisoformat(week["contributionDays"][0]["date"])
        if first.month != last_month and first.day <= 7 and wi < len(weeks) - 2:
            body.append(f'<text x="{x}" y="{top - 8}" class="small">{first.strftime("%b")}</text>')
            last_month = first.month
        for day in week["contributionDays"]:
            date = dt.date.fromisoformat(day["date"])
            y = top + ((date.weekday() + 1) % 7) * step  # Sunday first, like GitHub
            n = day["contributionCount"]
            body.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{HEAT[level(n)]}">'
                f'<title>{n} contribution{"s" if n != 1 else ""} on {date:%b %d, %Y}</title></rect>'
            )
    for label, row in (("Mon", 1), ("Wed", 3), ("Fri", 5)):
        body.append(f'<text x="12" y="{top + row * step + 9}" class="small">{label}</text>')
    ly = top + 7 * step + 18
    lx = width - 25 - (len(HEAT) * step) - 70
    body.append(f'<text x="{lx}" y="{ly + 9}" class="small">Less</text>')
    for i, c in enumerate(HEAT):
        body.append(f'<rect x="{lx + 32 + i * step}" y="{ly}" width="{cell}" height="{cell}" rx="2" fill="{c}"/>')
    body.append(f'<text x="{lx + 36 + len(HEAT) * step}" y="{ly + 9}" class="small">More</text>')
    height = ly + 30
    return frame(width, height, "Contribution Activity", "\n".join(body), "Contribution activity in the last year")


# --------------------------------------------------------------------------- leetcode

LEETCODE_USERNAME = os.environ.get("LEETCODE_USERNAME", "Mmeghna10")
LC_COLORS = {"Easy": "#1CBBBA", "Medium": "#FFB700", "Hard": "#F63737"}

LEETCODE_QUERY = """
query($u: String!) {
  allQuestionsCount { difficulty count }
  matchedUser(username: $u) {
    submitStatsGlobal { acSubmissionNum { difficulty count } }
    badges { displayName }
    languageProblemCount { languageName problemsSolved }
    userCalendar { streak totalActiveDays }
  }
  userContestRanking(username: $u) { attendedContestsCount rating topPercentage }
}
"""


def collect_leetcode() -> dict | None:
    """Public LeetCode data. Returns None (and keeps the old card) if LeetCode is unreachable."""
    try:
        req = urllib.request.Request(
            "https://leetcode.com/graphql",
            data=json.dumps({"query": LEETCODE_QUERY, "variables": {"u": LEETCODE_USERNAME}}).encode(),
            headers={
                "Content-Type": "application/json",
                "Referer": f"https://leetcode.com/u/{LEETCODE_USERNAME}/",
                "User-Agent": "Mozilla/5.0 (profile-stats-generator)",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)["data"]
        user = data["matchedUser"]
        if not user:
            raise ValueError(f"LeetCode user {LEETCODE_USERNAME!r} not found")
    except Exception as exc:  # network blocks, schema changes, rate limits
        print(f"warning: skipped LeetCode card ({exc})")
        return None
    solved = {r["difficulty"]: r["count"] for r in user["submitStatsGlobal"]["acSubmissionNum"]}
    totals = {r["difficulty"]: r["count"] for r in data["allQuestionsCount"]}
    langs = sorted(user["languageProblemCount"], key=lambda l: l["problemsSolved"], reverse=True)
    return {
        "solved": solved,
        "totals": totals,
        "badges": len(user["badges"]),
        "streak": user["userCalendar"]["streak"],
        "active_days": user["userCalendar"]["totalActiveDays"],
        "language": langs[0] if langs else None,
        "contest": data["userContestRanking"],
    }


def leetcode_card(d: dict) -> str:
    width, height = 760, 200
    total = d["solved"].get("All", 0)
    body = []
    # ring split by difficulty mix
    r, cx, cy = 54, 100, 118
    circ = 2 * 3.14159 * r
    body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BORDER}" stroke-width="9"/>')
    offset = 0.0
    for i, level in enumerate(("Easy", "Medium", "Hard")):
        part = circ * d["solved"].get(level, 0) / (total or 1)
        if part <= 0:
            continue
        body.append(
            f'<circle class="fade" style="animation-delay:{200 + i * 150}ms" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{LC_COLORS[level]}" stroke-width="9" stroke-dasharray="{max(part - 3, 0.1):.1f} {circ:.1f}" '
            f'stroke-dashoffset="{-offset:.1f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += part
    body.append(
        f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" style="font-size:30px;font-weight:700;fill:{TEXT}">{total}</text>'
        f'<text x="{cx}" y="{cy + 24}" text-anchor="middle" class="small">solved</text>'
    )
    # difficulty bars
    top_count = max([d["solved"].get(l, 0) for l in LC_COLORS] + [1])
    for i, (level, color) in enumerate(LC_COLORS.items()):
        y = 82 + i * 38
        n, t = d["solved"].get(level, 0), d["totals"].get(level, 0)
        w = 200 * n / top_count
        body.append(
            f'<g class="fade" style="animation-delay:{150 + i * 120}ms" transform="translate(190,{y})">'
            f'<text x="0" y="0" class="label" style="fill:{color};font-weight:600">{level}</text>'
            f'<text x="200" y="0" class="value" text-anchor="end">{n}<tspan class="small"> / {t}</tspan></text>'
            f'<rect x="0" y="8" width="200" height="6" rx="3" fill="{BORDER}"/>'
            f'<rect x="0" y="8" width="{w:.1f}" height="6" rx="3" fill="{color}"/></g>'
        )
    # side facts
    facts = []
    if d["contest"] and d["contest"].get("attendedContestsCount"):
        c = d["contest"]
        facts.append(("Contest rating", f'{round(c["rating"])}  (top {c["topPercentage"]:.1f}%)'))
    if d["language"]:
        facts.append(("Main language", f'{d["language"]["languageName"]} · {d["language"]["problemsSolved"]}'))
    facts += [
        ("Max streak", f'{d["streak"]} days'),
        ("Active days", str(d["active_days"])),
        ("Badges", str(d["badges"])),
    ]
    for i, (label, value) in enumerate(facts[:4]):
        y = 82 + i * 30
        body.append(
            f'<g class="fade" style="animation-delay:{300 + i * 120}ms" transform="translate(460,{y})">'
            f'<text x="0" y="0" class="label">{esc(label)}</text>'
            f'<text x="275" y="0" class="value" text-anchor="end">{esc(value)}</text></g>'
        )
    body.append(f'<line x1="430" y1="62" x2="430" y2="178" stroke="{BORDER}"/>')
    return frame(width, height, f"LeetCode · {LEETCODE_USERNAME}", "\n".join(body), "LeetCode stats")


def main() -> None:
    data = collect()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cards = [
        ("stats.svg", stats_card(data)),
        ("languages.svg", languages_card(data)),
        ("contributions.svg", contributions_card(data)),
    ]
    lc = collect_leetcode()
    if lc:
        cards.append(("leetcode.svg", leetcode_card(lc)))
    for name, svg in cards:
        (OUT_DIR / name).write_text(svg, encoding="utf-8")
        print(f"wrote assets/{name}")


if __name__ == "__main__":
    main()
