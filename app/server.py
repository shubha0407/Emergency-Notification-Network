"""
Emergency notification server (UDP).

  python3 server.py                       primary server
  python3 server.py --standby             standby server
  python3 server.py --bind 127.0.0.1      bind one IP (needed to run both servers on one laptop)
"""
import socket, json, time, threading, uuid, argparse
from common import (SERVER_PORT, STANDBY_IP, HB_INTERVAL, HB_TIMEOUT, HB_PORT_PRIMARY,
                    HB_STANDBY_OFFSET, ACK_TIMEOUT, MAX_TRIES, ARM_WINDOW, CTRL_ADDR,
                    CTRL_BIND, DSCP, DEADLINE_MS, DEFAULT_DEADLINE_MS, USE_HMAC, sign, verify)

ap = argparse.ArgumentParser()
ap.add_argument("--standby", action="store_true", help="run as the standby server")
ap.add_argument("--bind", default="0.0.0.0", help="IP to bind (default: all interfaces)")
args = ap.parse_args()
ROLE = "STANDBY" if args.standby else "PRIMARY"
BIND_IP = args.bind

# ---------------- sockets ----------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)       # control: REGISTER / ACK / ALERT
sock.bind((BIND_IP, SERVER_PORT))

ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)       # private link to the SDN controller
try:
    ctrl.bind((CTRL_BIND[ROLE], 0))
    ctrl.settimeout(0.2)
except OSError:
    ctrl.close()
    ctrl = None                    # not running in our topology: work without the controller


def make_out_sock(dscp):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((BIND_IP, 0))
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, dscp << 2)
    except OSError as e:
        print("Could not set DSCP", dscp, e, flush=True)
    return s


# one persistent socket per DSCP value (instead of a new socket for every alert)
out_socks = {d: make_out_sock(d) for d in set(DSCP.values()) | {0}}
hb_sock = make_out_sock(0)

# ---------------- state ----------------
subscribers = {}                   # (ip, port) -> heartbeat base port of that subscriber
pending = {}                       # seq -> delivery state of an alert still being delivered
lock = threading.Lock()            # protects subscribers and pending
counter = {"seq": 0}
state = {"standby_active": False, "last_primary_hb": time.time()}
arm_lock = threading.Lock()
arm_state = {"count": 0}           # alerts currently in flight


def log(*a):
    print(*a, flush=True)


def reply(obj, addr):
    try:
        sock.sendto(json.dumps(obj).encode(), addr)
    except OSError as e:
        log("Reply failed to", addr, e)


# ---------------- ARM / DISARM (talks to the SDN controller) ----------------
def ctrl_msg(kind, extra=None):
    """Send one message to the controller and wait briefly for its reply. Caller holds arm_lock."""
    payload = {"type": kind}
    if extra:
        payload.update(extra)
    try:
        ctrl.sendto(json.dumps(payload).encode(), CTRL_ADDR)
        ctrl.recvfrom(256)
        return True
    except OSError:
        return False


def arm():
    if not ctrl:
        return
    with arm_lock:
        arm_state["count"] += 1
        if not ctrl_msg("ARM", {"window": ARM_WINDOW}):
            log("Controller not reachable, sending without priority")


def disarm():
    """Called when an alert is finished. Only the LAST finished alert sends DISARM."""
    if not ctrl:
        return
    with arm_lock:
        arm_state["count"] = max(0, arm_state["count"] - 1)
        if arm_state["count"] == 0:
            if ctrl_msg("DISARM"):
                log("Controller disarmed (normal forwarding restored)")
            else:
                log("No DISARM confirmation (the controller's ARM window will expire by itself)")


def disarm_async():
    threading.Thread(target=disarm, daemon=True).start()   # never block the main loop on the controller


# ---------------- alert delivery ----------------
def send(p, targets):
    out_msg = dict(p["msg"])
    out_msg["try"] = p["tries"]
    if USE_HMAC:
        out_msg = sign(out_msg)                 # 'try' changes, so sign on every send
    out = json.dumps(out_msg).encode()
    for sub in targets:
        try:
            p["sock"].sendto(out, sub)
        except OSError as e:
            log("Send failed to", sub, e)


