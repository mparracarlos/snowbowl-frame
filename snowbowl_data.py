"""
Fetch and parse the Arizona Snowbowl snow report from OnTheSnow.

OnTheSnow's resort page (a Next.js app) embeds its data in the page as a
"flight" payload pushed through `self.__next_f.push([1, "..."])`. That payload
is a list of lines like  `23:{"last24":5.08,...}`  where values can point to
other lines with "$23". Units in the payload are already metric:
snow in cm, temperatures in °C, wind in m/s (converted to km/h here).

Requests: the scheduled GitHub job calls fetch_report() three times a day
(before 6:00, 11:00 and 18:00 Phoenix), so OnTheSnow sees ~3 page loads a day.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional

RESORT_URL = "https://www.onthesnow.com/arizona/arizona-snowbowl/skireport"
PHOENIX = timezone(timedelta(hours=-7))  # Arizona does not use daylight saving
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', re.S)
_LINE_RE = re.compile(r"^([0-9a-f]+):(.*)$")


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def fetch_payload_with_playwright(url: str = RESORT_URL) -> str:
    """Load the page in headless Chromium and return the joined flight payload."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_function("self.__next_f && self.__next_f.length > 5", timeout=20000)
        payload = page.evaluate("self.__next_f.map(x => x[1] || '').join('')")
        browser.close()
    return payload


