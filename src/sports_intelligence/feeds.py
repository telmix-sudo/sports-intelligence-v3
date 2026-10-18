"""HTTP adapters: explicit provenance, timeouts, no fallback invented values."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.parse
import urllib.request

USER_AGENT = "SportsIntelligenceResearch/0.3 (+https://github.com/telmix-sudo/sports-intelligence-v3)"
FOOTBALL_SEASONS = ("2324", "2425", "2526", "2627")
FOOTBALL_CSV_BASE = "https://www.football-data.co.uk/mmz4281"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
SCOREBOARDS = {
    "epl": "soccer/eng.1",
    "la_liga": "soccer/esp.1",
    "bundesliga": "soccer/ger.1",
    "serie_a": "soccer/ita.1",
    "ligue_1": "soccer/fra.1",
    "nfl": "football/nfl",
    "nba": "basketball/nba",
    "nhl": "hockey/nhl",
    "mlb": "baseball/mlb",
}


def fetch_bytes(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read(3_500_000 + 1)
    if len(data) > 3_500_000:
        raise ValueError("Data source unexpectedly large")
    return data


def fetch_text(url: str, timeout: int = 12) -> tuple[str, dict]:
    raw = fetch_bytes(url, timeout)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return text, {"url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                  "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}


def football_csv_urls(seasons: tuple[str, ...] = FOOTBALL_SEASONS) -> list[str]:
    return [f"{FOOTBALL_CSV_BASE}/{season}/E0.csv" for season in seasons]


def espn_scoreboard_url(path: str, day: dt.date) -> str:
    date_part = day.strftime("%Y%m%d")
    return f"{ESPN_BASE}/{path}/scoreboard?{urllib.parse.urlencode({'dates': date_part, 'limit': 300})}"


def fetch_espn(path: str, day: dt.date) -> tuple[dict, dict]:
    url = espn_scoreboard_url(path, day)
    content, metadata = fetch_text(url)
    result = json.loads(content)
    if not isinstance(result, dict) or not isinstance(result.get("events", []), list):
        raise ValueError("ESPN scoreboard schema invalid")
    return result, metadata
