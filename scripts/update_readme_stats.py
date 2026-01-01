import os
import re
import sys
import json
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]

QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    repositories(privacy: PUBLIC, isFork: false, first: 100, ownerAffiliations: OWNER, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        name
        stargazerCount
        forkCount
        url
      }
    }
    followers { totalCount }
    following { totalCount }
    contributionsCollection {
      contributionCalendar {
        totalContributions
      }
    }
  }
}
"""

def format_block(d: dict) -> str:
    u = d["user"]
    repos = u["repositories"]["nodes"]

    total_repos = u["repositories"]["totalCount"]
    total_stars = sum(r["stargazerCount"] for r in repos)
    total_forks = sum(r["forkCount"] for r in repos)
    contribs = u["contributionsCollection"]["contributionCalendar"]["totalContributions"]
    followers = u["followers"]["totalCount"]
    following = u["following"]["totalCount"]

    top = sorted(repos, key=lambda r: r["stargazerCount"], reverse=True)[:5]

    lines = []
    lines.append(f"- **Public repos (non-fork):** {total_repos}")
    lines.append(f"- **Total stars (across public non-fork repos):** {total_stars}")
    lines.append(f"- **Total forks (across public non-fork repos):** {total_forks}")
    lines.append(f"- **Contributions (last 12 months):** {contribs}")
    lines.append(f"- **Followers:** {followers}  |  **Following:** {following}")
    lines.append("")
    lines.append("**Top repos by stars:**")
    for r in top:
        lines.append(f"- [{r['name']}]({r['url']}) — ⭐ {r['stargazerCount']} | 🍴 {r['forkCount']}")
    return "\n".join(lines)

def replace_between(text: str, start: str, end: str, new_content: str) -> str:
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end),
        flags=re.DOTALL,
    )
    replacement = start + "\n" + new_content.strip() + "\n" + end
    if not pattern.search(text):
        raise RuntimeError(f"Could not find markers {start} ... {end} in README.md")
    return pattern.sub(replacement, text, count=1)

def main():
    username = os.environ.get("GH_USERNAME")
    if not username:
        print("Missing GH_USERNAME env var", file=sys.stderr)
        sys.exit(1)

    data = gh_graphql(QUERY, {"login": username})
    block = format_block(data)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    updated = replace_between(readme, START, END, block)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(updated)

if __name__ == "__main__":
    main()
