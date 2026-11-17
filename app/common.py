"""
Shared settings for server.py, sender.py and subscriber.py.
Every host must run the SAME copy of this file.
"""
import hmac, hashlib, json, os

# ---------------- ports ----------------
SERVER_PORT = 7000        # both servers listen here: REGISTER, UNREGISTER, ACK, ALERT
HB_PORT_PRIMARY = 8000    # heartbeats FROM the primary arrive on this port
HB_PORT_STANDBY = 8001    # heartbeats FROM the standby arrive on this port
HB_STANDBY_OFFSET = HB_PORT_STANDBY - HB_PORT_PRIMARY

# ---------------- addresses ----------------
# Standby server = host h7 in the D2 topology (topology/netconf.py), same UDP port 7000.
# For a test on ONE laptop use "127.0.0.2" and run:
#   python3 server.py --bind 127.0.0.1
#   python3 server.py --standby --bind 127.0.0.2
STANDBY_IP = "10.0.0.7"

CTRL_ADDR = ("10.200.0.254", 6000)                                  # SDN controller (ARM/DISARM)
CTRL_BIND = {"PRIMARY": "10.200.0.1", "STANDBY": "10.200.0.2"}      # management network (mgmt0 bridge)

# ---------------- heartbeat ----------------
HB_INTERVAL = 3                         # seconds between heartbeats
HB_MISS_LIMIT = 3                       # missed heartbeats before a server is declared down
HB_TIMEOUT = HB_INTERVAL * HB_MISS_LIMIT

# ---------------- delivery ----------------
ACK_TIMEOUT = 0.5         # seconds to wait for ACKs before retransmitting
MAX_TRIES = int(os.environ.get("DC_MAX_TRIES", "3"))   # first send + 2 retransmissions (env override for experiments)
ARM_WINDOW = 30           # seconds the controller keeps alert-priority rules if no DISARM arrives
DSCP = {"CRITICAL": 46, "HIGH": 34}

# Deadline: how long (ms) an alert is still worth delivering / counts as "on time".
DEADLINE_MS = {"CRITICAL": 1000, "HIGH": 2000}
DEFAULT_DEADLINE_MS = 2000

# ---------------- optional HMAC ----------------
# Set USE_HMAC = True on EVERY host (sender, servers, subscribers) to enable it.
USE_HMAC = True
HMAC_KEY = b"change-this-shared-secret"


def _digest(msg):
    body = {k: v for k, v in msg.items() if k != "sig"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hmac.new(HMAC_KEY, text.encode(), hashlib.sha256).hexdigest()


def sign(msg):
    """Return a copy of msg with a 'sig' field added."""
    out = dict(msg)
    out["sig"] = _digest(out)
    return out


def verify(msg):
    """True only if msg carries a correct 'sig'."""
    sig = msg.get("sig")
    return isinstance(sig, str) and hmac.compare_digest(sig, _digest(msg))
