"""
Draws the two 800x480 black-and-white dashboards for the Waveshare 7.5" V2 panel.

  Page 1 "Conditions": mountain sketch, new snow, depths, temperatures, lifts/runs,
                        and a POW POW! banner on days with >= 25 cm.
  Page 2 "Outlook":    5-day forecast, last 7 days of snow, 8-day total, season dates.

All temperatures are °C and all snow is cm (OnTheSnow already provides metric).
"""
from __future__ import annotations

import glob
import math
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

import snowbowl_data as sd

W, H = 800, 480
BLACK, WHITE = 0, 255
HERE = os.path.dirname(os.path.abspath(__file__))
SKETCHES_DIR = os.path.join(HERE, "sketches")

# ---------------------------------------------------------------------------
# Fonts (macOS first, Linux fallbacks for testing)
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "black": [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/Library/Fonts/Arial Black.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}
_font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _font_cache:
        for path in _FONT_CANDIDATES[kind]:
            if os.path.exists(path):
                _font_cache[key] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def fmt_cm(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return "%d" % round(v) if v >= 1 or v == 0 else "%.1f" % v


def fmt_c(v: Optional[float]) -> str:
    return "—" if v is None else "%d°" % round(v)


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def mon_day(d: Optional[date]) -> str:
    return d.strftime("%b %d").replace(" 0", " ") if d else "—"


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    l, t, r, b = draw.textbbox((0, 0), s, font=f)
    return r - l


def bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, frac: float) -> None:
    draw.rectangle([x, y, x + w, y + h], outline=BLACK, width=2, fill=WHITE)
    fw = int(round(w * max(0.0, min(1.0, frac))))
    if fw > 0:
        draw.rectangle([x, y, x + fw, y + h], fill=BLACK)


def header(draw: ImageDraw.ImageDraw, left: str, right: str) -> None:
    draw.rectangle([0, 0, W, 52], fill=BLACK)
    draw.text((20, 26), left, font=font("bold", 28), fill=WHITE, anchor="lm")
    draw.text((W - 20, 26), right, font=font("bold", 18), fill=WHITE, anchor="rm")


def footer(draw: ImageDraw.ImageDraw, report: Dict[str, Any], page: int, now: datetime) -> None:
    upd = parse_date(report.get("report_updated"))
    fetched = report.get("fetched_at", "")
    try:
        fetched_s = datetime.fromisoformat(fetched).strftime("%-I:%M %p").lower()
    except ValueError:
        fetched_s = "—"
    s = "Resort report %s · OnTheSnow data %s · %d/2" % (mon_day(upd), fetched_s, page)
    if report.get("_stale"):
        s = "OFFLINE · " + s
    draw.text((W - 16, H - 10), s, font=font("regular", 13), fill=BLACK, anchor="rs")


# ---------------------------------------------------------------------------
# Weather icons, drawn with primitives so they stay crisp in 1-bit
# ---------------------------------------------------------------------------
def _cloud(draw, cx, cy, s, fill=WHITE, outline=BLACK):
    w = max(2, int(s * 0.07))
    parts = [(cx - s * 0.30, cy - s * 0.02, s * 0.22), (cx - s * 0.05, cy - s * 0.15, s * 0.28),
             (cx + s * 0.25, cy, s * 0.20)]
    for x, y, r in parts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=outline)
    draw.rounded_rectangle([cx - s * 0.48, cy - s * 0.02, cx + s * 0.45, cy + s * 0.22], radius=int(s * 0.12), fill=outline)
    if fill != outline:
        for x, y, r in parts:
            draw.ellipse([x - r + w, y - r + w, x + r - w, y + r - w], fill=fill)
        draw.rounded_rectangle([cx - s * 0.48 + w, cy - s * 0.02 + w, cx + s * 0.45 - w, cy + s * 0.22 - w],
                               radius=int(s * 0.10), fill=fill)


def _sun(draw, cx, cy, r, color=BLACK):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    for i in range(8):
        a = i * math.pi / 4
        x1, y1 = cx + math.cos(a) * r * 1.45, cy + math.sin(a) * r * 1.45
        x2, y2 = cx + math.cos(a) * r * 2.0, cy + math.sin(a) * r * 2.0
        draw.line([x1, y1, x2, y2], fill=color, width=max(2, int(r * 0.28)))


