"""Draws the charts in docs/ from the benchmark results:

- benchmark-{light,dark}.svg from benches/results.json (bench_router.py)
- load-{light,dark}.svg from benches/load_results.json (load_test.py)
- servers-{light,dark}.svg from benches/servers_results.json (servers.py)

Run: uv run python benches/plot.py
"""

import json
import math
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
    desc = "; ".join(
        f"{label(r)}{' cached' if 'cached' in r else ''}: {duration(timings[r][10_000])}" for r in uncached + cached
    )
    out.append(f"<desc>Median time per lookup with 10,000 routes. {desc}.</desc>")
    out.append(f'<rect width="{WIDTH}" height="{height}" rx="8" fill="{t["surface"]}"/>')

    # Title, subtitle, legend
    out.append(
        f'<text x="20" y="32" font-size="16" font-weight="600" fill="{t["primary"]}">Time per route lookup (lower is better, log scale)</text>'
    )
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
    out.append(
        f'<line x1="{PLOT_LEFT}" y1="{axis_y}" x2="{PLOT_RIGHT}" y2="{axis_y}" stroke="{t["axis"]}" stroke-width="1"/>'
    )

    for kind, value, row_y in rows:
        if kind == "header":
            out.append(
                f'<text x="20" y="{row_y + 4}" font-size="12" font-weight="600" fill="{t["secondary"]}">{value}</text>'
            )
            continue
        router = value
        is_korvex = router.startswith("korvex")
        weight = ' font-weight="600"' if is_korvex else ""
        ink = t["primary"] if is_korvex else t["secondary"]
        out.append(f'<text x="32" y="{row_y + 4}" font-size="13"{weight} fill="{ink}">{label(router)}</text>')
        xs = [x_of(timings[router][n]) for n in ROUTE_COUNTS]
        out.append(
            f'<line x1="{min(xs):.1f}" y1="{row_y}" x2="{max(xs):.1f}" y2="{row_y}" stroke="{t["axis"]}" stroke-width="2" stroke-linecap="round"/>'
        )
        for x, color in zip(xs, t["dots"]):
            out.append(
                f'<circle cx="{x:.1f}" cy="{row_y}" r="5" fill="{color}" stroke="{t["surface"]}" stroke-width="2"/>'
            )
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


LOAD_CLIENTS_INDEX = 3  # 200 clients, see CONCURRENCY in load_test.py
LOAD_NAMES = {"fastapi": "FastAPI", "blacksheep": "BlackSheep"}
WORST_CASE_ONLY = {"blacksheep"}  # sorts routes itself: the tested one always comes last
LOAD_MAX = 40_000


def thousands(value):
    return f"{value / 1000:.1f}k"


