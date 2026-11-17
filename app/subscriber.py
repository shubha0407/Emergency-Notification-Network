import socket, json, sys, time, select
from common import (SERVER_PORT, STANDBY_IP, HB_PORT_PRIMARY, HB_STANDBY_OFFSET,
                    HB_INTERVAL, HB_TIMEOUT, USE_HMAC, verify)

# usage: python3 subscriber.py <primary_ip> [my_port] [log_file] [hb_port]
#   my_port  : UDP port for alerts (default 5000)
#   hb_port  : heartbeat base port (default 8000: primary beats on hb_port, standby on hb_port+1)
#              only change it when several subscribers share ONE machine
if len(sys.argv) < 2:
    print("usage: python3 subscriber.py <primary_ip> [my_port] [log_file] [hb_port]")
    sys.exit(1)
primary_ip = sys.argv[1]
my_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
logfile = sys.argv[3] if len(sys.argv) > 3 else f"sub_{my_port}.csv"
hb_base = int(sys.argv[4]) if len(sys.argv) > 4 else HB_PORT_PRIMARY

servers = {"PRIMARY": (primary_ip, SERVER_PORT), "STANDBY": (STANDBY_IP, SERVER_PORT)}

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)        # alerts, REG_OK, ACKs
sock.bind(("0.0.0.0", my_port))
hb_p = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)        # heartbeats from the primary
hb_p.bind(("0.0.0.0", hb_base))
hb_s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)        # heartbeats from the standby
hb_s.bind(("0.0.0.0", hb_base + HB_STANDBY_OFFSET))

last_seen = {"PRIMARY": None, "STANDBY": None}    # last time we heard from each server
up = {"PRIMARY": False, "STANDBY": False}
last_try = {"PRIMARY": 0.0, "STANDBY": 0.0}


def register(role):
    reg = {"type": "REGISTER", "hb_port": hb_base}
    try:
        sock.sendto(json.dumps(reg).encode(), servers[role])
    except OSError:
        pass                                        # unreachable server: retried later


def mark_seen(role):
    if role in ("PRIMARY", "STANDBY"):
        last_seen[role] = time.time()
        if not up[role]:
            up[role] = True
            print(f"{role} server {servers[role][0]} is UP", flush=True)


# ---- register with BOTH servers; retry until at least one confirms (UDP can lose the first try) ----
for attempt in range(1, 11):
    for role in servers:
        register(role)
    end = time.time() + 1.0
    while time.time() < end and not all(last_seen.values()):
        sock.settimeout(max(0.01, end - time.time()))
        try:
            data, _ = sock.recvfrom(2048)
            r = json.loads(data.decode())
        except socket.timeout:
            break
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("type") == "REG_OK":
            mark_seen(r.get("role"))
    if any(last_seen.values()):
        break
    print(f"No reply from any server, retrying ({attempt}/10)", flush=True)
else:
    print("No server reachable, giving up", flush=True)
    sys.exit(1)
sock.settimeout(None)
print(f"Subscriber on port {my_port} (heartbeat ports {hb_base}/{hb_base + HB_STANDBY_OFFSET})", flush=True)

log = open(logfile, "w")
# one row per UNIQUE alert (duplicates from retransmission/failover are ACKed but not logged)
# late = 1 if latency_ms > deadline_ms (parsed by experiments/analyze.py and tools/collect_results.py)
log.write("seq,alert_id,srv,priority,alert_type,sent_ts,recv_ts,latency_ms,try,deadline_ms,late\n")
seen = set()


def on_heartbeat(s):
    data, _ = s.recvfrom(1024)
    try:
        m = json.loads(data.decode())
    except ValueError:
        return
    if isinstance(m, dict) and m.get("type") == "HEARTBEAT":
        mark_seen("PRIMARY" if s is hb_p else "STANDBY")


def on_alert_socket():
    data, addr = sock.recvfrom(2048)
    recv_ts = time.time()
    try:
        msg = json.loads(data.decode())
    except ValueError:
        print("Malformed packet ignored", flush=True)
        return
    if not isinstance(msg, dict):
        return
    mtype = msg.get("type")
    if mtype == "REG_OK":
        mark_seen(msg.get("role"))
        return
    if mtype != "ALERT":
        return
    if USE_HMAC and not verify(msg):
        print("Alert with missing/invalid signature REJECTED (not ACKed)", flush=True)
        return
    try:
        seq, ts = msg["seq"], msg["ts"]
        srv = msg.get("srv")
        key = msg.get("alert_id") or (srv, seq)
        is_dup = key in seen
        latency = (recv_ts - ts) * 1000
    except (KeyError, TypeError):
        print("Malformed alert ignored", flush=True)
        return

    # ACK the server that sent this alert (PRIMARY or STANDBY), even for duplicates
    ack_to = servers[srv] if isinstance(srv, str) and srv in servers else (addr[0], SERVER_PORT)
    ack = {"type": "ACK", "seq": seq, "alert_id": msg.get("alert_id")}
    try:
        sock.sendto(json.dumps(ack).encode(), ack_to)
    except OSError:
        pass
    if is_dup:                          # retransmission, or same alert via the other server
        return
    seen.add(key)

    tries = msg.get("try", 1)
    deadline = msg.get("deadline_ms")
    late = int(isinstance(deadline, (int, float)) and latency > deadline)
    dl = f"{deadline:.0f}" if isinstance(deadline, (int, float)) else ""
    atype = str(msg.get("alert_type", "")).replace(",", " ")
    log.write(f"{seq},{msg.get('alert_id', '')},{srv},{msg.get('priority', '')},{atype},"
              f"{ts},{recv_ts},{latency:.3f},{tries},{dl},{late}\n")
    log.flush()

    on_time = ""
    if isinstance(deadline, (int, float)):
        on_time = f" | deadline {deadline:.0f} ms: " + ("LATE!" if latency > deadline else "met")
    print("\n====== EMERGENCY ALERT ======")
    print(f"Seq      : {seq}   (from {srv})")
    print(f"Type     : {msg.get('alert_type')}")
    print(f"Priority : {msg.get('priority')}")
    print(f"Location : {msg.get('location')}")
    print(f"Message  : {msg.get('message')}")
    print(f"Latency  : {latency:.2f} ms" + (f" (try {tries})" if isinstance(tries, int) and tries > 1 else "") + on_time)
    print("=============================", flush=True)


def check_servers():
    now = time.time()
    for role in servers:
        alive = last_seen[role] is not None and now - last_seen[role] <= HB_TIMEOUT
        if up[role] and not alive:
            up[role] = False
            print(f"WARNING: {role} server silent for >{HB_TIMEOUT}s (heartbeat lost)", flush=True)
        if not alive and now - last_try[role] >= HB_INTERVAL:
            register(role)              # a restarted server has an empty list: register again
            last_try[role] = now


try:
    while True:
        ready, _, _ = select.select([sock, hb_p, hb_s], [], [], 0.5)
        for s in ready:
            if s is sock:
                on_alert_socket()
            else:
                on_heartbeat(s)
        check_servers()
except KeyboardInterrupt:
    for role in servers:
        try:
            sock.sendto(json.dumps({"type": "UNREGISTER"}).encode(), servers[role])
        except OSError:
            pass
    print("\nUnregistered, bye.")
finally:
    log.close()
    sock.close()
    hb_p.close()
    hb_s.close()
