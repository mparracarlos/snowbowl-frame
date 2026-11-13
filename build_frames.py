"""
Builds the two e-ink dashboards as static files, for the frame to download.

Run by the scheduled GitHub job (.github/workflows/update.yml). Each run:
  1. loads the Arizona Snowbowl report from OnTheSnow (one page load),
  2. draws both dashboards,
  3. writes them to frames/ (the workflow then commits that folder).

Output
  frames/page1.bin, frames/page2.bin   48,000 bytes each: 800x480, 1 bit per pixel,
                                       MSB first, 1 = white. This is what the ESP32 reads.
  frames/preview1.png, preview2.png    the same pictures, for people
  frames/status.json                   when it ran and whether the fetch worked
  data/latest.json                     last good report (used if a later fetch fails)

If OnTheSnow can't be reached, the dashboards are redrawn from data/latest.json
with "OFFLINE" in the footer, and the script exits with code 2 so the job is
flagged as failed (GitHub emails you) while the frame keeps showing something.

Options
  --fixture FILE   use a saved HTML page instead of the network (for testing)
  --methods LIST   "urllib", "playwright" or "urllib,playwright" (default from
                   the SNOW_FETCH environment variable, else "urllib,playwright")
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import render
import snowbowl_data as sd

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, "frames")
LATEST = os.path.join(HERE, "data", "latest.json")
RUNS_PER_DAY = 3


def load_latest():
    try:
        with open(LATEST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def rotate_sketch(now: datetime):
    """A different sketch on every run (three a day), cycling through sketches/."""
    files = render.list_sketches()
    if not files:
        return None
    run_of_day = sum(1 for h in (9, 15) if now.hour >= h)  # morning / midday / evening
    return files[(now.timetuple().tm_yday * RUNS_PER_DAY + run_of_day) % len(files)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture")
    ap.add_argument("--methods", default=os.environ.get("SNOW_FETCH", "urllib,playwright"))
    args = ap.parse_args()

    now = datetime.now(sd.PHOENIX)
    error = None
    try:
        if args.fixture:
            with open(args.fixture, encoding="utf-8") as f:
                report = sd.parse_payload(sd.payload_from_html(f.read()))
            report["source"] = "fixture"
        else:
            report = sd.fetch_report(args.methods)
        os.makedirs(os.path.dirname(LATEST), exist_ok=True)
        with open(LATEST, "w") as f:
            json.dump(report, f, indent=1)
    except Exception as exc:  # noqa: BLE001 - fall back to the last good report
        error = str(exc)
        print("FETCH FAILED:", error, file=sys.stderr)
        report = load_latest()
        if report is None:
            print("No earlier report to fall back on; frames left unchanged.", file=sys.stderr)
            return 1
        report["_stale"] = True

    render.pick_sketch = rotate_sketch
    os.makedirs(FRAMES, exist_ok=True)
    for page in (1, 2):
        img = render.render_page(report, page, now)
        data = render.frame_bytes(img)
        assert len(data) == 48000, len(data)
        with open(os.path.join(FRAMES, "page%d.bin" % page), "wb") as f:
            f.write(data)
        img.save(os.path.join(FRAMES, "preview%d.png" % page))

    pow_day = sd.pow_pow(report, now.date())
    with open(os.path.join(FRAMES, "status.json"), "w") as f:
        json.dump({
            "built_at": now.isoformat(timespec="minutes"),
            "data_fetched_at": report.get("fetched_at"),
            "resort_report_updated": report.get("report_updated"),
            "source": report.get("source"),
            "stale": bool(report.get("_stale")),
            "pow_pow": pow_day,
            "error": error,
        }, f, indent=1)
    print("Built frames at %s (source: %s%s)" % (
        now.strftime("%Y-%m-%d %H:%M"), report.get("source"), ", STALE" if error else ""))
    return 2 if error else 0


if __name__ == "__main__":
    sys.exit(main())