def fan_out(s, msg, targets):
    arm()                                       # ask the controller for priority before sending
    msg["ts"] = time.time()                     # taken AFTER arm(), so ARM time isn't counted as latency
    with lock:
        p = {"msg": msg, "sock": out_socks[DSCP.get(msg["priority"], 0)],
             "waiting": set(targets), "tries": 1, "last": time.time(), "n": len(targets),
             "deadline": msg["ts"] + msg["deadline_ms"] / 1000.0}
        pending[s] = p
        send(p, targets)
        log(f"Alert seq {s} sent to {len(targets)} subscribers (deadline {msg['deadline_ms']:.0f} ms)")


def retransmitter():
    while True:
        time.sleep(0.05)
        now = time.time()
        finished = 0
        with lock:
            for s, p in list(pending.items()):
                if now >= p["deadline"]:
                    log(f"Alert seq {s}: DEADLINE passed, stopping. No ACK from {sorted(p['waiting'])}")
                    del pending[s]
                    finished += 1
                elif now - p["last"] < ACK_TIMEOUT:
                    continue
                elif p["tries"] >= MAX_TRIES:
                    log(f"Alert seq {s}: no ACK from {sorted(p['waiting'])} after {MAX_TRIES} tries")
                    del pending[s]
                    finished += 1
                else:
                    p["tries"] += 1
                    p["last"] = now
                    send(p, list(p["waiting"]))
                    log(f"Retransmit seq {s} (try {p['tries']}) to {len(p['waiting'])} subscribers")
        for _ in range(finished):
            disarm_async()


# ---------------- heartbeat ----------------
def heartbeat_sender():
    offset = 0 if ROLE == "PRIMARY" else HB_STANDBY_OFFSET
    while True:
        active = ROLE == "PRIMARY" or state["standby_active"]
        data = json.dumps({"type": "HEARTBEAT", "role": ROLE, "active": active,
                           "ts": time.time()}).encode()
        with lock:
            targets = [(ip, hb + offset) for (ip, _), hb in subscribers.items()]
        if ROLE == "PRIMARY":
            targets.append((STANDBY_IP, HB_PORT_PRIMARY))    # the standby watches the primary
        for t in targets:
            try:
                hb_sock.sendto(data, t)
            except OSError:
                pass                                        # unreachable host: just try again next time
        time.sleep(HB_INTERVAL)


def primary_monitor(hb_in):
    """Standby only: become ACTIVE when the primary's heartbeat stops, passive again when it returns."""
    while True:
        try:
            data, _ = hb_in.recvfrom(1024)
            m = json.loads(data.decode())
            if isinstance(m, dict) and m.get("type") == "HEARTBEAT" and m.get("role") == "PRIMARY":
                state["last_primary_hb"] = time.time()
        except socket.timeout:
            pass
        except (ValueError, OSError):
            pass
        active = time.time() - state["last_primary_hb"] > HB_TIMEOUT
        if active != state["standby_active"]:
            state["standby_active"] = active
            if active:
                log(f"PRIMARY heartbeat lost (>{HB_TIMEOUT}s): STANDBY is now ACTIVE")
            else:
                log("PRIMARY heartbeat is back: STANDBY returns to passive")


# ---------------- message handlers ----------------
def on_register(msg, addr):
    try:
        hb_base = int(msg.get("hb_port", HB_PORT_PRIMARY))
    except (TypeError, ValueError):
        hb_base = HB_PORT_PRIMARY
    if not 1 <= hb_base <= 65534:
        hb_base = HB_PORT_PRIMARY
    with lock:
        new = addr not in subscribers
        subscribers[addr] = hb_base
        total = len(subscribers)
    reply({"type": "REG_OK", "role": ROLE}, addr)
    if new:
        log("Registered", addr, "| total:", total)


def on_unregister(addr):
    done = 0
    with lock:
        subscribers.pop(addr, None)
        for s, p in list(pending.items()):
            p["waiting"].discard(addr)           # nobody to wait for any more
            if not p["waiting"]:
                del pending[s]
                done += 1
    for _ in range(done):
        disarm_async()
    log("Unregistered", addr)