def draw_load(results, machine, theme):
    """Requests per second per framework: one dot for routers where route
    order doesn't matter, a worst-to-best range for those where it does."""
    t = THEMES[theme]
    light, dark = t["dots"][0], t["dots"][2]

    def rps(run):
        return results[run]["ramp"][LOAD_CLIENTS_INDEX]["requests_per_second"]

    frameworks = [run for run in results if "," not in run]
    best = {f: rps(f"{f}, route first") if f"{f}, route first" in results else rps(f) for f in frameworks}
    frameworks.sort(key=lambda f: -best[f])

    def x_of_rps(value):
        return PLOT_LEFT + value / LOAD_MAX * (PLOT_RIGHT - PLOT_LEFT)

    top = 112
    axis_y = top + ROW_HEIGHT * len(frameworks)
    height = axis_y + 100
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" role="img">',
        "<title>Requests per second per framework on one CPU core, higher is better</title>",
        "<desc>Best case, 200 clients: "
        + "; ".join(f"{LOAD_NAMES.get(f, f.capitalize())}: {best[f]:,.0f}" for f in frameworks)
        + ".</desc>",
        f'<rect width="{WIDTH}" height="{height}" rx="8" fill="{t["surface"]}"/>',
        f'<text x="20" y="32" font-size="16" font-weight="600" fill="{t["primary"]}">'
        "Requests per second on one CPU core (higher is better)</text>",
        f'<text x="20" y="54" font-size="12" fill="{t["secondary"]}">'
        "Same app and server for all (uvicorn, 1 worker, 1,000 routes), 200 clients, random URLs. No request failed.</text>",
    ]
    legend = [(dark, "Best case"), (light, "Worst case: route declared after the 1,000 others")]
    legend_x = 20
    for color, text in legend:
        out.append(f'<circle cx="{legend_x + 5}" cy="78" r="5" fill="{color}"/>')
        out.append(f'<text x="{legend_x + 15}" y="82" font-size="12" fill="{t["secondary"]}">{text}</text>')
        legend_x += 30 + 6.6 * len(text)

    for value in range(0, LOAD_MAX + 1, 10_000):
        x = x_of_rps(value)
        out.append(
            f'<line x1="{x:.1f}" y1="{top - 16}" x2="{x:.1f}" y2="{axis_y}" stroke="{t["grid"]}" stroke-width="1"/>'
        )
        tick = "0" if value == 0 else f"{value // 1000}k"
        out.append(
            f'<text x="{x:.1f}" y="{axis_y + 18}" font-size="11" text-anchor="middle" fill="{t["muted"]}">{tick}</text>'
        )
    out.append(
        f'<line x1="{PLOT_LEFT}" y1="{axis_y}" x2="{PLOT_RIGHT}" y2="{axis_y}" stroke="{t["axis"]}" stroke-width="1"/>'
    )

    for i, framework in enumerate(frameworks):
        y = top + i * ROW_HEIGHT
        is_korvex = framework == "korvex"
        weight = ' font-weight="600"' if is_korvex else ""
        ink = t["primary"] if is_korvex else t["secondary"]
        name = LOAD_NAMES.get(framework, framework.capitalize())
        out.append(f'<text x="20" y="{y + 4}" font-size="13"{weight} fill="{ink}">{name}</text>')
        value_label = thousands(best[framework])
        best_color = light if framework in WORST_CASE_ONLY else dark
        if f"{framework}, route first" in results:
            worst = rps(framework)
            out.append(
                f'<line x1="{x_of_rps(worst):.1f}" y1="{y}" x2="{x_of_rps(best[framework]):.1f}" y2="{y}" '
                f'stroke="{t["axis"]}" stroke-width="2" stroke-linecap="round"/>'
            )
            out.append(
                f'<circle cx="{x_of_rps(worst):.1f}" cy="{y}" r="5" fill="{light}" stroke="{t["surface"]}" stroke-width="2"/>'
            )
            value_label = f"{thousands(worst)} to {value_label}"
        out.append(
            f'<circle cx="{x_of_rps(best[framework]):.1f}" cy="{y}" r="5" fill="{best_color}" stroke="{t["surface"]}" stroke-width="2"/>'
        )
        out.append(f'<text x="{PLOT_RIGHT + 16}" y="{y + 4}" font-size="12"{weight} fill="{ink}">{value_label}</text>')

    out.append(
        f'<text x="20" y="{axis_y + 46}" font-size="12" fill="{t["secondary"]}">'
        "Starlette, FastAPI and Django try routes one by one, so the route&#8217;s position matters.</text>"
    )
    out.append(
        f'<text x="20" y="{axis_y + 64}" font-size="12" fill="{t["secondary"]}">'
        "BlackSheep does too, but sorts routes itself: here the tested route always comes last.</text>"
    )
    cpu = machine.get("cpu", {}).get("brand_raw", "")
    out.append(
        f'<text x="20" y="{axis_y + 84}" font-size="11" fill="{t["muted"]}">Python {machine.get("python_version", "")}, {cpu}</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


SERVER_NAMES = {
    "korvex": "Korvex (built-in)",
    "granian": "Granian + Korvex app",
    "uvicorn": "uvicorn + Korvex app",
    "robyn": "Robyn",
}
SERVERS_MAX = 140_000


def draw_servers(results, machine, theme):
    """Requests per second per server, each with its best worker count,
    on the same 4 cores: a light dot for 200 clients, a dark one for 1,000."""
    t = THEMES[theme]
    light, dark = t["dots"][0], t["dots"][2]

    def total(runs):
        return sum(run["requests_per_second"] for run in runs)

    best = {}  # server -> (label, runs), keeping the config with the most req/s over both loads
    for label, runs in results.items():
        server = label.split(",")[0]
        if server not in best or total(runs) > total(best[server][1]):
            best[server] = (label, runs)
    servers = sorted(best, key=lambda name: -best[name][1][-1]["requests_per_second"])

    def x_of_rps(value):
        return PLOT_LEFT + value / SERVERS_MAX * (PLOT_RIGHT - PLOT_LEFT)

    top = 112
    axis_y = top + ROW_HEIGHT * len(servers)
    height = axis_y + 82
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" role="img">',
        "<title>Requests per second per server on the same 4 CPU cores, higher is better</title>",
        "<desc>"
        + "; ".join(
            f"{SERVER_NAMES[name]}: {best[name][1][0]['requests_per_second']:,.0f} with 200 clients, "
            f"{best[name][1][-1]['requests_per_second']:,.0f} with 1,000"
            for name in servers
        )
        + ".</desc>",
        f'<rect width="{WIDTH}" height="{height}" rx="8" fill="{t["surface"]}"/>',
        f'<text x="20" y="32" font-size="16" font-weight="600" fill="{t["primary"]}">'
        "Requests per second on the same 4 CPU cores (higher is better)</text>",
        f'<text x="20" y="54" font-size="12" fill="{t["secondary"]}">'
        "Same app for all, each server with its best worker count. No request failed.</text>",
    ]
    legend_x = 20
    for color, text in [(light, "200 clients"), (dark, "1,000 clients")]:
        out.append(f'<circle cx="{legend_x + 5}" cy="78" r="5" fill="{color}"/>')
        out.append(f'<text x="{legend_x + 15}" y="82" font-size="12" fill="{t["secondary"]}">{text}</text>')
        legend_x += 30 + 6.6 * len(text)

    for value in range(0, SERVERS_MAX + 1, 20_000):
        x = x_of_rps(value)
        out.append(
            f'<line x1="{x:.1f}" y1="{top - 16}" x2="{x:.1f}" y2="{axis_y}" stroke="{t["grid"]}" stroke-width="1"/>'
        )
        tick = "0" if value == 0 else f"{value // 1000}k"
        out.append(
            f'<text x="{x:.1f}" y="{axis_y + 18}" font-size="11" text-anchor="middle" fill="{t["muted"]}">{tick}</text>'
        )
    out.append(
        f'<line x1="{PLOT_LEFT}" y1="{axis_y}" x2="{PLOT_RIGHT}" y2="{axis_y}" stroke="{t["axis"]}" stroke-width="1"/>'
    )

    for i, name in enumerate(servers):
        y = top + i * ROW_HEIGHT
        label, runs = best[name]
        is_korvex = name == "korvex"
        weight = ' font-weight="600"' if is_korvex else ""
        ink = t["primary"] if is_korvex else t["secondary"]
        out.append(f'<text x="20" y="{y + 4}" font-size="13"{weight} fill="{ink}">{SERVER_NAMES[name]}</text>')
        xs = [x_of_rps(run["requests_per_second"]) for run in runs]
        out.append(
            f'<line x1="{min(xs):.1f}" y1="{y}" x2="{max(xs):.1f}" y2="{y}" stroke="{t["axis"]}" stroke-width="2" stroke-linecap="round"/>'
        )
        for x, color in zip(xs, [light, dark]):
            out.append(f'<circle cx="{x:.1f}" cy="{y}" r="5" fill="{color}" stroke="{t["surface"]}" stroke-width="2"/>')
        value_label = " / ".join(thousands(run["requests_per_second"]) for run in runs)
        out.append(f'<text x="{PLOT_RIGHT + 16}" y="{y + 4}" font-size="12"{weight} fill="{ink}">{value_label}</text>')

    out.append(
        f'<text x="20" y="{axis_y + 46}" font-size="12" fill="{t["secondary"]}">'
        "uvicorn and Granian run the Korvex ASGI app; Robyn runs its own version with its own server.</text>"
    )
    cpu = machine.get("cpu", {}).get("brand_raw", "")
    out.append(
        f'<text x="20" y="{axis_y + 66}" font-size="11" fill="{t["muted"]}">Python {machine.get("python_version", "")}, {cpu}</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


def main():
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    timings, machine = load("benches/results.json")
    for theme in THEMES:
        (docs / f"benchmark-{theme}.svg").write_text(draw(timings, machine, theme) + "\n")
    for router, by_count in sorted(timings.items(), key=lambda item: item[1][10_000]):
        print(f"{router:20}" + "".join(f"{duration(by_count[n]):>12}" for n in ROUTE_COUNTS))

    server_results = Path("benches/servers_results.json")
    if server_results.exists():
        results = json.loads(server_results.read_text())
        for theme in THEMES:
            (docs / f"servers-{theme}.svg").write_text(draw_servers(results, machine, theme) + "\n")

    load_results = Path("benches/load_results.json")
    if load_results.exists():
        results = json.loads(load_results.read_text())
        for theme in THEMES:
            (docs / f"load-{theme}.svg").write_text(draw_load(results, machine, theme) + "\n")


if __name__ == "__main__":
    main()
