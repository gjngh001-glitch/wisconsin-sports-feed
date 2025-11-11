#!/usr/bin/env python3
"""
Wisconsin Sports Aggregator RSS — Packers, Bucks, Brewers, Badgers Only
------------------------------------------------------------------------
- Scores from ESPN scoreboards (NFL/NBA/MLB + Badgers: NCAAF/NCAAM/NCAAW/W-Volleyball).
- Official team news merged into the same feed:
  • Packers: packers.com/news/rss
  • Bucks: nba.com/bucks/rss.xml
  • Brewers: mlb.com/brewers/feeds/news/rss.xml
  • UW Badgers: football, men's basketball, women's basketball, volleyball

Usage examples:
  python wisco_core_teams_rss.py --out wisconsin-sports.xml --days 6 --max-items 150 --live-final-only
  python wisco_core_teams_rss.py --out wisconsin-sports.xml --days 10 --max-items 200  # include previews

Notes:
- ESPN scoreboard endpoints are unofficial and may change.
- Review team site terms for public display usage.
"""

import argparse
import datetime as dt
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import format_datetime, parsedate_to_datetime
from html import escape

# ---------------------- CONFIG ----------------------

# Restrict to the four targets only (robust matching strings)
TEAM_MATCHERS = [
    # Packers (NFL)
    "Green Bay Packers", "Packers", "GB",
    # Bucks (NBA)
    "Milwaukee Bucks", "Bucks", "MIL",
    # Brewers (MLB)
    "Milwaukee Brewers", "Brewers",
    # Wisconsin Badgers (UW)
    "Wisconsin Badgers", "Wisconsin", "UW", "UW–Madison", "UW-Madison",
]

# Leagues required for these teams only
LEAGUES = [
    ("football", "nfl", "NFL"),                         # Packers
    ("basketball", "nba", "NBA"),                       # Bucks
    ("baseball", "mlb", "MLB"),                         # Brewers
    ("football", "college-football", "NCAAF"),          # Badgers football
    ("basketball", "mens-college-basketball", "NCAAM"), # Badgers men's basketball
    ("basketball", "womens-college-basketball", "NCAAW"), # Badgers women's basketball
    ("volleyball", "womens-college-volleyball", "NCAAWV"), # Badgers women's volleyball
]

# Official team news RSS feeds to merge
TEAM_NEWS_FEEDS = [
    ("Packers News", "https://www.packers.com/news/rss"),
    ("Bucks News", "https://www.nba.com/bucks/rss.xml"),
    ("Brewers News", "https://www.mlb.com/brewers/feeds/news/rss.xml"),
    ("UW News • Football", "https://uwbadgers.com/rss?path=football"),
    ("UW News • Men's Basketball", "https://uwbadgers.com/rss?path=mbball"),
    ("UW News • Women's Basketball", "https://uwbadgers.com/rss?path=wbball"),
    ("UW News • Volleyball", "https://uwbadgers.com/rss?path=wvball"),
]

# ---------------------------------------------------

def fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (RSS Aggregator)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")

def fetch_json(url: str, timeout: int = 20):
    import json as _json
    return _json.loads(fetch_text(url, timeout=timeout))

def date_range(center: dt.date, days: int):
    span = max(days, 0)
    for offset in range(-span // 2, span // 2 + 1):
        yield center + dt.timedelta(days=offset)

def matches_team(name: str) -> bool:
    n = (name or "").lower()
    return any(key.lower() in n for key in TEAM_MATCHERS)

def pick_game_link(event: dict) -> str:
    links = event.get("links") or []
    for rel in ("boxscore", "summary", "pbp", "recap"):
        for l in links:
            if rel in (l.get("rel") or []):
                return l.get("href") or ""
    for l in links:
        if l.get("href"):
            return l["href"]
    return event.get("shortLink") or event.get("web") or "https://www.espn.com/"

def event_to_item(event: dict, league_label: str, live_final_only: bool) -> dict | None:
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[-1])

    def team_label(c):
        t = c.get("team") or {}
        return t.get("displayName") or t.get("shortDisplayName") or t.get("name") or ""

    def abbr(c):
        t = c.get("team") or {}
        return t.get("abbreviation") or ""

    home_name = team_label(home)
    away_name = team_label(away)

    # Only include events involving our four targets
    if not (matches_team(home_name) or matches_team(away_name) or matches_team(abbr(home)) or matches_team(abbr(away))):
        return None

    status = (comp.get("status") or {}).get("type") or {}
    state = status.get("state")  # "pre" / "in" / "post"
    detail = status.get("shortDetail") or status.get("detail") or ""

    if live_final_only and state not in {"in", "post"}:
        return None

    home_score = home.get("score") or "0"
    away_score = away.get("score") or "0"

    state_label = "Final" if state == "post" else ("Live" if state == "in" else "Preview")
    title = f"{league_label} • {state_label}: {away_name} {away_score} @ {home_name} {home_score} ({detail})"
    description = f"{escape(away_name)} vs {escape(home_name)} — {escape(detail)}"

    link = pick_game_link(event)
    pubdate = event.get("date")
    try:
        dt_pub = dt.datetime.fromisoformat(pubdate.replace("Z", "+00:00")).astimezone(dt.timezone.utc) if pubdate else dt.datetime.now(dt.timezone.utc)
    except Exception:
        dt_pub = dt.datetime.now(dt.timezone.utc)

    return {
        "title": title,
        "link": link,
        "description": description,
        "pubDate": format_datetime(dt_pub),
        "guid": (event.get("id") or "") + "-" + league_label,
        "kind": "score",
    }

