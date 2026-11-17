"""
Writes results/d2/RESULTS.md (tables + key findings) and results/d2/aggregate.csv from summary.csv.

  python3 experiments/report.py [results/d2]
"""
import csv, os, statistics, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "d2")
from run_experiments import SCENARIOS

LABEL = {"plain": "B0 plain routing", "static": "B1 static priority", "full": "DeadlineCast (ours)",
         "full_noretx": "Ours, no retransmission", "full_nostandby": "Ours, no standby"}
COLS = [("delivery_pct", "Delivered %", 1), ("deadline_miss_pct", "Deadline miss %", 1),
        ("lat_avg_ms", "Avg ms", 1), ("lat_p95_ms", "p95 ms", 1), ("lat_max_ms", "Max ms", 1),
        ("e2e_p95_ms", "End-to-end p95 ms", 1), ("first_try_pct", "First try %", 0),
        ("retransmissions", "Retx", 1), ("via_standby", "Via standby", 1), ("fake_reached", "Fakes reached", 0),
        ("quarantines", "Quarantines", 1), ("recovery_ms", "Reroute ms", 1), ("flood_mbps", "Flood Mbps", 1)]


def mean(rows, key):
    vals = []
    for r in rows:
        try:
            v = float(r[key])
            if v == v:
                vals.append(v)
        except (KeyError, ValueError):
            pass
    return statistics.mean(vals) if vals else None


def f(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def main():
    rows = list(csv.DictReader(open(os.path.join(OUT, "summary.csv"))))
    groups = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["config"]), []).append(r)

    with open(os.path.join(OUT, "aggregate.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "config", "runs"] + [k for k, _, _ in COLS])
        for (s, c), g in groups.items():
            w.writerow([s, c, len(g)] + [f(mean(g, k), 3) for k, _, _ in COLS])

    def m(s, c, k):
        return mean(groups.get((s, c), []), k)

    lines = ["# D2 results", "",
             f"Generated from `{os.path.relpath(os.path.join(OUT, 'summary.csv'), ROOT)}` "
             f"({len(rows)} runs). Each run: 20 CRITICAL alerts, 0.5 s apart, to 5 subscribers; "
             "deadline 1000 ms. Values are means over the repetitions.", "",
             "## Key findings", ""]
    findings = []

    def cmp(s, metric, better_low, unit, what):
        a, b = m(s, "static", metric), m(s, "full", metric)
        p = m(s, "plain", metric)
        if a is None or b is None:
            return
        txt = f"**{what}:** DeadlineCast {f(b)}{unit}"
        txt += f", static priority {f(a)}{unit}" + (f", plain routing {f(p)}{unit}" if p is not None else "")
        findings.append(txt + ".")

    cmp("congestion", "deadline_miss_pct", True, "% deadline misses", "Congestion (12 Mbps flood)")
    cmp("abuse", "deadline_miss_pct", True, "% deadline misses", "Fake-urgent flood (DSCP 46 abuse)")
    cmp("linkfail", "delivery_pct", False, "% delivered", "Link failure")
    if m("linkfail", "full", "recovery_ms") is not None:
        findings.append(f"**Reroute time after the link failure:** {f(m('linkfail', 'full', 'recovery_ms'))} ms "
                        "(controller detects the port-down event and reinstalls all routes).")
    if m("loss", "full", "delivery_pct") is not None:
        findings.append(f"**10% packet loss:** {f(m('loss', 'full', 'delivery_pct'))}% delivered with "
                        f"retransmission vs {f(m('loss', 'full_noretx', 'delivery_pct'))}% without.")
    if m("failover", "full", "delivery_pct") is not None:
        findings.append(f"**Primary server crash:** {f(m('failover', 'full', 'delivery_pct'))}% delivered with the "
                        f"standby vs {f(m('failover', 'full_nostandby', 'delivery_pct'))}% without; "
                        f"{f(m('failover', 'full', 'via_standby'))} alerts per run went through the standby.")
    if m("fakealert", "full", "fake_reached") is not None:
        findings.append(f"**Fake-alert attack:** {f(m('fakealert', 'full', 'fake_reached'), 0)} fake alerts reached "
                        f"subscribers with DeadlineCast vs {f(m('fakealert', 'static', 'fake_reached'), 0)} with "
                        "static priority (where HMAC still rejected them at the application).")
    lines += [f"- {x}" for x in findings] + [""]

    for s, spec in SCENARIOS.items():
        cfgs = [c for c in spec["configs"] if (s, c) in groups]
        if not cfgs:
            continue
        lines += [f"## {s}: {spec['desc']}", "",
                  "| Configuration | Runs | " + " | ".join(h for _, h, _ in COLS) + " |",
                  "|---|---|" + "---|" * len(COLS)]
        for c in cfgs:
            g = groups[(s, c)]
            lines.append(f"| {LABEL.get(c, c)} | {len(g)} | " +
                         " | ".join(f(mean(g, k), nd) for k, _, nd in COLS) + " |")
        lines.append("")

    figs = os.path.join(OUT, "figs")
    if os.path.isdir(figs):
        lines += ["## Charts", ""]
        for name in sorted(os.listdir(figs)):
            lines += [f"![{name}](figs/{name})", ""]
    lines += ["## Column guide", "",
              "- **Delivered %**: unique alerts received / (alerts sent x 5 subscribers).",
              "- **Deadline miss %**: alerts not delivered, or delivered later than their deadline (1000 ms).",
              "- **Avg / p95 / Max ms**: server-to-subscriber latency of delivered alerts.",
              "- **End-to-end p95 ms**: operator press to subscriber screen (includes failover waits).",
              "- **First try %**: delivered without needing a retransmission.",
              "- **Fakes reached**: fake alerts that reached a subscriber program (rejected by HMAC or shown).",
              "- **Reroute ms**: time from the link-down event to all routes being reinstalled.",
              "- **Flood Mbps**: background traffic that actually got through (the cost side of prioritisation).", ""]
    with open(os.path.join(OUT, "RESULTS.md"), "w") as fh:
        fh.write("\n".join(lines))
    print("written", os.path.join(OUT, "RESULTS.md"), "and aggregate.csv")


if __name__ == "__main__":
    main()
