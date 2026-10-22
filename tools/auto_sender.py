import socket, json, sys, time

# usage: python3 tools/auto_sender.py <server_ip> [count] [gap_seconds] [CRITICAL|HIGH]
server = (sys.argv[1], 7000)
count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
gap = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
priority = sys.argv[4] if len(sys.argv) > 4 else "CRITICAL"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, (46 if priority == "CRITICAL" else 34) << 2)
sock.settimeout(1.0)

accepted = 0
for i in range(1, count + 1):
    msg = {"type": "ALERT", "alert_type": "TEST", "priority": priority,
           "location": "Test zone", "message": f"Automated test alert {i}/{count}"}
    sock.sendto(json.dumps(msg).encode(), server)
    try:
        r = json.loads(sock.recvfrom(2048)[0].decode())
        if r.get("type") == "ALERT_ACCEPTED":
            accepted += 1
            print(f"Alert {i}/{count} accepted as seq {r['seq']} -> {r['subscribers']} subscribers", flush=True)
    except (socket.timeout, ValueError):
        print(f"Alert {i}/{count}: no confirmation from server", flush=True)
    time.sleep(gap)
print(f"Done: {count} alerts sent, {accepted} accepted by the server")
