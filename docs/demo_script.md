# D1 demo script (about 10 minutes)

> **D1 version.** These instructions describe Deliverable 1. Since D2, the D1 files are `topology/topo_d1.py` and `controller/deadlinecast_d1.py`: use those names wherever this page says `topo.py` or `deadlinecast_ryu.py`. The current project is described in the main README.

Before the demo: start the VM, open 3 terminals (A, B, C), `cd ~/emergency-sdn` in each. Terminal A: `sudo mn -c`.

| # | Window | Type | Show | Say |
|---|---|---|---|---|
| 1 | B | `source ~/ryu-env/bin/activate` then `ryu-manager --ofp-tcp-listen-port 6653 controller/deadlinecast_ryu.py` | Ryu starts | "This is our SDN controller, the brain of the network." |
| 2 | A | `sudo python3 topology/topo.py` | `QoS queues created on 6 ports of s1`; B shows `switch 1 ready` | "Mininet created 6 hosts and one Open vSwitch switch, connected to the controller over OpenFlow 1.3. Every port has 3 priority queues." |
| 3 | A | `pingall`, `h1 ping -c 2 10.200.0.254` | 0% dropped, replies | "Full connectivity, plus a private link from the server to the controller." |
| 4 | A | `sh ovs-ofctl -O OpenFlow13 dump-flows s1` | Rules 300/200/150/0 | Explain each priority (see README section 2). "No server-to-subscriber priority rules exist yet." |
| 5 | A | `source tools/start_app.mn` | 5 `Registered` lines | "Five subscribers. Three share port 5000 on different hosts; a socket is unique by IP and port. Two use 5001 and 5002 because they share a host." |
| 6 | C | `tail -F logs/h3a.out` | Registered | |
| 7 | A | `h3 tcpdump -n -v -c 2 udp port 5000 > logs/dscp.txt 2>&1 &` then `h2 python3 app/sender.py 10.0.0.1`, choose 3, exit with 5 | C shows the alert; sender shows `accepted ... 5 subscribers`; B shows `ARMED` | "The server asked the controller to arm priority before sending. The application changed the network." |
| 8 | A | `sh cat logs/dscp.txt`, `sh ovs-ofctl -O OpenFlow13 dump-flows s1 \| grep nw_src=10.0.0.1`, `sh ovs-ofctl -O OpenFlow13 queue-stats s1 3`, `sh cat logs/server.out` | tos 0xb8; new rules with hard_timeout=30; queue 2 used; 5 ACKs | "DSCP 46 on the wire, temporary rules, queue 2, and every subscriber confirmed." |
| 9 | A | `h4 iperf3 -s -D`, `h6 iperf3 -c 10.0.0.4 -u -b 1M -S 184 -t 3`, `sh ovs-ofctl -O OpenFlow13 dump-flows s1 \| grep "priority=200" \| grep 10.0.0.4` | n_packets ≈ 260 on the re-mark rule | "h6 faked urgent traffic; the controller removed the mark. Nobody can jump the queue." |
| 10 | — | Show `results/` and the README results table | Ours vs baseline | "Under a 12 Mbps flood our alerts stay fast; with a plain switch they slow down or get lost. In D2 we add deadlines, rerouting and failure recovery." |

After: `exit`, `sudo mn -c`, Ctrl+C in B.
