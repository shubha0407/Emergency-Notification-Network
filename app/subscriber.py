import socket, json, sys, time

# usage: python3 subscriber.py <server_ip> [my_port] [log_file]
server = (sys.argv[1], 7000)
my_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
logfile = sys.argv[3] if len(sys.argv) > 3 else f"sub_{my_port}.csv"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", my_port))

# register, and retry until the server confirms (UDP can lose the first try)
sock.settimeout(1.0)
for attempt in range(1, 11):
    sock.sendto(json.dumps({"type": "REGISTER"}).encode(), server)
    try:
        data, _ = sock.recvfrom(2048)
        if json.loads(data.decode()).get("type") == "REG_OK":
            print(f"Subscriber on port {my_port}, registered with {server}", flush=True)
            break
    except (socket.timeout, ValueError):
        print(f"No reply from server, retrying ({attempt}/10)", flush=True)
else:
    print("Server not reachable, giving up", flush=True)
    sys.exit(1)
sock.settimeout(None)

log = open(logfile, "w")
log.write("seq,sent_ts,recv_ts,latency_ms,try\n")
seen = set()

try:
    while True:
        data, _ = sock.recvfrom(2048)
        recv_ts = time.time()
        try:
            msg = json.loads(data.decode())
        except ValueError:
            print("Malformed packet ignored", flush=True)
            continue
        if msg.get("type") != "ALERT":
            continue

        sock.sendto(json.dumps({"type": "ACK", "seq": msg["seq"]}).encode(), server)
        if msg["seq"] in seen:          # duplicate from a retransmission: ACK again, don't show twice
            continue
        seen.add(msg["seq"])

        latency = (recv_ts - msg["ts"]) * 1000
        log.write(f"{msg['seq']},{msg['ts']},{recv_ts},{latency:.3f},{msg.get('try', 1)}\n")
        log.flush()
        print("\n====== EMERGENCY ALERT ======")
        print(f"Seq      : {msg['seq']}")
        print(f"Type     : {msg['alert_type']}")
        print(f"Priority : {msg['priority']}")
        print(f"Location : {msg['location']}")
        print(f"Message  : {msg['message']}")
        print(f"Latency  : {latency:.2f} ms" + (f" (try {msg['try']})" if msg.get("try", 1) > 1 else ""))
        print("=============================", flush=True)
except KeyboardInterrupt:
    sock.sendto(json.dumps({"type": "UNREGISTER"}).encode(), server)
    print("\nUnregistered, bye.")
finally:
    log.close()
    sock.close()
