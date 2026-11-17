# Emergency Notification Network (UDP + SDN)

> **D1 version.** These instructions describe Deliverable 1. Since D2, the D1 files are `topology/topo_d1.py` and `controller/deadlinecast_d1.py`: use those names wherever this page says `topo.py` or `deadlinecast_ryu.py`. The current project is described in the main README.

Project #16, SDN-Socket Programming Jackfruit Mini Project. **Deliverable D1.**

A UDP-based emergency notification system running on a Mininet network controlled by a Ryu SDN controller. A sender submits alerts to a broker server, which delivers them to registered subscribers and tracks their acknowledgements. The SDN controller recognises emergency traffic and forwards it through a high-priority queue, but only for trusted senders and only while an alert is active.

---

## 1. Problem and objectives

Emergency alerts are small, urgent and go to many people at once, often while the network is congested. A normal network treats an alert like any other packet, so it can wait behind bulk traffic or get dropped.

**Objectives for D1**

1. Deliver alerts over UDP from a sender, through a broker server, to multiple registered subscribers.
2. Confirm delivery with ACKs, and retransmit to subscribers that don't answer.
3. Run the whole system inside a Mininet topology controlled by an SDN controller.
4. Classify emergency traffic (DSCP marking) and forward it through a high-priority switch queue.
5. Let the application influence the network: the server asks the controller (ARM) to enable alert priority before each alert.
6. Stop non-trusted hosts from faking emergency priority.
7. Measure notification latency and packet loss.

**Expected network behaviour:** alerts from the server and sender use queue 2 and stay fast even when a host floods the network. Fake "urgent" traffic is re-marked to normal. Priority rules exist only during an alert window (30 s) and remove themselves.

---

## 2. Architecture

![Architecture](docs/architecture.png)

| Component | Host | Address | Role |
|---|---|---|---|
| Server (broker) | h1 | 10.0.0.1, UDP 7000 | Registers subscribers, accepts alerts, fans them out, tracks ACKs, retransmits, sends ARM to the controller |
| Sender | h2 | 10.0.0.2 | Operator console that submits alerts |
| Subscribers | h3, h4, h5 | 10.0.0.3 to .5 | 5 subscriber processes: h3:5000, h4:5000, h5:5000, h3:5001, h4:5002 |
| Noise / attacker | h6 | 10.0.0.6 | Generates congestion and spoofed traffic with iperf3 |
| Switch | s1 | Open vSwitch, OpenFlow 1.3 | Forwards packets using rules from the controller; 3 queues per port |
| Controller | VM (root namespace) | 127.0.0.1:6653 (OpenFlow), 10.200.0.254:6000 (ARM) | Ryu app `controller/deadlinecast_ryu.py` |

h1 has a second, private link (10.200.0.1 to 10.200.0.254) used only for ARM messages to the controller, like a management network.

### Communication flow

![Message flow](docs/message_flow.png)

### Application protocol (JSON over UDP)

| Message | From → To | Purpose |
|---|---|---|
| `REGISTER` / `REG_OK` | Subscriber → Server / Server → Subscriber | Sign up; the subscriber retries until it gets REG_OK |
| `UNREGISTER` | Subscriber → Server | Leave (Ctrl+C) |
| `ALERT` | Sender → Server, Server → Subscribers | The alert: type, priority, location, message, plus `seq`, `ts`, `try` added by the server |
| `ALERT_ACCEPTED` | Server → Sender | Confirms the server received the alert and how many subscribers it goes to |
| `ACK` | Subscriber → Server | Delivery confirmation for one `seq` |
| `ARM` / `ARM_OK` | Server → Controller | Ask the controller to enable alert priority for `window` seconds |

**Reliability:** UDP gives no delivery guarantee, so the server keeps a pending list per alert. Subscribers that haven't ACKed within 0.5 s get a retransmission (up to 3 tries in total). Subscribers ignore duplicates but ACK them again.

**Concurrency:** the server's main thread receives all messages, each alert's fan-out runs in its own thread, and a background thread handles retransmissions. Shared state is protected with a lock.

### SDN design

**Queues** (linux-htb on every switch port, 10 Mbps cap): `q2` alerts (served first, at least 2 Mbps), `q1` ACKs (0.5 to 2 Mbps), `q0` everything else.

**Flow rules** installed by the controller:

| Priority | Match | Action | When |
|---|---|---|---|
| 300 | UDP from sender 10.0.0.2 to server, DSCP 46 or 34 | queue 2 | Permanent |
| 300 | UDP from server to each subscriber, DSCP 46 or 34 | queue 2, `hard_timeout` 30 s | Installed on ARM |
| 200 | Any packet with DSCP 46 or 34 (not matched above) | Re-mark DSCP to 0, queue 0 | Permanent (anti-spoofing) |
| 150 | UDP from subscribers to server port 7000 (ACKs) | queue 1 | Permanent |
| 1 | Learned destination MAC | Output port | Learned from packet-in |
| 0 | Anything else | Send to controller | Permanent (table-miss) |

DSCP values: CRITICAL = 46 (ToS byte 0xb8), HIGH = 34 (ToS byte 0x88).

---

## 3. Repository structure

