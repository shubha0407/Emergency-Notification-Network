"""
Draws the D2 result charts from results/d2/summary.csv and the raw run folders.

  python3 experiments/plot.py [results/d2]
Output: results/d2/figs/*.png
"""
import csv, os, statistics, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "d2")
FIGS = os.path.join(OUT, "figs")

# colour follows the configuration, always the same slot (validated palette, light mode)
COLOR = {"plain": "#2a78d6", "static": "#eb6834", "full": "#1baf7a",
         "full_noretx": "#eda100", "full_nostandby": "#e87ba4"}
LABEL = {"plain": "B0 plain routing", "static": "B1 static priority", "full": "DeadlineCast (ours)",
         "full_noretx": "Ours, no retransmission", "full_nostandby": "Ours, no standby server"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE})


def load():
    rows = list(csv.DictReader(open(os.path.join(OUT, "summary.csv"))))
    agg = {}
    for r in rows:
        agg.setdefault((r["scenario"], r["config"]), []).append(r)
    return agg


def stat(agg, scn, cfg, key):
    vals = []
    for r in agg.get((scn, cfg), []):
        try:
            v = float(r[key])
            if v == v:
                vals.append(v)
        except (KeyError, ValueError):
            pass
    if not vals:
        return None
    return statistics.mean(vals), min(vals), max(vals)


def style(ax, ylabel):
    ax.set_ylabel(ylabel)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def bar_group(ax, agg, scenarios, configs, key, fmt="{:.0f}", log=False):
    w = 0.8 / len(configs)
    for j, cfg in enumerate(configs):
        xs, ys, lo, hi = [], [], [], []
        for i, scn in enumerate(scenarios):
            s = stat(agg, scn, cfg, key)
            if s is None:
                continue
            m, mn, mx = s
            xs.append(i + (j - (len(configs) - 1) / 2) * w)
            ys.append(max(m, 0.05) if log else m)
            lo.append(max(m - mn, 0))
            hi.append(max(mx - m, 0))
        bars = ax.bar(xs, ys, width=w * 0.92, color=COLOR[cfg], label=LABEL[cfg], edgecolor=SURFACE, linewidth=2)
        if any(lo) or any(hi):
            ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=1, capsize=2)
        for x, y in zip(xs, ys):
            ax.annotate(fmt.format(y), (x, y), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(scenarios)
    if log:
        ax.set_yscale("log")


def fig_main(agg):
    scen = [s for s in ["normal", "congestion", "abuse", "linkfail"] if any((s, c) in agg for c in COLOR)]
    cfgs = ["plain", "static", "full"]
    for key, ylabel, name, title, log, fmt in [
        ("deadline_miss_pct", "% of alerts not delivered on time", "fig1_deadline_miss.png",
         "Alerts that missed their deadline (lower is better)", False, "{:.0f}"),
        ("lat_p95_ms", "p95 latency, ms (log scale)", "fig2_latency_p95.png",
         "95th-percentile alert latency (lower is better)", True, "{:.1f}"),
        ("delivery_pct", "% of alerts delivered", "fig3_delivery.png",
         "Alerts delivered to subscribers (higher is better)", False, "{:.0f}")]:
        fig, ax = plt.subplots(figsize=(8.5, 4.2))
        bar_group(ax, agg, scen, cfgs, key, fmt=fmt, log=log)
        style(ax, ylabel)
        ax.set_title(title, loc="left", color=INK, fontsize=12, pad=12)
        ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.1))
        fig.tight_layout()
        fig.savefig(os.path.join(FIGS, name), dpi=160)
        plt.close(fig)


