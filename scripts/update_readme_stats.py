import os
import re
import sys
import json
import math
from datetime import datetime, timedelta, timezone
import urllib.request

README_PATH = "README.md"
START = "<!-- GH_STATS_START -->"
END = "<!-- GH_STATS_END -->"

def gh_graphql(query: str, variables: dict) -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN env var")

    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "readme-stats-updater",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]

QUERY = """
query(
  $login: String!,
  $reposFirst: Int!,
  $since30: GitTimestamp!,
  $since90: GitTimestamp!
) {
  user(login: $login) {
    name
    login
    followers { totalCount }
    following { totalCount }

    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { contributionCount }
        }
      }
    }

    repositories(
      privacy: PUBLIC,
      isFork: false,
      first: $reposFirst,
      ownerAffiliations: OWNER,
      orderBy: {field: STARGAZERS, direction: DESC}
    ) {
      totalCount
      nodes {
        name
        url
        stargazerCount
        forkCount
        pushedAt
        isArchived

        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }

        # Heuristics (existence checks)
        dockerfile: object(expression: "HEAD:Dockerfile") { __typename }
        compose: object(expression: "HEAD:docker-compose.yml") { __typename }
        compose2: object(expression: "HEAD:docker-compose.yaml") { __typename }
        workflows: object(expression: "HEAD:.github/workflows") { __typename }

        testsDir: object(expression: "HEAD:tests") { __typename }
        testDir: object(expression: "HEAD:test") { __typename }
        testsUnderscore: object(expression: "HEAD:__tests__") { __typename }
        pytestIni: object(expression: "HEAD:pytest.ini") { __typename }
        toxIni: object(expression: "HEAD:tox.ini") { __typename }
        noseCfg: object(expression: "HEAD:nosetests.cfg") { __typename }
        jestCfg: object(expression: "HEAD:jest.config.js") { __typename }
        vitestCfg: object(expression: "HEAD:vitest.config.ts") { __typename }
        vitestCfg2: object(expression: "HEAD:vitest.config.js") { __typename }

        defaultBranchRef {
          name
          target {
            __typename
            ... on Commit {
              history { totalCount }
              history30: history(since: $since30) { totalCount }
              history90: history(since: $since90) { totalCount }
            }
          }
        }
      }
    }
  }
}
"""

def _exists(obj_field: dict) -> bool:
    # GraphQL returns null if not found; if found, __typename present.
    return bool(obj_field) and "__typename" in obj_field

def pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0%"
    return f"{(part / whole) * 100:.0f}%"

def human_dt(s: str) -> str:
    # ISO string -> YYYY-MM-DD
    if not s:
        return "n/a"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return s

