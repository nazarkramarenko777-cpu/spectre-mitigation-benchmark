"""Summarise the raw measurements written by bench.py.

    python analyze.py

Reads every results/raw_*.csv (warm-up rounds are ignored), and writes:
  results/summary.csv   one row per workload: medians, spread, slowdown, 95% CI
  results/summary.md    the same as a Markdown table (paste into the paper)
  results/slowdown.svg  bar chart of the slowdown with 95% CI whiskers
  results/noise_check.csv  ON vs ON (first vs last ON session), if there are two

Only sessions that finished cleanly are used (matching system JSON with
'finished', no 'warning', not run with --force); skipped files are listed.
All clean ON sessions are pooled for the ON median. "Beyond noise" = the CI
excludes 0 AND the slowdown is larger than the ON-vs-ON difference.

Slowdown = (median_on - median_off) / median_off * 100 %.
Positive = the workload is slower with mitigations on.
The 95% confidence interval comes from a percentile bootstrap of the ratio
of medians (10 000 resamples, fixed seed, so the numbers are reproducible).
"""

import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
BOOT = 10_000
SEED = 7

ORDER = ["file_create_delete", "file_stat", "zip_extract", "process_spawn",
         "thread_pingpong", "cpu_python", "cpu_sha256"]


def session_ok(csv_path):
    """Use a session only if it finished cleanly: its system JSON exists,
    has 'finished', no 'warning', and was not run with --force."""
    js = csv_path.with_name(csv_path.name.replace("raw_", "system_", 1)[:-4] + ".json")
    if not js.exists():
        return False, "no system JSON (run was interrupted?)"
    try:
        info = json.loads(js.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return False, f"unreadable system JSON ({exc})"
    if "finished" not in info:
        return False, "not finished"
    if info.get("warning"):
        return False, info["warning"]
    if info.get("forced"):
        return False, "run with --force"
    return True, ""


def valid_files(pattern="raw_*.csv"):
    good, skipped = [], []
    for f in sorted(RESULTS.glob(pattern)):
        ok, why = session_ok(f)
        (good if ok else skipped).append((f, why))
    return [f for f, _ in good], skipped


def load():
    data = defaultdict(list)  # (workload, condition) -> [seconds]
    groups, sessions = {}, defaultdict(set)
    files, skipped = valid_files()
    for f, why in skipped:
        print(f"SKIPPED {f.name}: {why}")
    if not files:
        sys.exit(f"No usable raw_*.csv files in {RESULTS}. Run bench.py first.")
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["warmup"] == "1":
                    continue
                key = (row["workload"], row["condition"])
                data[key].append(float(row["seconds"]))
                groups[row["workload"]] = row["group"]
                sessions[row["condition"]].add(row["session"])
    return data, groups, sessions, files


def quartiles(xs):
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], q[2]


def boot_ci(on, off, rng):
    ratios = []
    for _ in range(BOOT):
        a = statistics.median(rng.choices(on, k=len(on)))
        b = statistics.median(rng.choices(off, k=len(off)))
        ratios.append(a / b)
    ratios.sort()
    lo = ratios[int(0.025 * BOOT)]
    hi = ratios[int(0.975 * BOOT) - 1]
    return (lo - 1) * 100, (hi - 1) * 100


def summarise(data, groups):
    rng = random.Random(SEED)
    rows = []
    names = [w for w in ORDER if w in groups] + sorted(set(groups) - set(ORDER))
    for w in names:
        row = {"workload": w, "group": groups[w]}
        for cond in ("on", "off"):
            xs = data.get((w, cond), [])
            row[f"n_{cond}"] = len(xs)
            if len(xs) >= 2:
                q1, q3 = quartiles(xs)
                row[f"median_{cond}_s"] = statistics.median(xs)
                row[f"iqr_{cond}_s"] = q3 - q1
                row[f"cv_{cond}_pct"] = statistics.stdev(xs) / statistics.mean(xs) * 100
        on, off = data.get((w, "on"), []), data.get((w, "off"), [])
        if len(on) >= 2 and len(off) >= 2:
            row["slowdown_pct"] = (statistics.median(on) / statistics.median(off) - 1) * 100
            row["ci95_low_pct"], row["ci95_high_pct"] = boot_ci(on, off, rng)
            # 0 inside the interval = no difference we can claim
            row["significant"] = "yes" if row["ci95_low_pct"] > 0 or row["ci95_high_pct"] < 0 else "no"
        rows.append(row)
    return rows


