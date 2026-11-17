"""
Sends N alerts automatically, using exactly the same delivery logic as app/sender.py
(including failover to the standby server), and logs one row per alert.

  python3 tools/auto_sender.py <primary_ip> [count] [gap_s] [CRITICAL|HIGH] [csv_file]
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from sender import AlertClient, make_alert

primary = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
gap = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
priority = sys.argv[4] if len(sys.argv) > 4 else "CRITICAL"
csv_path = sys.argv[5] if len(sys.argv) > 5 else "logs/sender.csv"

client = AlertClient(primary)
log = open(csv_path, "w")
log.write("n,alert_id,t_submit,t_done,role,seq,subscribers\n")
start = time.time()
accepted = 0
for i in range(1, count + 1):
    msg = make_alert("TEST", priority, "Test zone", f"Automated test alert {i}/{count}")
    t0 = time.time()
    role, r = client.deliver(msg, verbose=False)
    t1 = time.time()
    ok = role is not None
    accepted += ok
    log.write(f"{i},{msg['alert_id']},{t0:.6f},{t1:.6f},{role or 'NONE'},"
              f"{(r or {}).get('seq', '')},{(r or {}).get('subscribers', '')}\n")
    log.flush()
    print(f"Alert {i}/{count}: " + (f"accepted by {role} as seq {r.get('seq')} -> {r.get('subscribers')} subscribers"
                                    if ok else "NOT accepted by any server"), flush=True)
    time.sleep(max(0.0, start + i * gap - time.time()))
print(f"Done: {count} alerts sent, {accepted} accepted", flush=True)
client.close()