def _flakes(draw, cx, cy, s, color=BLACK):
    r = max(2, int(s * 0.055))
    for dx, dy in [(-0.25, 0.38), (0, 0.46), (0.25, 0.38), (-0.12, 0.56), (0.12, 0.56)]:
        x, y = cx + dx * s, cy + dy * s
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def weather_icon(draw, kind: str, cx: int, cy: int, s: int, invert: bool = False) -> None:
    fg, bg = (WHITE, BLACK) if invert else (BLACK, WHITE)
    k = (kind or "").upper()
    if "SNOW" in k or "FLURR" in k or "SLEET" in k:
        _cloud(draw, cx, cy - s * 0.12, s, fill=fg, outline=fg)
        _flakes(draw, cx, cy - s * 0.12, s, color=fg)
    elif "RAIN" in k or "DRIZZLE" in k or "SHOWER" in k or "STORM" in k:
        _cloud(draw, cx, cy - s * 0.12, s, fill=fg, outline=fg)
        for dx in (-0.22, 0, 0.22):
            x = cx + dx * s
            draw.line([x, cy + s * 0.25, x - s * 0.08, cy + s * 0.45], fill=fg, width=max(2, int(s * 0.06)))
    elif "FOG" in k or "MIST" in k or "HAZE" in k:
        for i, dy in enumerate((-0.25, -0.05, 0.15, 0.35)):
            inset = 0.1 if i % 2 else 0
            draw.line([cx - s * (0.45 - inset), cy + dy * s, cx + s * (0.45 - inset), cy + dy * s],
                      fill=fg, width=max(3, int(s * 0.08)))
    elif "MOSTLY_SUNNY" in k or "SLIGHTLY" in k or "PARTLY" in k or "FAIR" in k:
        _sun(draw, cx - s * 0.15, cy - s * 0.15, int(s * 0.17), color=fg)
        _cloud(draw, cx + s * 0.08, cy + s * 0.12, int(s * 0.8), fill=bg, outline=fg)
    elif "CLOUD" in k or "OVERCAST" in k:
        _cloud(draw, cx, cy, s, fill=fg, outline=fg)
    else:  # SUN / CLEAR
        _sun(draw, cx, cy, int(s * 0.2), color=fg)


def describe(kind: str) -> str:
    k = (kind or "").upper()
    names = {"SUN": "Sunny", "CLEAR": "Clear", "MOSTLY_SUNNY": "Mostly sunny", "SLIGHTLY_CLOUDY": "Partly cloudy",
             "CLOUDY": "Cloudy", "OVERCAST": "Overcast", "FOG": "Fog", "SNOW": "Snow",
             "SNOW_SHOWERS": "Snow showers", "RAIN": "Rain"}
    return names.get(k, k.replace("_", " ").capitalize() or "—")


# ---------------------------------------------------------------------------
# Mountain sketch
# ---------------------------------------------------------------------------
_sketch_cache: Dict[Tuple[str, int, int], Image.Image] = {}


def list_sketches() -> List[str]:
    files: List[str] = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
        files.extend(glob.glob(os.path.join(SKETCHES_DIR, ext)))
    return sorted(set(files))


def pick_sketch(now: datetime) -> Optional[str]:
    files = list_sketches()
    if not files:
        return None
    # A new sketch every time page 1 comes around (every 2 hours), cycling through the folder.
    slot = (now.timetuple().tm_yday * 24 + now.hour) // 2
    return files[slot % len(files)]


def sketch_image(path: str, w: int, h: int) -> Image.Image:
    key = (path, w, h)
    if key not in _sketch_cache:
        img = Image.open(path).convert("L")
        img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.3))
        img = ImageOps.autocontrast(img, cutoff=1)
        _sketch_cache[key] = img.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
    return _sketch_cache[key]


def draw_fallback_mountain(draw, x0, y0, w, h):
    """Simple line drawing of the San Francisco Peaks if the sketches folder is empty."""
    pts = [(0, 1), (0.18, 0.55), (0.3, 0.62), (0.45, 0.18), (0.55, 0.32), (0.66, 0.1), (0.82, 0.5), (1, 0.8), (1, 1)]
    poly = [(x0 + px * w, y0 + py * h) for px, py in pts]
    draw.polygon(poly, outline=BLACK, fill=WHITE)
    draw.line(poly[:-1], fill=BLACK, width=3)
    for i in range(6):
        draw.line([x0 + (0.47 + i * 0.03) * w, y0 + (0.25 + i * 0.1) * h,
                   x0 + (0.44 + i * 0.03) * w, y0 + (0.38 + i * 0.1) * h], fill=BLACK, width=2)


