"""Draws docs/benchmark-{light,dark}.svg from a bench_router.py JSON report.

Run: uv run python benches/plot.py benches/results.json
"""

import json
import math
import sys
from pathlib import Path

ROUTE_COUNTS = [100, 1_000, 10_000]
NAMES = {"starlette": "Starlette / FastAPI"}
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "primary": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "dots": ["#86b6ef", "#2a78d6", "#104281"],  # 100, 1k, 10k routes
    },
    "dark": {
        "surface": "#1a1a19",
        "primary": "#f0efec",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "dots": ["#184f95", "#3987e5", "#9ec5f4"],
    },
}

WIDTH = 760
PLOT_LEFT, PLOT_RIGHT = 190, 650
ROW_HEIGHT = 30
X_MIN, X_MAX = 1e-8, 1e-2  # 10 ns to 10 ms


def load(report_path):
    """{router: {route_count: seconds per lookup}}"""
    report = json.loads(Path(report_path).read_text())
    timings = {}
    for bench in report["benchmarks"]:
        info = bench["extra_info"]
        timings.setdefault(info["router"], {})[info["routes"]] = bench["stats"]["median"] / info["lookups"]
    return timings, report["machine_info"]


def x_of(seconds):
    fraction = (math.log10(seconds) - math.log10(X_MIN)) / (math.log10(X_MAX) - math.log10(X_MIN))
    return PLOT_LEFT + fraction * (PLOT_RIGHT - PLOT_LEFT)


def duration(seconds):
    """1.4 ms, 3.5 µs, 372 ns"""
    for unit, scale in (("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)):
        if seconds >= scale or unit == "ns":
            value = seconds / scale
            return f"{value:.2g} {unit}" if value < 10 else f"{value:.0f} {unit}"


def label(router):
    base = router.replace(" (cached)", "")
    return NAMES.get(base, base.capitalize())


def draw(timings, machine, theme):
    t = THEMES[theme]
    uncached = sorted((r for r in timings if "cached" not in r), key=lambda r: timings[r][10_000])
    cached = sorted((r for r in timings if "cached" in r), key=lambda r: timings[r][10_000])
    groups = [("Actual matching (no cache)", uncached), ("Same URLs repeated (router's own LRU cache)", cached)]

    out = []
    y = 104
    rows = []
    for title, routers in groups:
        rows.append(("header", title, y))
        y += 26
        for router in routers:
            rows.append(("row", router, y))
            y += ROW_HEIGHT
        y += 12
    axis_y = y
    height = axis_y + 82

    cpu = machine.get("cpu", {}).get("brand_raw", "")
    python = machine.get("python_version", "")
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" role="img">'
    )
    out.append("<title>Route lookup time per router, log scale, lower is better</title>")
    desc = "; ".join(f"{label(r)}{' cached' if 'cached' in r else ''}: {duration(timings[r][10_000])}" for r in uncached + cached)
    out.append(f"<desc>Median time per lookup with 10,000 routes. {desc}.</desc>")
    out.append(f'<rect width="{WIDTH}" height="{height}" rx="8" fill="{t["surface"]}"/>')

    # Title, subtitle, legend
    out.append(f'<text x="20" y="32" font-size="16" font-weight="600" fill="{t["primary"]}">Time per route lookup (lower is better, log scale)</text>')
    out.append(
        f'<text x="20" y="54" font-size="12" fill="{t["secondary"]}">'
        "Median time per lookup. Every lookup is checked to find the right route.</text>"
    )
    legend_x = 20
    for color, count in zip(t["dots"], ROUTE_COUNTS):
        out.append(f'<circle cx="{legend_x + 5}" cy="78" r="5" fill="{color}"/>')
        text = f"{count:,} routes"
        out.append(f'<text x="{legend_x + 15}" y="82" font-size="12" fill="{t["secondary"]}">{text}</text>')
        legend_x += 26 + 7 * len(text)

    # Vertical gridlines and axis labels
    exponent = round(math.log10(X_MIN))
    while 10.0**exponent <= X_MAX * 1.0001:
        x = x_of(10.0**exponent)
        out.append(f'<line x1="{x:.1f}" y1="100" x2="{x:.1f}" y2="{axis_y}" stroke="{t["grid"]}" stroke-width="1"/>')
        out.append(
            f'<text x="{x:.1f}" y="{axis_y + 18}" font-size="11" text-anchor="middle" fill="{t["muted"]}">{duration(10.0**exponent)}</text>'
        )
        exponent += 1
    out.append(f'<line x1="{PLOT_LEFT}" y1="{axis_y}" x2="{PLOT_RIGHT}" y2="{axis_y}" stroke="{t["axis"]}" stroke-width="1"/>')

    for kind, value, row_y in rows:
        if kind == "header":
            out.append(f'<text x="20" y="{row_y + 4}" font-size="12" font-weight="600" fill="{t["secondary"]}">{value}</text>')
            continue
        router = value
        is_korvex = router == "korvex"
        weight = ' font-weight="600"' if is_korvex else ""
        ink = t["primary"] if is_korvex else t["secondary"]
        out.append(f'<text x="32" y="{row_y + 4}" font-size="13"{weight} fill="{ink}">{label(router)}</text>')
        xs = [x_of(timings[router][n]) for n in ROUTE_COUNTS]
        out.append(
            f'<line x1="{min(xs):.1f}" y1="{row_y}" x2="{max(xs):.1f}" y2="{row_y}" stroke="{t["axis"]}" stroke-width="2" stroke-linecap="round"/>'
        )
        for x, color in zip(xs, t["dots"]):
            out.append(f'<circle cx="{x:.1f}" cy="{row_y}" r="5" fill="{color}" stroke="{t["surface"]}" stroke-width="2"/>')
        out.append(
            f'<text x="{PLOT_RIGHT + 16}" y="{row_y + 4}" font-size="12"{weight} fill="{ink}">{duration(timings[router][10_000])}</text>'
        )

    out.append(
        f'<text x="20" y="{axis_y + 46}" font-size="12" fill="{t["secondary"]}">'
        "A single dot means the three overlap: lookup time doesn't grow with the number of routes.</text>"
    )
    out.append(f'<text x="20" y="{axis_y + 66}" font-size="11" fill="{t["muted"]}">Python {python}, {cpu}</text>')
    out.append(f'<text x="{PLOT_RIGHT + 16}" y="98" font-size="11" fill="{t["muted"]}">at 10,000</text>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    timings, machine = load(sys.argv[1] if len(sys.argv) > 1 else "benches/results.json")
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    for theme in THEMES:
        (docs / f"benchmark-{theme}.svg").write_text(draw(timings, machine, theme) + "\n")
    for router, by_count in sorted(timings.items(), key=lambda item: item[1][10_000]):
        print(f"{router:20}" + "".join(f"{duration(by_count[n]):>12}" for n in ROUTE_COUNTS))


if __name__ == "__main__":
    main()
