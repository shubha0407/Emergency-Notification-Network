"""
Single source of truth for the D2 network. Imported by topology/topo.py (Mininet),
controller/deadlinecast_ryu.py (Ryu) and experiments/run_experiments.py.

              +---- s2 ----+
   h1 h2 h6 --s1            s4-- h3 h4 h5 h7
              +---- s3 ----+

  s1 = server-side edge, s4 = subscriber-side edge, s2 = primary core, s3 = backup core
"""

# ---- switches (dpid -> name) ----
SWITCHES = {1: "s1", 2: "s2", 3: "s3", 4: "s4"}

# ---- hosts: name -> (ip, mac, switch dpid, switch port, role) ----
HOSTS = {
    "h1": ("10.0.0.1", "00:00:00:00:00:01", 1, 1, "primary"),
    "h2": ("10.0.0.2", "00:00:00:00:00:02", 1, 2, "sender"),
    "h6": ("10.0.0.6", "00:00:00:00:00:06", 1, 3, "noise"),
    "h3": ("10.0.0.3", "00:00:00:00:00:03", 4, 3, "subscriber"),
    "h4": ("10.0.0.4", "00:00:00:00:00:04", 4, 4, "subscriber"),
    "h5": ("10.0.0.5", "00:00:00:00:00:05", 4, 5, "subscriber"),
    "h7": ("10.0.0.7", "00:00:00:00:00:07", 4, 6, "standby"),
}

# ---- switch-to-switch links: (switch A, port on A, switch B, port on B) ----
# listed in preference order: when two paths cost the same, the earlier link wins (s2 path = primary)
LINKS = [
    (1, 4, 2, 1),   # s1:4 <-> s2:1
    (2, 2, 4, 1),   # s2:2 <-> s4:1
    (1, 5, 3, 1),   # s1:5 <-> s3:1
    (3, 2, 4, 2),   # s3:2 <-> s4:2
]

LINK_MBPS = 10                      # every switch port is capped at this rate (topology/qos_setup.sh)

# ---- management network (private, not through the OpenFlow switches) ----
MGMT_BRIDGE = "mgmt0"
MGMT_CTRL_IP = "10.200.0.254"       # controller's ARM/DISARM listener
MGMT_HOSTS = {"h1": "10.200.0.1", "h7": "10.200.0.2"}   # server hosts on the management network
CTRL_UDP_PORT = 6000

# ---- application ports (must match app/common.py) ----
SERVER_PORT = 7000
SUB_ALERT_PORTS = [5000, 5001, 5002]          # ports subscribers receive alerts on
HB_PORTS = [8000, 8001, 8002, 8003]           # heartbeat ports (subscribers: base, base+1; standby: 8000)
URGENT_DSCP = [46, 34]                        # CRITICAL, HIGH

# ---- subscriber processes started by tools/start_app.mn and the experiment runner ----
# (host, alert port, csv name, heartbeat base port)
SUBSCRIBERS = [
    ("h3", 5000, "h3a", 8000),
    ("h4", 5000, "h4a", 8000),
    ("h5", 5000, "h5a", 8000),
    ("h3", 5001, "h3b", 8002),
    ("h4", 5002, "h4b", 8002),
]


def ip(host):
    return HOSTS[host][0]


def by_role(role):
    return [h for h, v in HOSTS.items() if v[4] == role]


PRIMARY = by_role("primary")[0]
STANDBY = by_role("standby")[0]
SENDER = by_role("sender")[0]
SERVER_HOSTS = [PRIMARY, STANDBY]
SUB_HOSTS = sorted(set(h for h, _, _, _ in SUBSCRIBERS))
TRUSTED = SERVER_HOSTS + [SENDER]                 # hosts allowed to send urgent (DSCP 46/34) traffic