# ---------------------------------------------------------------------------
# Page 1 — Conditions
# ---------------------------------------------------------------------------
def render_conditions(report: Dict[str, Any], now: datetime) -> Image.Image:
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    today = now.date()
    opening = parse_date(report.get("opening_date"))
    closing = parse_date(report.get("closing_date"))
    is_open = sd.is_open(report)

    if is_open:
        status = "OPEN"
    elif opening and opening > today:
        status = "OPENS %s" % mon_day(opening).upper()
    else:
        status = "CLOSED"
    header(d, "ARIZONA SNOWBOWL", status)

    # --- Mountain sketch (left) ---------------------------------------------
    sx0, sy0, sw, sh = 0, 52, 480, 300
    path = pick_sketch(now)
    if path:
        img.paste(sketch_image(path, sw, sh), (sx0, sy0))
    else:
        draw_fallback_mountain(d, sx0 + 10, sy0 + 20, sw - 20, sh - 30)
    d.line([sw, sy0, sw, sy0 + sh], fill=BLACK, width=3)

    pow_ = sd.pow_pow(report, today)
    if pow_:
        by = sy0 + sh - 108
        d.rectangle([sx0, by, sx0 + sw, sy0 + sh], fill=BLACK)
        size = 80  # shrink until it fits (Arial Black on the Mac is wide)
        while size > 40 and text_w(d, "POW POW!", font("black", size)) > sw - 40:
            size -= 2
        d.text((sx0 + sw // 2, by + 44), "POW POW!", font=font("black", size), fill=WHITE, anchor="mm")
        label = "%s cm %s" % (fmt_cm(pow_["cm"]),
                              "of new snow" if pow_["kind"] == "new" else "forecast today")
        d.text((sx0 + sw // 2, by + 90), label, font=font("bold", 20), fill=WHITE, anchor="mm")

    # --- Right column: new snow, depth, temps --------------------------------
    rx = 500
    upd = parse_date(report.get("report_updated"))
    report_fresh = upd is not None and (today - upd).days <= 1
    ns = report.get("new_snow_cm", {})

    if report_fresh or is_open:
        title = "NEW SNOW · 24 H"
        big = fmt_cm(ns.get("24h") or 0)
        sub = "48 h %s · 72 h %s cm" % (fmt_cm(ns.get("48h") or 0), fmt_cm(ns.get("72h") or 0))
    else:
        title = "SNOW TODAY · FORECAST"
        big = fmt_cm(sd.forecast_today_cm(report, today))
        sub = "resort's last report: %s" % mon_day(upd)
    d.text((rx, 66), title, font=font("bold", 15), fill=BLACK)
    f_big = font("bold", 86)
    d.text((rx - 4, 86), big, font=f_big, fill=BLACK)
    d.text((rx + text_w(d, big, f_big) + 4, 150), "cm", font=font("bold", 26), fill=BLACK)
    d.text((rx, 188), sub, font=font("regular", 15), fill=BLACK)

    d.line([rx, 214, W - 16, 214], fill=BLACK, width=2)
    cols = (rx, rx + 100, rx + 180)
    d.text((cols[1], 224), "DEPTH", font=font("bold", 13), fill=BLACK)
    d.text((cols[2], 224), "LOW / HIGH", font=font("bold", 13), fill=BLACK)
    depth = report.get("depth_cm", {})
    nw = report.get("now", {})
    rows = [("Summit", depth.get("summit"), nw.get("summit", {})),
            ("Mid", depth.get("middle"), nw.get("mid", {})),
            ("Base", depth.get("base"), nw.get("base", {}))]
    y = 246
    for name, dep, t in rows:
        d.text((cols[0], y), name, font=font("bold", 19), fill=BLACK)
        d.text((cols[1], y), ("%s cm" % fmt_cm(dep)) if dep is not None else "—", font=font("regular", 19), fill=BLACK)
        d.text((cols[2], y), "%s / %sC" % (fmt_c(t.get("min")), fmt_c(t.get("max"))), font=font("regular", 18), fill=BLACK)
        y += 28
    wind = nw.get("wind_kmh")
    d.text((rx, y + 2), "%s · wind %s km/h" % (describe(nw.get("type")), wind if wind is not None else "—"),
           font=font("regular", 15), fill=BLACK)

    # --- Bottom strip: lifts & runs, or opening countdown --------------------
    by = 352
    d.line([0, by, W, by], fill=BLACK, width=3)
    lifts, runs = report.get("lifts", {}), report.get("runs", {})
    if is_open or not (opening and opening > today):
        def ot(x):
            return (x.get("open") or 0), (x.get("total") or 0)
        lo, lt = ot(lifts)
        ro, rt = ot(runs)
        d.text((20, by + 14), "LIFTS OPEN", font=font("bold", 15), fill=BLACK)
        d.text((360, by + 14), "%d / %d" % (lo, lt), font=font("bold", 15), fill=BLACK, anchor="ra")
        bar(d, 20, by + 36, 340, 16, lo / lt if lt else 0)
        d.text((20, by + 64), "RUNS OPEN", font=font("bold", 15), fill=BLACK)
        d.text((360, by + 64), "%d / %d" % (ro, rt), font=font("bold", 15), fill=BLACK, anchor="ra")
        bar(d, 20, by + 86, 340, 16, ro / rt if rt else 0)
        levels = report.get("runs_by_level", {})
        gx = [400, 600]
        gy = [by + 22, by + 64]
        for i, name in enumerate(["Beginner", "Intermediate", "Advanced", "Expert"]):
            x, yy = gx[i % 2], gy[i // 2]
            v = levels.get(name, {})
            d.text((x, yy), name, font=font("regular", 17), fill=BLACK)
            d.text((x + 180, yy), "%s/%s" % (v.get("open") if v.get("open") is not None else "—", v.get("total") or "—"),
                   font=font("bold", 17), fill=BLACK, anchor="ra")
    else:
        days = (opening - today).days
        d.text((20, by + 12), "OPENING DAY", font=font("bold", 15), fill=BLACK)
        d.text((20, by + 32), "in %d day%s" % (days, "" if days == 1 else "s"), font=font("bold", 48), fill=BLACK)
        info = "%s – %s" % (mon_day(opening), mon_day(closing))
        d.text((400, by + 18), "Season", font=font("regular", 17), fill=BLACK)
        d.text((780, by + 18), info, font=font("bold", 17), fill=BLACK, anchor="ra")
        d.text((400, by + 46), "Lifts · runs · acres", font=font("regular", 17), fill=BLACK)
        terrain = "%s · %s · %s" % (lifts.get("total") or "—", runs.get("total") or "—",
                                    (report.get("acres") or {}).get("total") or "—")
        d.text((780, by + 46), terrain, font=font("bold", 17), fill=BLACK, anchor="ra")
        nxt = sum(x["snow_cm"] for x in report.get("snow_forecast", [])
                  if parse_date(x.get("date")) and parse_date(x["date"]) >= today)
        d.text((400, by + 74), "Snow next 8 days", font=font("regular", 17), fill=BLACK)
        d.text((780, by + 74), "%s cm" % fmt_cm(nxt), font=font("bold", 17), fill=BLACK, anchor="ra")

    footer(d, report, 1, now)
    return img


# ---------------------------------------------------------------------------
# Page 2 — Outlook
# ---------------------------------------------------------------------------
def render_outlook(report: Dict[str, Any], now: datetime) -> Image.Image:
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    today = now.date()
    header(d, "SNOWBOWL OUTLOOK", now.strftime("%a %b %d").replace(" 0", " ").upper())

    weather = report.get("weather", [])
    upcoming = [x for x in weather if parse_date(x.get("date")) and parse_date(x["date"]) >= today][:5]
    past = [x for x in weather if parse_date(x.get("date")) and parse_date(x["date"]) < today][-7:]

    # --- 5-day forecast ---------------------------------------------------
    col_w, x0, top, bottom = 152, 20, 60, 258
    best = max(upcoming, key=lambda x: x.get("snow_cm") or 0) if upcoming else None
    if best is not None and (best.get("snow_cm") or 0) < 1:
        best = None
    for i, day in enumerate(upcoming):
        cx = x0 + i * col_w + col_w // 2
        inv = day is best
        fg = WHITE if inv else BLACK
        if inv:
            d.rectangle([x0 + i * col_w + 2, top, x0 + (i + 1) * col_w - 2, bottom], fill=BLACK)
        dd = parse_date(day["date"])
        name = "TODAY" if dd == today else dd.strftime("%a").upper()
        d.text((cx, top + 16), name, font=font("bold", 20), fill=fg, anchor="mm")
        weather_icon(d, day.get("type"), cx, top + 66, 64, invert=inv)
        d.text((cx, top + 132), fmt_cm(day.get("snow_cm")), font=font("bold", 40), fill=fg, anchor="mm")
        d.text((cx, top + 160), "cm", font=font("regular", 15), fill=fg, anchor="mm")
        d.text((cx, top + 184), "%s / %sC" % (fmt_c(day.get("low_c")), fmt_c(day.get("high_c"))),
               font=font("regular", 17), fill=fg, anchor="mm")
        if i:
            d.line([x0 + i * col_w, top + 6, x0 + i * col_w, bottom - 6], fill=BLACK, width=1)

    d.line([0, 270, W, 270], fill=BLACK, width=3)

    # --- Last 7 days bar chart -------------------------------------------
    d.text((20, 284), "LAST 7 DAYS · cm", font=font("bold", 15), fill=BLACK)
    base_y, max_h = 432, 104
    peak = max([x.get("snow_cm") or 0 for x in past] + [5])
    bw, gap = 36, 14
    for i, day in enumerate(past):
        x = 26 + i * (bw + gap)
        v = day.get("snow_cm") or 0
        h = int(max_h * v / peak)
        if h:
            d.rectangle([x, base_y - h, x + bw, base_y], fill=BLACK)
        if v >= 0.5:
            d.text((x + bw // 2, base_y - h - 4), fmt_cm(v), font=font("bold", 14), fill=BLACK, anchor="ms")
        dd = parse_date(day["date"])
        d.text((x + bw // 2, base_y + 6), dd.strftime("%a")[0], font=font("regular", 14), fill=BLACK, anchor="mt")
    d.line([20, base_y, 380, base_y], fill=BLACK, width=2)

    d.line([400, 284, 400, 450], fill=BLACK, width=1)

    # --- Right side: totals & season --------------------------------------
    rx = 420
    nxt = sum(x["snow_cm"] for x in report.get("snow_forecast", [])
              if parse_date(x.get("date")) and parse_date(x["date"]) >= today)
    d.text((rx, 284), "SNOW NEXT 8 DAYS", font=font("bold", 15), fill=BLACK)
    f_big = font("bold", 56)
    v = fmt_cm(nxt)
    d.text((rx - 2, 302), v, font=f_big, fill=BLACK)
    d.text((rx + text_w(d, v, f_big) + 6, 336), "cm", font=font("bold", 22), fill=BLACK)

    opening = parse_date(report.get("opening_date"))
    closing = parse_date(report.get("closing_date"))
    d.text((rx, 380), "SEASON", font=font("bold", 15), fill=BLACK)
    d.text((rx, 400), "%s – %s" % (mon_day(opening), mon_day(closing)), font=font("bold", 20), fill=BLACK)
    if opening and closing:
        if today < opening:
            note = "Opens in %d days" % (opening - today).days
        elif today <= closing:
            note = "Day %d of %d" % ((today - opening).days + 1, (closing - opening).days + 1)
        else:
            note = "Season over"
        d.text((rx, 428), note, font=font("regular", 17), fill=BLACK)

    footer(d, report, 2, now)
    return img


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def to_1bit(img: Image.Image) -> Image.Image:
    """Hard threshold (no dithering) so text stays crisp; the sketch is pre-dithered."""
    return img.point(lambda p: 255 if p >= 128 else 0).convert("1", dither=Image.NONE)


def render_page(report: Dict[str, Any], page: int, now: datetime) -> Image.Image:
    img = render_conditions(report, now) if page == 1 else render_outlook(report, now)
    return to_1bit(img)


def frame_bytes(img1: Image.Image) -> bytes:
    """800*480/8 = 48,000 bytes, row-major, MSB first, 1 = white (GxEPD2 convention)."""
    assert img1.mode == "1" and img1.size == (W, H)
    return img1.tobytes()