def fig_robustness(agg):
    panels = [("loss", ["full_noretx", "full"], "delivery_pct", "% delivered", "10% packet loss"),
              ("failover", ["full_nostandby", "full"], "delivery_pct", "% delivered", "Primary server crash"),
              ("fakealert", ["static", "full"], "fake_reached", "fake alerts reaching subscribers",
               "Fake-alert attack")]
    panels = [p for p in panels if any((p[0], c) in agg for c in p[1])]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(4 * len(panels), 3.8))
    axes = axes if len(panels) > 1 else [axes]
    for ax, (scn, cfgs, key, ylabel, title) in zip(axes, panels):
        for i, cfg in enumerate(cfgs):
            s = stat(agg, scn, cfg, key)
            if s is None:
                continue
            m, mn, mx = s
            ax.bar(i, m, width=0.6, color=COLOR[cfg], edgecolor=SURFACE, linewidth=2)
            ax.errorbar([i], [m], yerr=[[m - mn], [mx - m]], fmt="none", ecolor=INK2, elinewidth=1, capsize=2)
            ax.annotate(f"{m:.0f}", (i, m), xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=9, color=INK)
        ax.set_xticks(range(len(cfgs)))
        ax.set_xticklabels([LABEL[c].replace(", ", ",\n").replace(" (ours)", "\n(ours)") for c in cfgs], fontsize=8)
        style(ax, ylabel)
        ax.set_title(title, loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig4_robustness.png"), dpi=160)
    plt.close(fig)


def first_run(scn, cfg):
    base = os.path.join(OUT, "raw", scn, cfg)
    if not os.path.isdir(base):
        return None
    reps = sorted(os.listdir(base))
    return os.path.join(base, reps[0]) if reps else None


def event_time(d, name):
    try:
        for r in csv.DictReader(open(os.path.join(d, "events.csv"))):
            if r["event"] == name:
                return float(r["ts"])
    except OSError:
        pass
    return None


def fig_linkfail():
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    t_fail = None
    for cfg in ["static", "full"]:
        d = first_run("linkfail", cfg)
        if not d or not os.path.exists(os.path.join(d, "alerts.csv")):
            continue
        rows = list(csv.DictReader(open(os.path.join(d, "alerts.csv"))))
        sender = list(csv.DictReader(open(os.path.join(d, "sender.csv"))))
        t0 = min(float(r["t_submit"]) for r in sender)
        tf = event_time(d, "link_down")
        if tf and t_fail is None:
            t_fail = tf - t0
        xs = [float(r["t_rel_s"]) for r in rows]
        ys = [int(r["subs_reached"]) for r in rows]
        ax.plot(xs, ys, color=COLOR[cfg], linewidth=2, marker="o", markersize=5, label=LABEL[cfg],
                markeredgecolor=SURFACE, markeredgewidth=1.5, drawstyle="steps-post")
    if t_fail is not None:
        ax.axvline(t_fail, color=INK2, linewidth=1, linestyle="--")
        ax.annotate("link s1-s2 cut", (t_fail, 5.3), xytext=(4, 0), textcoords="offset points",
                    fontsize=9, color=INK2)
    ax.set_ylim(-0.3, 5.8)
    ax.set_xlabel("time since first alert, s")
    style(ax, "subscribers reached (of 5)")
    ax.set_title("Link failure: who still gets each alert", loc="left", color=INK, fontsize=12, pad=12)
    ax.legend(frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig5_linkfail_timeline.png"), dpi=160)
    plt.close(fig)


def fig_abuse_load():
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    q_at = None
    for cfg in ["static", "full"]:
        d = first_run("abuse", cfg)
        path = os.path.join(d, "linkstats.csv") if d else ""
        if not d or not os.path.exists(path):
            continue
        rows = [r for r in csv.DictReader(open(path)) if r["link"] == "s1->s2" and float(r["mbps"]) <= 100]
        if not rows:
            continue
        t0 = float(rows[0]["ts"])
        ax.plot([float(r["ts"]) - t0 for r in rows], [float(r["mbps"]) for r in rows], color=COLOR[cfg],
                linewidth=2, label=LABEL[cfg])
        if cfg == "full":
            for r in csv.DictReader(open(os.path.join(d, "controller_events.csv"))):
                if r["event"] == "QUARANTINE":
                    q_at = float(r["ts"]) - t0
                    break
    if q_at is not None:
        ax.axvline(q_at, color=INK2, linewidth=1, linestyle="--")
        ax.annotate("attacker quarantined", (q_at, ax.get_ylim()[1] * 0.55), xytext=(4, 0),
                    textcoords="offset points", fontsize=9, color=INK2)
    ax.set_xlabel("time, s")
    style(ax, "load on core link s1->s2, Mbps")
    ax.set_title("Fake-urgent flood: the attack traffic on the core link", loc="left", color=INK, fontsize=12, pad=12)
    ax.legend(frameon=False, loc="center right")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "fig6_abuse_load.png"), dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(FIGS, exist_ok=True)
    agg = load()
    fig_main(agg)
    fig_robustness(agg)
    fig_linkfail()
    fig_abuse_load()
    print("charts written to", FIGS)
    for f in sorted(os.listdir(FIGS)):
        print("  ", f)
