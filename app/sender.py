import socket, json, sys, time, uuid
from common import SERVER_PORT, STANDBY_IP, DEADLINE_MS, DEFAULT_DEADLINE_MS, USE_HMAC, sign

# usage: python3 sender.py [primary_server_ip]

PROBE_PRIMARY_AFTER = 10.0   # s: after failing over, try the primary first again after this long


class AlertClient:
    """Delivery logic shared by sender.py (operator menu) and tools/auto_sender.py (tests)."""

    def __init__(self, primary_ip):
        self.servers = {"PRIMARY": (primary_ip, SERVER_PORT),
                        "STANDBY": (STANDBY_IP, SERVER_PORT)}
        self.order = ["PRIMARY", "STANDBY"]       # sticky: whichever server answered last goes first
        self.probe_at = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 46 << 2)   # mark as urgent (DSCP 46)
        except OSError as e:
            print("Could not set DSCP:", e)

    def wait_reply(self, alert_id, timeout):
        """Wait for the reply that belongs to THIS alert (ignores stale replies to older alerts)."""
        end = time.time() + timeout
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                return None
            except OSError:
                return None
            try:
                r = json.loads(data.decode())
            except ValueError:
                continue
            if (isinstance(r, dict) and r.get("alert_id") == alert_id
                    and r.get("type") in ("ALERT_ACCEPTED", "ALERT_REJECTED")):
                return r

    def _try_order(self):
        if self.order[0] == "STANDBY" and time.time() >= self.probe_at:
            self.order = ["PRIMARY", "STANDBY"]   # periodically give the primary another chance
        return list(self.order)

    def deliver(self, msg, verbose=True):
        """Send to the preferred server; if it does not answer within 1 s, fail over to the other one.
        Returns (role or None, reply or None)."""
        out = lambda *a: print(*a, flush=True) if verbose else None
        data = json.dumps(sign(msg) if USE_HMAC else msg).encode()
        for role in self._try_order():
            addr = self.servers[role]
            try:
                self.sock.sendto(data, addr)
            except OSError as e:
                out(f"Could not send to {role} {addr[0]}: {e}")
                continue
            r = self.wait_reply(msg["alert_id"], 1.0)
            if r is None:
                out(f"No reply from {role} server ({addr[0]}).")
                continue
            if r["type"] == "ALERT_ACCEPTED":
                who = r.get("role", role)
                out(f"{who} server accepted alert #{r.get('seq')}, "
                    f"delivering to {r.get('subscribers')} subscribers.")
                if role == "STANDBY":
                    out("(delivered via the STANDBY server because the primary did not respond)")
                    if self.order[0] != "STANDBY":
                        self.order = ["STANDBY", "PRIMARY"]
                        self.probe_at = time.time() + PROBE_PRIMARY_AFTER
                else:
                    self.order = ["PRIMARY", "STANDBY"]
                return who, r
            out(f"{r.get('role', role)} server REJECTED the alert: {r.get('reason')}")
            return None, r
        out("ALERT NOT DELIVERED: neither server responded. Check the IPs and that a server is running.")
        return None, None

    def close(self):
        self.sock.close()


def make_alert(alert_type, priority, location, message):
    return {
        "type": "ALERT",
        "alert_id": uuid.uuid4().hex[:12],       # same id on retransmissions / failover, so duplicates are ignored
        "alert_type": alert_type,
        "priority": priority,
        "deadline_ms": DEADLINE_MS.get(priority, DEFAULT_DEADLINE_MS),
        "location": location,
        "message": message,
    }


alerts = {
    "1": ("FIRE", "HIGH"),
    "2": ("MEDICAL", "HIGH"),
    "3": ("SECURITY THREAT", "CRITICAL"),
    "4": ("FLOOD", "HIGH"),
}

if __name__ == "__main__":
    client = AlertClient(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1")
    print("=== EMERGENCY NOTIFICATION SYSTEM ===")
    print("1. Fire\n2. Medical Emergency\n3. Security Threat\n4. Flood\n5. Exit")
    while True:
        choice = input("\nChoose emergency type: ").strip()
        if choice == "5":
            break
        if choice not in alerts:
            print("Invalid choice. Try again.")
            continue
        alert_type, priority = alerts[choice]
        msg = make_alert(alert_type, priority,
                         input("Enter location: ").strip() or "Unknown",
                         input("Enter emergency details: ").strip() or "No details")
        client.deliver(msg)
    client.close()