def format_block(d: dict) -> str:
    u = d["user"]
    repos = [r for r in u["repositories"]["nodes"] if not r.get("isArchived")]

    total_repos = u["repositories"]["totalCount"]
    followers = u["followers"]["totalCount"]
    following = u["following"]["totalCount"]

    # Base totals
    total_stars = sum(r["stargazerCount"] for r in repos)
    total_forks = sum(r["forkCount"] for r in repos)

    # “Starred repos” count
    starred_repos = sum(1 for r in repos if r["stargazerCount"] > 0)

    # Commits totals (default branch only)
    commits_all = 0
    commits_30 = 0
    commits_90 = 0
    repos_with_default_branch = 0

    for r in repos:
        db = r.get("defaultBranchRef")
        if not db:
            continue
        tgt = db.get("target") or {}
        if tgt.get("__typename") != "Commit":
            continue
        repos_with_default_branch += 1
        commits_all += (tgt.get("history") or {}).get("totalCount", 0)
        commits_30 += (tgt.get("history30") or {}).get("totalCount", 0)
        commits_90 += (tgt.get("history90") or {}).get("totalCount", 0)

    avg_stars = (total_stars / len(repos)) if repos else 0.0

    # Repo signals
    docker_repos = 0
    ci_repos = 0
    tests_repos = 0

    for r in repos:
        has_docker = any([
            _exists(r.get("dockerfile")),
            _exists(r.get("compose")),
            _exists(r.get("compose2")),
        ])
        has_ci = _exists(r.get("workflows"))

        has_tests = any([
            _exists(r.get("testsDir")),
            _exists(r.get("testDir")),
            _exists(r.get("testsUnderscore")),
            _exists(r.get("pytestIni")),
            _exists(r.get("toxIni")),
            _exists(r.get("noseCfg")),
            _exists(r.get("jestCfg")),
            _exists(r.get("vitestCfg")),
            _exists(r.get("vitestCfg2")),
        ])

        docker_repos += 1 if has_docker else 0
        ci_repos += 1 if has_ci else 0
        tests_repos += 1 if has_tests else 0

    # Contributions calendar: total + “active weeks”
    cal = u["contributionsCollection"]["contributionCalendar"]
    contribs_12mo = cal["totalContributions"]
    weeks = cal["weeks"]
    active_weeks = 0
    for w in weeks:
        days = w.get("contributionDays", [])
        if sum(d.get("contributionCount", 0) for d in days) > 0:
            active_weeks += 1

    # Language breakdown (aggregate by bytes)
    lang_sizes = {}
    for r in repos:
        langs = (r.get("languages") or {}).get("edges") or []
        for e in langs:
            size = int(e.get("size") or 0)
            name = (e.get("node") or {}).get("name") or "Other"
            lang_sizes[name] = lang_sizes.get(name, 0) + size

    total_lang_bytes = sum(lang_sizes.values())
    top_langs = sorted(lang_sizes.items(), key=lambda kv: kv[1], reverse=True)[:6]

    # Top repos by stars
    top = sorted(repos, key=lambda r: r["stargazerCount"], reverse=True)[:7]

    # Most recently pushed repos (by pushedAt)
    recent = sorted(
        [r for r in repos if r.get("pushedAt")],
        key=lambda r: r["pushedAt"],
        reverse=True
    )[:5]

    lines = []
    lines.append("### 📌 Core Stats")
    lines.append(f"- **Public repos (non-fork):** {total_repos}")
    lines.append(f"- **Total stars (across public non-fork repos):** {total_stars}")
    lines.append(f"- **Total forks (across public non-fork repos):** {total_forks}")
    lines.append(f"- **Followers:** {followers}  |  **Following:** {following}")
    lines.append("")
    lines.append("### 📈 Impact Metrics")
    lines.append(f"- **Starred repos:** {starred_repos} / {len(repos)} ({pct(starred_repos, len(repos))})")
    lines.append(f"- **Avg stars per repo:** {avg_stars:.2f}")
    lines.append(f"- **Repos with Docker (Dockerfile/compose):** {docker_repos} / {len(repos)} ({pct(docker_repos, len(repos))})" if len(repos) == 0 else
                 f"- **Repos with Docker (Dockerfile/compose):** {docker_repos} / {len(repos)} ({pct(docker_repos, len(repos))})")
    lines.append(f"- **Repos with CI (.github/workflows):** {ci_repos} / {len(repos)} ({pct(ci_repos, len(repos))})")
    lines.append(f"- **Repos with tests (heuristic):** {tests_repos} / {len(repos)} ({pct(tests_repos, len(repos))})")
    lines.append("")
    lines.append("### ⚡ Activity (default branch only)")
    lines.append(f"- **Contributions (last 12 months):** {contribs_12mo}")
    lines.append(f"- **Active weeks (last 12 months):** {active_weeks} / {len(weeks)}")
    lines.append(f"- **Commits (last 30 days):** {commits_30}")
    lines.append(f"- **Commits (last 90 days):** {commits_90}")
    lines.append(f"- **Commits (all-time, default branch):** {commits_all}")
    if repos_with_default_branch and repos_with_default_branch != len(repos):
        lines.append(f"- _Note:_ commit counts available for **{repos_with_default_branch}/{len(repos)}** repos (some repos may not have a standard default branch).")
    lines.append("")
    lines.append("### 🧠 Language Breakdown (by code size across repos)")
    if total_lang_bytes == 0:
        lines.append("- (No language data available)")
    else:
        for name, size in top_langs:
            p = (size / total_lang_bytes) * 100 if total_lang_bytes else 0
            lines.append(f"- **{name}:** {p:.0f}%")
    lines.append("")
    lines.append("### ⭐ Top repos by stars")
    for r in top:
        lines.append(f"- [{r['name']}]({r['url']}) — ⭐ {r['stargazerCount']} | 🍴 {r['forkCount']}")
    lines.append("")
    lines.append("### 🕒 Recently updated")
    for r in recent:
        lines.append(f"- [{r['name']}]({r['url']}) — last push **{human_dt(r.get('pushedAt'))}**")

    return "\n".join(lines)

def replace_between(text: str, start: str, end: str, new_content: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), flags=re.DOTALL)
    replacement = start + "\n" + new_content.strip() + "\n" + end
    if not pattern.search(text):
        raise RuntimeError(f"Could not find markers {start} ... {end} in {README_PATH}")
    return pattern.sub(replacement, text, count=1)

def main():
    username = os.environ.get("GH_USERNAME")
    if not username:
        print("Missing GH_USERNAME env var", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    since30 = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    since90 = (now - timedelta(days=90)).isoformat().replace("+00:00", "Z")

    data = gh_graphql(
        QUERY,
        {
            "login": username,
            "reposFirst": 100,
            "since30": since30,
            "since90": since90,
        },
    )
    block = format_block(data)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    updated = replace_between(readme, START, END, block)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(updated)

if __name__ == "__main__":
    main()