def on_ack(msg, addr):
    s = msg.get("seq")
    finished = False
    with lock:
        p = pending.get(s)
        if p and p["msg"]["alert_id"] == msg.get("alert_id") and addr in p["waiting"]:
            p["waiting"].discard(addr)
            log(f"ACK for seq {s} from {addr}")
            if not p["waiting"]:
                log(f"Alert seq {s} delivered to all {p['n']} subscribers "
                    f"in {(time.time() - p['msg']['ts']) * 1000:.2f} ms (try {p['tries']})")
                del pending[s]
                finished = True
    if finished:
        disarm_async()


def get_deadline_ms(msg, priority):
    default = DEADLINE_MS.get(priority, DEFAULT_DEADLINE_MS)
    try:
        d = float(msg.get("deadline_ms", default))
    except (TypeError, ValueError):
        return default
    return d if d > 0 else default


def on_alert(msg, addr):
    alert_id = msg.get("alert_id")
    if USE_HMAC and not verify(msg):
        log(f"REJECTED alert from {addr}: missing or invalid signature")
        reply({"type": "ALERT_REJECTED", "alert_id": alert_id, "reason": "bad signature"}, addr)
        return
    if not isinstance(alert_id, str) or not alert_id:
        alert_id = uuid.uuid4().hex[:12]
    priority = str(msg.get("priority", "HIGH"))
    deadline_ms = get_deadline_ms(msg, priority)
    if ROLE == "STANDBY" and not state["standby_active"]:
        log("NOTE: alert sent directly to the standby while the primary heartbeat is still fresh "
            "(the sender could not reach the primary)")
    counter["seq"] += 1
    s = counter["seq"]
    with lock:
        targets = list(subscribers)
    reply({"type": "ALERT_ACCEPTED", "seq": s, "alert_id": alert_id,
           "subscribers": len(targets), "role": ROLE}, addr)
    log(f"Alert seq {s} ({msg.get('alert_type')}, {priority}) from {addr}")
    if not targets:
        log("No subscribers registered, nothing to deliver")
        return
    out_msg = {"type": "ALERT", "seq": s, "alert_id": alert_id, "srv": ROLE,
               "alert_type": msg.get("alert_type", "UNKNOWN"), "priority": priority,
               "location": msg.get("location", ""), "message": msg.get("message", ""),
               "deadline_ms": deadline_ms}
    threading.Thread(target=fan_out, args=(s, out_msg, targets), daemon=True).start()


def handle(data, addr):
    try:
        msg = json.loads(data.decode())
        mtype = msg["type"]
        if mtype == "REGISTER":
            on_register(msg, addr)
        elif mtype == "UNREGISTER":
            on_unregister(addr)
        elif mtype == "ACK":
            on_ack(msg, addr)
        elif mtype == "ALERT":
            on_alert(msg, addr)
        else:
            log("Unknown message type:", mtype)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        log("Ignored malformed packet from", addr, "-", repr(e))


# ---------------- start ----------------
threading.Thread(target=retransmitter, daemon=True).start()
threading.Thread(target=heartbeat_sender, daemon=True).start()
if ROLE == "STANDBY":
    hb_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    hb_in.bind((BIND_IP, HB_PORT_PRIMARY))       # bind here so a port clash fails loudly at startup
    hb_in.settimeout(0.5)
    threading.Thread(target=primary_monitor, args=(hb_in,), daemon=True).start()

log(f"{ROLE} server listening on UDP {BIND_IP}:{SERVER_PORT} "
    f"(heartbeat every {HB_INTERVAL}s, controller link {'ON' if ctrl else 'OFF'}, HMAC {'ON' if USE_HMAC else 'OFF'})")
try:
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError as e:                      # e.g. Windows reports ICMP errors on recvfrom
            log("recv error:", e)
            continue
        handle(data, addr)
except KeyboardInterrupt:
    log("\nServer shutting down")