```
app/
  server.py            broker server (UDP 7000)
  sender.py            interactive operator console
  subscriber.py        subscriber; logs latency to CSV
controller/
  deadlinecast_ryu.py  our SDN controller
  baseline_l2.py       plain learning switch (baseline for comparison; see Setup)
topology/
  topo.py              Mininet topology
  qos_setup.sh         creates the 3 queues on every switch port
tools/
  start_app.mn         Mininet script: clean logs, start server + 5 subscribers
  auto_sender.py       sends N alerts automatically (for measurements)
  collect_results.py   latency and loss table from the logs
  arm_test.py          sends one ARM to test the controller link
docs/                  diagrams, test cases, demo script
results/               measured results (markdown tables + summary.csv)
logs/                  runtime logs (not committed)
```

---

## 4. Setup (Ubuntu VM)

Tested on Ubuntu 26.04 in VirtualBox (4 GB RAM, 2 CPUs).

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 tcpdump git curl unzip
sudo systemctl enable --now openvswitch-switch
sudo mn --test pingall
```

Ryu is unmaintained and only works on older Python, so it runs in its own Python 3.9 environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv python install 3.9
uv venv --python 3.9 --seed ~/ryu-env
source ~/ryu-env/bin/activate
pip install "setuptools==58.2.0" wheel pbr
pip install --no-build-isolation ryu "eventlet==0.30.2"
ryu-manager --version
cp ~/ryu-env/lib/python3.9/site-packages/ryu/app/simple_switch_13.py controller/baseline_l2.py
deactivate
```

Ryu always runs inside `~/ryu-env`. Mininet always runs with `sudo` and the system Python.

---

## 5. How to run

Use three terminals in the project folder.

**Terminal A: clean up**
```bash
cd ~/emergency-sdn
sudo mn -c
```

**Terminal B: controller** (start after `mn -c`, which kills Ryu)
```bash
cd ~/emergency-sdn
source ~/ryu-env/bin/activate
ryu-manager --ofp-tcp-listen-port 6653 controller/deadlinecast_ryu.py
```

**Terminal A: network**
```bash
sudo python3 topology/topo.py
```
At `mininet>`:
```
pingall
source tools/start_app.mn
h2 python3 app/sender.py 10.0.0.1
```

**Terminal C: live view of one subscriber**
```bash
tail -F ~/emergency-sdn/logs/h3a.out
```

**Stop:** `exit` in Terminal A, then `sudo mn -c`. Ctrl+C in Terminal B.

---

## 6. Test cases

See [docs/test_cases.md](docs/test_cases.md) for every test with its command, expected result and evidence.

## 7. Results

Measured with `tools/auto_sender.py` (20 CRITICAL alerts, 0.5 s apart, 5 subscribers) and summarised with `tools/collect_results.py`. Congestion = h6 sending 12 Mbps of UDP to h3 over a 10 Mbps port. Full tables are in `results/`.

| Run | Controller | Load | Loss % | Avg latency (ms) | p95 (ms) | Max (ms) | First-try % |
|---|---|---|---|---|---|---|---|
| ours_noload | DeadlineCast | none | _fill_ | _fill_ | _fill_ | _fill_ | _fill_ |
| ours_congested | DeadlineCast | 12 Mbps flood | _fill_ | _fill_ | _fill_ | _fill_ | _fill_ |
| base_noload | Learning switch | none | _fill_ | _fill_ | _fill_ | _fill_ | _fill_ |
| base_congested | Learning switch | 12 Mbps flood | _fill_ | _fill_ | _fill_ | _fill_ | _fill_ |

Copy the numbers from `results/summary.csv` after running the experiments in docs/test_cases.md (TC13 to TC14).

---

## 8. D1 rubric coverage

| Component (marks) | Where it is covered |
|---|---|
| Problem understanding and architecture (2) | Sections 1 and 2, docs/architecture.png, docs/message_flow.png |
| TCP/UDP socket implementation (4) | app/: UDP sockets, client/server/broker roles, threads + lock, error handling (malformed packets, timeouts, send errors), registration retry, ACKs and retransmission |
| Mininet + SDN implementation (4) | topology/topo.py, qos_setup.sh, controller/deadlinecast_ryu.py: topology, queues, classification, anti-spoofing, learning forwarding |
| Socket–SDN integration (3) | ARM message from the server installs time-limited priority rules; DSCP marks set by the app drive the switch rules |
| Initial testing and demo (2) | docs/test_cases.md, results/, docs/demo_script.md |

## 9. Team

| Member | Responsibility |
|---|---|
| Member 1 | UDP socket application (sender, server, subscriber) |
| Member 2 | Mininet topology, Open vSwitch queues, Ryu controller, integration and testing |
| Member 3 | Documentation and demo support |

## 10. Plan for D2

Multi-switch topology with redundant paths, deadline-aware delivery (earthquake warning zones with different lead times), dynamic preemption of background traffic, load-aware rerouting and link-failure recovery, rogue sender blocking, server failover, and a full comparison against static-priority and plain-switch baselines under congestion, packet loss and link failure.

## 11. Known limitations (D1)

- Single switch; no redundant paths yet.
- Priority rules are installed per alert window, not per subscriber deadline (D2).
- No message authentication yet; anti-spoofing relies on trusted source IPs.
