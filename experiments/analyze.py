"""
Turns the logs of one run into metrics. Used by run_experiments.py, and on its own:

  python3 experiments/analyze.py <run_folder>        e.g. logs   or   results/d2/raw/congestion/full/rep1
"""
import csv, glob, json, os, re, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "topology"))
import netconf as C

SUB_NAMES = [name for _, _, name, _ in C.SUBSCRIBERS]


def pct(values, q):
    if not values:
        return float("nan")
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(q * len(v))) - 1))]


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def count_lines(path, *needles):
    if not os.path.exists(path):
        return 0
    with open(path, errors="ignore") as f:
        return sum(1 for line in f if any(n in line for n in needles))


def iperf_mbps(path):
    """Received throughput of the background flood, from 'iperf3 -s -1 -J' output."""
    try:
        with open(path) as f:
            txt = f.read()
        data = json.loads(txt[txt.index("{"):])
        s = data["end"].get("sum") or data["end"].get("sum_received") or {}
        bps = s.get("bits_per_second", 0) * (1 - s.get("lost_percent", 0) / 100.0)
        return bps / 1e6
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def analyze_run(d):
    sender = read_csv(os.path.join(d, "sender.csv"))
    submit = {r["alert_id"]: float(r["t_submit"]) for r in sender}
    roles = {r["alert_id"]: r["role"] for r in sender}
    t_start = min(submit.values()) if submit else 0.0

    subs = {n: read_csv(os.path.join(d, f"{n}.csv")) for n in SUB_NAMES}
    n_subs = len(SUB_NAMES)
    expected = len(sender) * n_subs

    lat, e2e, first, late, delivered, fake_shown = [], [], 0, 0, 0, 0
    per_alert = {aid: {"got": 0, "max_lat": 0.0, "max_e2e": 0.0} for aid in submit}
    for rows in subs.values():
        seen = set()
        for r in rows:
            aid = r.get("alert_id", "")
            if aid.startswith("fake") or aid not in submit:
                fake_shown += aid.startswith("fake")
                continue
            if aid in seen:
                continue
            seen.add(aid)
            l = float(r["latency_ms"])
            e = (float(r["recv_ts"]) - submit[aid]) * 1000
            delivered += 1
            lat.append(l)
            e2e.append(e)
            first += r.get("try", "1") == "1"
            late += r.get("late", "0") == "1"
            pa = per_alert[aid]
            pa["got"] += 1
            pa["max_lat"] = max(pa["max_lat"], l)
            pa["max_e2e"] = max(pa["max_e2e"], e)

    on_time = delivered - late
    outs = [os.path.join(d, f"{n}.out") for n in SUB_NAMES]
    ev_path = os.path.join(d, "controller_events.csv")
    if not os.path.exists(ev_path):                      # interactive runs: controller logs in logs/controller/
        ev_path = os.path.join(d, "controller", "controller_events.csv")
    ev = read_csv(ev_path)
    rerouted = [float(m.group(1)) for r in ev if r["event"] == "REROUTED"
                for m in [re.search(r"in ([\d.]+) ms", r["detail"])] if m]
    nan = float("nan")
    m = {
        "alerts": len(sender),
        "accepted": sum(1 for r in sender if r["role"] != "NONE"),
        "via_standby": sum(1 for r in sender if r["role"] == "STANDBY"),
        "expected": expected,
        "delivered": delivered,
        "delivery_pct": 100.0 * delivered / expected if expected else nan,
        "deadline_miss_pct": 100.0 * (expected - on_time) / expected if expected else nan,
        "lat_avg_ms": statistics.mean(lat) if lat else nan,
        "lat_p50_ms": pct(lat, 0.50),
        "lat_p95_ms": pct(lat, 0.95),
        "lat_max_ms": max(lat) if lat else nan,
        "e2e_avg_ms": statistics.mean(e2e) if e2e else nan,
        "e2e_p95_ms": pct(e2e, 0.95),
        "first_try_pct": 100.0 * first / delivered if delivered else nan,
        "retransmissions": sum(count_lines(os.path.join(d, f), "Retransmit seq")
                               for f in ("server.out", "standby.out")),
        "fake_reached": sum(count_lines(o, "REJECTED", "Malformed") for o in outs) + fake_shown,
        "flood_mbps": iperf_mbps(os.path.join(d, "flood_server.json")),
        "quarantines": sum(1 for r in ev if r["event"] == "QUARANTINE"),
        "alert_reroutes": sum(1 for r in ev if r["event"] == "ALERT_REROUTE"),
        "recovery_ms": min(rerouted) if rerouted else nan,
    }
    rows = []
    for aid, t in sorted(submit.items(), key=lambda kv: kv[1]):
        pa = per_alert[aid]
        rows.append({"t_rel_s": round(t - t_start, 3), "alert_id": aid, "role": roles[aid],
                     "subs_reached": pa["got"], "subs_total": n_subs,
                     "max_latency_ms": round(pa["max_lat"], 3) if pa["got"] else "",
                     "max_e2e_ms": round(pa["max_e2e"], 3) if pa["got"] else ""})
    return m, rows


def write_alerts(d, rows):
    if not rows:
        return
    with open(os.path.join(d, "alerts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "logs"
    m, rows = analyze_run(d)
    write_alerts(d, rows)
    for k, v in m.items():
        print(f"{k:20s} {v:.2f}" if isinstance(v, float) else f"{k:20s} {v}")
