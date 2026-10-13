"""Command line entry point: ``python -m wingstats <file.tcx|file.gpx|dir>``."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .analysis import Params, analyse
from .parsers import load_track, read_csv_summary
from .report import json_report, text_report
from .track import resample


def pick_input(path: Path) -> Path:
    if path.is_file():
        return path
    for ext in (".tcx", ".gpx"):
        found = sorted(path.glob(f"*{ext}"))
        if found:
            return found[0]
    raise SystemExit(f"No .tcx or .gpx file found in {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wingstats", description="Wingfoil statistics from a Garmin Connect export")
    ap.add_argument("input", type=Path, help="TCX or GPX file, or a directory containing one")
    ap.add_argument("--out", type=Path, default=Path("out"), help="output directory (default: out/)")
    ap.add_argument("--csv", type=Path, help="Garmin lap CSV for cross-checking totals (auto-detected next to the input)")
    ap.add_argument("--wind-from", type=float, help="wind direction in degrees FROM (overrides drift estimate)")
    ap.add_argument("--fly-on", type=float, default=Params.fly_on_kmh, help="km/h to count as flying")
    ap.add_argument("--fly-off", type=float, default=Params.fly_off_kmh, help="km/h below which flight is lost")
    ap.add_argument("--stop", type=float, default=Params.stop_kmh, help="km/h below which you are in the water")
    ap.add_argument("--min-flight", type=int, default=Params.min_flight_s, help="shortest flight in seconds")
    ap.add_argument("--water-stop", type=int, default=Params.water_stop_s,
                    help="a full stop with a longer recovery than this (seconds) is reported as a long stop, likely in the water")
    ap.add_argument("--no-trim", action="store_true",
                    help="keep the whole recording (by default the walk/swim out before the first flight and back after the last is cut)")
    ap.add_argument("--no-html", action="store_true", help="skip the HTML report")
    ap.add_argument("--open", action="store_true", help="open the HTML report in your web browser when done")
    ap.add_argument("--quiet", action="store_true", help="do not print the text report")
    args = ap.parse_args(argv)

    src = pick_input(args.input)
    raw = load_track(src)
    track = resample(raw)
    params = Params(fly_on_kmh=args.fly_on, fly_off_kmh=args.fly_off, stop_kmh=args.stop,
                    min_flight_s=args.min_flight, wind_from_deg=args.wind_from, water_stop_s=args.water_stop,
                    trim=not args.no_trim)
    result = analyse(track, params)

    csv_path = args.csv or src.with_suffix(".csv")
    summary = read_csv_summary(csv_path) if csv_path.exists() else None

    args.out.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    (args.out / f"{stem}.txt").write_text(text_report(result, summary), encoding="utf-8")
    (args.out / f"{stem}.json").write_text(json_report(result, summary), encoding="utf-8")
    written = [args.out / f"{stem}.txt", args.out / f"{stem}.json"]
    if not args.no_html:
        from .html_report import html_report
        html_path = args.out / f"{stem}.html"
        html_path.write_text(html_report(result, summary), encoding="utf-8")
        written.append(html_path)

    if not args.quiet:
        print(text_report(result, summary))
    print("\nWrote: " + ", ".join(str(p) for p in written), file=sys.stderr)

    if args.open and not args.no_html:
        url = html_path.resolve().as_uri()
        if webbrowser.open(url):
            print(f"Opened {url}", file=sys.stderr)
        else:
            print(f"Could not find a web browser; open {url} manually", file=sys.stderr)
    elif args.open:
        print("--open ignored because --no-html was given", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
