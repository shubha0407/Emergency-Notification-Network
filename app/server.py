import socket, json, time, threading

SERVER_PORT = 7000
ACK_TIMEOUT = 0.5          # seconds to wait for ACKs before retransmitting
MAX_TRIES = 3              # first send + 2 retransmissions
ARM_WINDOW = 30            # seconds the controller keeps alert-priority rules
DSCP = {"CRITICAL": 46, "HIGH": 34}
CTRL_ADDR = ("10.200.0.254", 6000)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", SERVER_PORT))

ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    ctrl.bind(("10.200.0.1", 0))   # private control link to the SDN controller
    ctrl.settimeout(0.2)
except OSError:
    ctrl = None                    # not running in our topology: work without the controller

subscribers = set()
pending = {}                       # seq -> delivery state
lock = threading.Lock()
seq = 0


def log(*a):
    print(*a, flush=True)


def arm():
    if not ctrl:
        return
    try:
        ctrl.sendto(json.dumps({"type": "ARM", "window": ARM_WINDOW}).encode(), CTRL_ADDR)
        ctrl.recvfrom(256)
    except OSError:
        log("Controller not reachable, sending without priority")


def send(p, targets):
    p["msg"]["try"] = p["tries"]
    out = json.dumps(p["msg"]).encode()
    for sub in targets:
        try:
            p["sock"].sendto(out, sub)
        except OSError as e:
            log("Send failed to", sub, e)


def fan_out(s, msg, targets):
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out_sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP.get(msg.get("priority"), 0) << 2)
    arm()                              # ask the controller for priority before sending
    msg["ts"] = time.time()            # send timestamp, used for latency
    with lock:
        p = {"msg": msg, "sock": out_sock, "waiting": set(targets),
             "tries": 1, "last": time.time(), "n": len(targets)}
        pending[s] = p
        send(p, targets)
        log(f"Alert seq {s} sent to {len(targets)} subscribers")


def retransmitter():
    while True:
        time.sleep(0.05)
        now = time.time()
        with lock:
            for s, p in list(pending.items()):
                if now - p["last"] < ACK_TIMEOUT:
                    continue
                if p["tries"] < MAX_TRIES:
                    p["tries"] += 1
                    p["last"] = now
                    send(p, list(p["waiting"]))
                    log(f"Retransmit seq {s} (try {p['tries']}) to {len(p['waiting'])} subscribers")
                else:
                    log(f"Alert seq {s}: no ACK from {sorted(p['waiting'])} after {MAX_TRIES} tries")
                    p["sock"].close()
                    del pending[s]


threading.Thread(target=retransmitter, daemon=True).start()
log(f"Server listening on UDP {SERVER_PORT}")

while True:
    data, addr = sock.recvfrom(2048)
    try:
        msg = json.loads(data.decode())
        mtype = msg["type"]
    except (ValueError, KeyError, TypeError):
        log("Ignored malformed packet from", addr)
        continue

    if mtype == "REGISTER":
        with lock:
            new = addr not in subscribers
            subscribers.add(addr)
            total = len(subscribers)
        sock.sendto(json.dumps({"type": "REG_OK"}).encode(), addr)
        if new:
            log("Registered", addr, "| total:", total)
    elif mtype == "UNREGISTER":
        with lock:
            subscribers.discard(addr)
        log("Unregistered", addr)
    elif mtype == "ACK":
        s = msg.get("seq")
        with lock:
            p = pending.get(s)
            if p and addr in p["waiting"]:
                p["waiting"].discard(addr)
                log(f"ACK for seq {s} from {addr}")
                if not p["waiting"]:
                    log(f"Alert seq {s} delivered to all {p['n']} subscribers "
                        f"in {(time.time() - p['msg']['ts']) * 1000:.2f} ms")
                    p["sock"].close()
                    del pending[s]
    elif mtype == "ALERT":
        seq += 1
        msg["seq"] = seq
        with lock:
            targets = list(subscribers)
        sock.sendto(json.dumps({"type": "ALERT_ACCEPTED", "seq": seq,
                                "subscribers": len(targets)}).encode(), addr)
        log(f"Alert seq {seq} ({msg.get('alert_type')}, {msg.get('priority')}) from {addr}")
        threading.Thread(target=fan_out, args=(seq, msg, targets), daemon=True).start()
    else:
        log("Unknown message type:", mtype)