def parse_team_rss(feed_title: str, url: str, max_items: int = 25) -> list[dict]:
    try:
        xml = fetch_text(url, timeout=20)
        root = ET.fromstring(xml)
    except Exception:
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate") or ""
        try:
            dt_pub = parsedate_to_datetime(pub) if pub else dt.datetime.now(dt.timezone.utc)
            if not dt_pub.tzinfo:
                dt_pub = dt_pub.replace(tzinfo=dt.timezone.utc)
        except Exception:
            dt_pub = dt.datetime.now(dt.timezone.utc)

        items.append({
            "title": f"{feed_title}: {title}",
            "link": link or "",
            "description": desc,
            "pubDate": format_datetime(dt_pub.astimezone(dt.timezone.utc)),
            "guid": f"news-{hash(feed_title + title + link + pub)}",
            "kind": "news",
        })
        if len(items) >= max_items:
            break
    return items

def build_rss(items: list, title="Wisconsin Sports (Core Teams)", link="https://www.espn.com/",
              description="Packers, Bucks, Brewers scores + UW Badgers scores & news.") -> str:
    now = format_datetime(dt.datetime.now(dt.timezone.utc))
    buf = []
    buf.append('<?xml version="1.0" encoding="UTF-8"?>')
    buf.append('<rss version="2.0">')
    buf.append("<channel>")
    buf.append(f"<title>{escape(title)}</title>")
    buf.append(f"<link>{escape(link)}</link>")
    buf.append(f"<description>{escape(description)}</description>")
    buf.append(f"<lastBuildDate>{now}</lastBuildDate>")
    for it in items:
        buf.append("<item>")
        buf.append(f"<title>{escape(it['title'])}</title>")
        buf.append(f"<link>{escape(it['link'])}</link>")
        buf.append(f"<guid isPermaLink='false'>{escape(it['guid'])}</guid>")
        buf.append(f"<pubDate>{it['pubDate']}</pubDate>")
        buf.append(f"<description><![CDATA[{it['description']}]]></description>")
        buf.append("</item>")
    buf.append("</channel>")
    buf.append("</rss>")
    return "\n".join(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="wisconsin-sports.xml", help="Output RSS XML file path")
    ap.add_argument("--days", type=int, default=6, help="Span of days centered on today (increase for more items)")
    ap.add_argument("--max-items", type=int, default=200, help="Max items in the final RSS feed")
    ap.add_argument("--live-final-only", action="store_true", help="Include only live and final games (skip previews)")
    ap.add_argument("--no-team-news", action="store_true", help="Disable merging of official team news feeds")
    args = ap.parse_args()

    today = dt.date.today()
    items = []

    # 1) SCORES: ESPN scoreboards for just the leagues we need
    for sport, league, label in LEAGUES:
        for d in date_range(today, args.days):
            ymd = d.strftime("%Y%m%d")
            url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={ymd}&limit=300"
            try:
                data = fetch_json(url)
            except Exception as e:
                print(f"[WARN] Failed {url}: {e}", file=sys.stderr)
                continue

            for event in data.get("events", []):
                item = event_to_item(event, label, live_final_only=args.live_final_only)
                if item:
                    items.append(item)

    # 2) NEWS: Official team feeds
    if not args.no_team_news:
        for feed_title, url in TEAM_NEWS_FEEDS:
            try:
                items.extend(parse_team_rss(feed_title, url, max_items=20))
            except Exception as e:
                print(f"[WARN] News feed failed {url}: {e}", file=sys.stderr)

    # Sort newest-first and cap
    def parse_rfc2822(s):
        try:
            return parsedate_to_datetime(s)
        except Exception:
            return dt.datetime.now(dt.timezone.utc)

    items.sort(key=lambda x: parse_rfc2822(x["pubDate"]), reverse=True)
    if args.max_items > 0:
        items = items[: args.max_items]

    rss = build_rss(items)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"[OK] wrote {args.out} with {len(items)} items.")

if __name__ == "__main__":
    main()