def fmt(v, nd=3):
    return "" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def write_csv(rows):
    cols = ["workload", "group", "n_on", "n_off", "median_on_s", "median_off_s",
            "iqr_on_s", "iqr_off_s", "cv_on_pct", "cv_off_pct",
            "slowdown_pct", "ci95_low_pct", "ci95_high_pct", "significant", "beyond_noise"]
    with open(RESULTS / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(cols)
        for r in rows:
            wr.writerow([fmt(r.get(c), 4) for c in cols])


def write_md(rows, sessions):
    lines = [
        "| Workload | Group | Median ON (s) | Median OFF (s) | Slowdown (%) | 95% CI (%) | Beyond noise | n ON / OFF |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ci = ""
        if "ci95_low_pct" in r:
            ci = f"{r['ci95_low_pct']:+.1f} to {r['ci95_high_pct']:+.1f}"
        sd = f"{r['slowdown_pct']:+.1f}" if "slowdown_pct" in r else ""
        lines.append(f"| {r['workload']} | {r['group']} | {fmt(r.get('median_on_s'))} | "
                     f"{fmt(r.get('median_off_s'))} | {sd} | {ci} | {r.get('beyond_noise', '')} | "
                     f"{r['n_on']} / {r['n_off']} |")
    lines.append("")
    lines.append("Sessions: " + "; ".join(f"{c}: {', '.join(sorted(s))}" for c, s in sorted(sessions.items())))
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def noise_floor():
    """A/A check: compare the first and the last 'on' session with each other.

    Both are the same condition, so any difference is run-to-run noise
    (drift, background activity). A real mitigation effect should be
    clearly larger than this.
    """
    per = defaultdict(lambda: defaultdict(list))  # session -> workload -> [s]
    for f in valid_files("raw_on_*.csv")[0]:
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["warmup"] != "1":
                    per[row["session"]][row["workload"]].append(float(row["seconds"]))
    if len(per) < 2:
        return None
    first, last = sorted(per)[0], sorted(per)[-1]
    lines = ["", f"A/A noise check (ON vs ON): {last} vs {first}",
             "| Workload | Median first ON (s) | Median last ON (s) | Difference (%) |", "|---|---|---|---|"]
    rows = []
    diffs = {}
    for w in ORDER:
        a, b = per[first].get(w), per[last].get(w)
        if a and b:
            d = (statistics.median(b) / statistics.median(a) - 1) * 100
            rows.append([w, statistics.median(a), statistics.median(b), d])
            diffs[w] = d
            lines.append(f"| {w} | {statistics.median(a):.3f} | {statistics.median(b):.3f} | {d:+.1f} |")
    with open(RESULTS / "noise_check.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["workload", "median_first_on_s", "median_last_on_s", "difference_pct",
                     "first_on_session", "last_on_session"])
        for r in rows:
            wr.writerow([r[0], f"{r[1]:.4f}", f"{r[2]:.4f}", f"{r[3]:.2f}", first, last])
    return lines, diffs


def write_svg(rows):
    rows = [r for r in rows if "slowdown_pct" in r]
    if not rows:
        return
    vals = [v for r in rows for v in (r["ci95_low_pct"], r["ci95_high_pct"], r["slowdown_pct"])]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    pad = max(1.0, (hi - lo) * 0.1)
    lo, hi = lo - pad, hi + pad
    W, left, right, top, bar_h, gap = 760, 170, 30, 50, 26, 14
    H = top + len(rows) * (bar_h + gap) + 50
    x = lambda v: left + (v - lo) / (hi - lo) * (W - left - right)
    colour = {"kernel-heavy": "#c0504d", "control": "#7f7f7f"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Arial" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{left}" y="22" font-size="14" font-weight="bold">Slowdown with mitigations ON vs OFF '
           f'(median, 95% bootstrap CI)</text>']
    y0, y1 = top - 8, top + len(rows) * (bar_h + gap)
    step = 1 if hi - lo <= 12 else (2 if hi - lo <= 25 else 5 if hi - lo <= 60 else 10)
    t = int(lo // step) * step
    while t <= hi:
        if t >= lo:
            out.append(f'<line x1="{x(t):.1f}" y1="{y0}" x2="{x(t):.1f}" y2="{y1}" stroke="#e5e5e5"/>')
            out.append(f'<text x="{x(t):.1f}" y="{y1 + 16}" text-anchor="middle">{t:+g}%</text>')
        t += step
    out.append(f'<line x1="{x(0):.1f}" y1="{y0}" x2="{x(0):.1f}" y2="{y1}" stroke="black"/>')
    for i, r in enumerate(rows):
        y = top + i * (bar_h + gap)
        v = r["slowdown_pct"]
        xa, xb = sorted((x(0), x(v)))
        out.append(f'<text x="{left - 8}" y="{y + bar_h / 2 + 4}" text-anchor="end">{r["workload"]}</text>')
        out.append(f'<rect x="{xa:.1f}" y="{y}" width="{max(xb - xa, 1):.1f}" height="{bar_h}" '
                   f'fill="{colour.get(r["group"], "#4f81bd")}"/>')
        cy = y + bar_h / 2
        l, h = x(r["ci95_low_pct"]), x(r["ci95_high_pct"])
        out.append(f'<line x1="{l:.1f}" y1="{cy}" x2="{h:.1f}" y2="{cy}" stroke="black" stroke-width="1.5"/>')
        for e in (l, h):
            out.append(f'<line x1="{e:.1f}" y1="{cy - 6}" x2="{e:.1f}" y2="{cy + 6}" stroke="black" stroke-width="1.5"/>')
        out.append(f'<text x="{max(xb, h) + 6:.1f}" y="{cy + 4}">{v:+.1f}%</text>')
    ly = H - 14
    out.append(f'<rect x="{left}" y="{ly - 10}" width="12" height="12" fill="{colour["kernel-heavy"]}"/>'
               f'<text x="{left + 18}" y="{ly}">kernel-heavy</text>')
    out.append(f'<rect x="{left + 120}" y="{ly - 10}" width="12" height="12" fill="{colour["control"]}"/>'
               f'<text x="{left + 138}" y="{ly}">control (pure computation)</text>')
    out.append("</svg>")
    (RESULTS / "slowdown.svg").write_text("\n".join(out), encoding="utf-8")


def main():
    global RESULTS
    if len(sys.argv) > 1:  # optional: another results folder
        RESULTS = Path(sys.argv[1])
    for old in ("summary.csv", "summary.md", "slowdown.svg", "noise_check.csv"):
        (RESULTS / old).unlink(missing_ok=True)  # outputs of this script only, rebuilt below
    data, groups, sessions, files = load()
    rows = summarise(data, groups)
    nf = noise_floor()
    diffs = nf[1] if nf else {}
    for r in rows:
        # A claim needs: CI excludes 0 AND the effect is bigger than ON-vs-ON noise.
        if "slowdown_pct" in r and r["workload"] in diffs:
            r["beyond_noise"] = "yes" if (r["significant"] == "yes"
                                          and abs(r["slowdown_pct"]) > abs(diffs[r["workload"]])) else "no"
    write_csv(rows)
    lines = write_md(rows, sessions)
    write_svg(rows)
    if nf:
        lines += nf[0]
        (RESULTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Read {len(files)} file(s).")
    print("\n".join(lines))
    missing = [c for c in ("on", "off") if c not in sessions]
    written = ["summary.csv", "summary.md"]
    if not missing:
        written.append("slowdown.svg")
    if nf:
        written.append("noise_check.csv")
    if missing:
        print(f"\nNo data yet for condition(s): {', '.join(missing)}. Slowdown needs both.")
    print(f"\nWritten in {RESULTS}: {', '.join(written)}")
    try:
        import mitigation_status
        if mitigation_status.get_status()["overall"] == "off":
            print("\n!!! WARNING: mitigations are OFF on this PC right now. "
                  "Run 4_mitigations_RESTORE_admin.bat and reboot. !!!")
    except Exception:
        pass


if __name__ == "__main__":
    main()