def fetch_payload_with_urllib(url: str = RESORT_URL) -> str:
    """Plain HTTP fallback: download the HTML and pull the payload strings out."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return payload_from_html(html)


def payload_from_html(html: str) -> str:
    parts = [json.loads(s) for s in _PUSH_RE.findall(html)]
    return "".join(p for p in parts if isinstance(p, str))


def fetch_report(methods: str = "urllib,playwright") -> Dict[str, Any]:
    """Fetch and parse the report. Tries plain HTTP first, then a headless browser."""
    errors = []
    available = {"urllib": fetch_payload_with_urllib, "playwright": fetch_payload_with_playwright}
    for fetcher in [available[m.strip()] for m in methods.split(",") if m.strip() in available]:
        try:
            payload = fetcher()
            report = parse_payload(payload)
            report["source"] = fetcher.__name__.replace("fetch_payload_with_", "")
            return report
        except Exception as exc:  # noqa: BLE001 - try the next method
            errors.append("%s: %s" % (fetcher.__name__, exc))
    raise RuntimeError("Could not fetch OnTheSnow report. " + " | ".join(errors))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _index_lines(payload: str) -> Dict[str, Any]:
    table: Dict[str, Any] = {}
    for line in payload.split("\n"):
        m = _LINE_RE.match(line)
        if not m:
            continue
        body = m.group(2)
        if not body or body[0] not in "{[":
            continue
        try:
            table[m.group(1)] = json.loads(body)
        except ValueError:
            continue
    return table


def _resolve(value: Any, table: Dict[str, Any], depth: int = 0) -> Any:
    if depth > 12:
        return value
    if isinstance(value, str) and re.fullmatch(r"\$[0-9a-f]+", value):
        return _resolve(table.get(value[1:]), table, depth + 1)
    if isinstance(value, dict):
        return {k: _resolve(v, table, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, table, depth + 1) for v in value]
    return value


def _num(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def parse_payload(payload: str) -> Dict[str, Any]:
    table = _index_lines(payload)
    root = None
    for obj in table.values():
        if isinstance(obj, dict) and {"snow", "lifts", "runs", "status", "currentWeather"} <= set(obj):
            root = obj
            break
    if root is None:
        raise ValueError("Snow report block not found in page payload (layout may have changed)")
    r = _resolve(root, table)

    snow = r.get("snow") or {}
    status = r.get("status") or {}
    terrain = r.get("terrain") or {}
    cw = r.get("currentWeather") or {}

    def open_total(block: Any) -> Dict[str, Optional[int]]:
        block = block or {}
        o = block.get("open", block.get("openNum"))
        t = block.get("total")
        return {"open": int(o) if o is not None else None, "total": int(t) if t is not None else None}

    def minmax(block: Any) -> Dict[str, Optional[float]]:
        block = block or {}
        return {"min": _num(block.get("min")), "max": _num(block.get("max"))}

    weather_days = []
    for d in r.get("weather") or []:
        if not isinstance(d, dict):
            continue
        wind = d.get("wind") or {}
        weather_days.append({
            "date": d.get("reportedDate"),
            "high_c": _num(d.get("tempHigh")),
            "low_c": _num(d.get("tempLow")),
            "snow_cm": _num(d.get("snowfall")) or 0.0,
            "forecast": bool(d.get("isForecast")),
            "type": d.get("type") or "",
            "wind_kmh": round(_num(wind.get("speed")) * 3.6) if _num(wind.get("speed")) is not None else None,
            "gust_kmh": round(_num(wind.get("gusts")) * 3.6) if _num(wind.get("gusts")) is not None else None,
        })

    report = {
        "resort": r.get("title") or "Arizona Snowbowl",
        "report_updated": r.get("updatedAt"),
        "fetched_at": datetime.now(PHOENIX).isoformat(timespec="minutes"),
        "new_snow_cm": {
            "24h": _num(snow.get("last24")),
            "48h": _num(snow.get("last48")),
            "72h": _num(snow.get("last72")),
        },
        "depth_cm": {k: _num((r.get("depths") or {}).get(k)) for k in ("base", "middle", "summit")},
        "typical_depth_cm": {k: _num((r.get("depthAverages") or {}).get(k)) for k in ("base", "middle", "summit")},
        "lifts": open_total(r.get("lifts")),
        "runs": open_total(r.get("runs")),
        "runs_by_level": {
            "Beginner": open_total(terrain.get("runsBeginner")),
            "Intermediate": open_total(terrain.get("runsIntermediate")),
            "Advanced": open_total(terrain.get("runsAdvanced")),
            "Expert": open_total(terrain.get("runsExpert")),
        },
        "parks": open_total(terrain.get("parks")),
        "acres": open_total(terrain.get("acres")),
        "open_flag": status.get("openFlag"),
        "opening_date": status.get("openingDate"),
        "closing_date": status.get("closingDate"),
        "now": {
            "date": cw.get("date"),
            "type": cw.get("type") or "",
            "wind_kmh": round(_num(cw.get("wind")) * 3.6) if _num(cw.get("wind")) is not None else None,
            "base": minmax(cw.get("base")),
            "mid": minmax(cw.get("mid")),
            "summit": minmax(cw.get("summit")),
        },
        "snow_forecast": [
            {"date": d.get("date"), "snow_cm": _num(d.get("snow")) or 0.0}
            for d in (r.get("forecast") or []) if isinstance(d, dict)
        ],
        "weather": weather_days,
    }
    return report


# ---------------------------------------------------------------------------
# Derived values used by the dashboards
# ---------------------------------------------------------------------------
POW_THRESHOLD_CM = 25.0  # ~10 inches


def today_phx() -> date:
    return datetime.now(PHOENIX).date()


def forecast_today_cm(report: Dict[str, Any], today: Optional[date] = None) -> float:
    today = today or today_phx()
    for d in report.get("weather", []):
        if d.get("date") == today.isoformat():
            return d.get("snow_cm") or 0.0
    for d in report.get("snow_forecast", []):
        if d.get("date") == today.isoformat():
            return d.get("snow_cm") or 0.0
    return 0.0


def pow_pow(report: Dict[str, Any], today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Return {'cm': x, 'kind': 'new'|'forecast'} when today is a powder day (>= 25 cm)."""
    new24 = report.get("new_snow_cm", {}).get("24h") or 0.0
    upd = (report.get("report_updated") or "")[:10]
    today = today or today_phx()
    # Only trust the resort's 24 h number if the report is from today or yesterday.
    report_fresh = upd in (today.isoformat(), (today - timedelta(days=1)).isoformat())
    if report_fresh and new24 >= POW_THRESHOLD_CM:
        return {"cm": new24, "kind": "new"}
    fc = forecast_today_cm(report, today)
    if fc >= POW_THRESHOLD_CM:
        return {"cm": fc, "kind": "forecast"}
    return None


def is_open(report: Dict[str, Any]) -> bool:
    lifts = report.get("lifts", {}).get("open")
    return bool(lifts) or report.get("open_flag") == 1


if __name__ == "__main__":
    import sys

    data = fetch_report()
    json.dump(data, sys.stdout, indent=2)
    print()
