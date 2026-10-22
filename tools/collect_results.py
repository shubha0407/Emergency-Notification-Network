import csv, glob, os, re, sys, statistics

# usage: python3 tools/collect_results.py <label> [logs_dir]
label = sys.argv[1] if len(sys.argv) > 1 else "run"
logs = sys.argv[2] if len(sys.argv) > 2 else "logs"
os.makedirs("results", exist_ok=True)

sent = retrans = delivered = failed = 0
server_out = os.path.join(logs, "server.out")
if os.path.exists(server_out):
    for line in open(server_out):
        m = re.search(r"Alert seq (\d+) sent to", line)
        if m:
            sent = max(sent, int(m.group(1)))
        retrans += "Retransmit seq" in line
        delivered += "delivered to all" in line
        failed += "no ACK from" in line

def pct(v, q):
    v = sorted(v)
    return v[max(0, round(q * len(v)) - 1)] if v else 0.0

rows, all_lat, all_first, all_recv = [], [], 0, 0
for f in sorted(glob.glob(os.path.join(logs, "*.csv"))):
    lat, seqs, first = [], set(), 0
    with open(f) as fh:
        for r in csv.DictReader(fh):
            try:
                s, l, t = int(r["seq"]), float(r["latency_ms"]), int(r.get("try") or 1)
            except (KeyError, ValueError, TypeError):
                continue
            if s in seqs:
                continue
            seqs.add(s); lat.append(l); first += (t == 1)
    expected = sent or (max(seqs) if seqs else 0)
    loss = 100 * (1 - len(seqs) / expected) if expected else 0.0
    rows.append((os.path.basename(f)[:-4], len(seqs), expected, loss,
                 statistics.mean(lat) if lat else 0, min(lat, default=0), pct(lat, 0.95), max(lat, default=0),
                 100 * first / len(seqs) if seqs else 0))
    all_lat += lat; all_first += first; all_recv += len(seqs)

exp_total = sent * len(rows) if sent else all_recv
overall = ("ALL", all_recv, exp_total, 100 * (1 - all_recv / exp_total) if exp_total else 0.0,
           statistics.mean(all_lat) if all_lat else 0, min(all_lat, default=0), pct(all_lat, 0.95),
           max(all_lat, default=0), 100 * all_first / all_recv if all_recv else 0)

head = "| Subscriber | Received | Expected | Loss % | Avg ms | Min ms | p95 ms | Max ms | First-try % |"
lines = [f"## Results: {label}", "",
         f"Alerts sent by server: {sent}, fully delivered: {delivered}, retransmissions: {retrans}, gave up: {failed}", "",
         head, "|" + "---|" * 9]
for r in rows + [overall]:
    lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.1f} | {r[4]:.2f} | {r[5]:.2f} | {r[6]:.2f} | {r[7]:.2f} | {r[8]:.0f} |")
text = "\n".join(lines) + "\n"
open(os.path.join("results", f"{label}.md"), "w").write(text)

summary = os.path.join("results", "summary.csv")
new = not os.path.exists(summary)
with open(summary, "a") as fh:
    if new:
        fh.write("label,alerts,subscribers,received,expected,loss_pct,avg_ms,p95_ms,max_ms,first_try_pct,retransmissions\n")
    fh.write(f"{label},{sent},{len(rows)},{overall[1]},{overall[2]},{overall[3]:.1f},{overall[4]:.2f},"
             f"{overall[6]:.2f},{overall[7]:.2f},{overall[8]:.0f},{retrans}\n")
print(text)
print(f"Saved results/{label}.md and added a row to results/summary.csv")
