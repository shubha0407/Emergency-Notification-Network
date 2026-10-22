# D1 test cases

Run everything from `~/emergency-sdn`. Commands in the "Command" column are typed at the `mininet>` prompt unless marked otherwise. Save each screenshot in `docs/screenshots/` with the name in the "Evidence" column, and write Pass or Fail in the last column.

**Setup for every test:** Terminal A `sudo mn -c`; Terminal B starts the controller; Terminal A `sudo python3 topology/topo.py`; then `source tools/start_app.mn` (except TC01 to TC04).

Background output in Mininet is not shown on screen, so packet captures are saved to `logs/` and read with `sh cat`.

| ID | What it tests | Command | Expected result | Evidence | Result |
|---|---|---|---|---|---|
| TC01 | Controller connects to switch | (Terminal B output) | `switch 1 ready: spoof, sender and ACK rules installed` and `control channel on 10.200.0.254:6000` | tc01_controller.png | pass |
| TC02 | Host connectivity | `pingall` | `0% dropped` | tc02_pingall.png | pass |
| TC03 | Private control link | `h1 ping -c 2 10.200.0.254` | 2 replies | tc03_ctrl_link.png | pass |
| TC04 | Permanent rules and queues | `sh ovs-ofctl -O OpenFlow13 dump-flows s1` and `sh ovs-vsctl list queue \| grep -c _uuid` | Rules at priority 300, 200, 150, 0; no `nw_src=10.0.0.1` rules yet; 18 queues | tc04_rules_before.png | pass |
| TC05 | Registration of 5 subscribers | `source tools/start_app.mn` | server.out shows 5 `Registered` lines | tc05_registered.png | pass |
| TC06 | Same port on different hosts, clash on same host | `h3 python3 app/subscriber.py 10.0.0.1 5000` | `OSError: [Errno 98] Address already in use` (h3:5000 is taken; h4:5000 and h5:5000 work) | tc06_port_clash.png | pass |
| TC07 | End-to-end alert delivery | `h2 python3 app/sender.py 10.0.0.1`, choose 3 | Sender: `Server accepted alert #1, delivering to 5 subscribers`; Terminal C shows the alert; `sh cat logs/server.out` shows 5 ACKs and `delivered to all 5` | tc07_alert.png, tc07_server.png | pass |
| TC08 | Wrong server address is detected | `h2 python3 app/sender.py 10.0.0.2`, choose 1 | `No confirmation from server...` | tc08_wrong_ip.png | pass |
| TC09 | ARM changes the network (integration) | Send an alert, then within 30 s `sh ovs-ofctl -O OpenFlow13 dump-flows s1 \| grep nw_src=10.0.0.1` | Terminal B prints `ARMED`; rules to 10.0.0.3/4/5 with `hard_timeout=30`, `set_queue:2`, n_packets > 0; gone after 30 s | tc09_armed.png, tc09_rules.png | pass |
| TC10 | DSCP marking on the wire | `h3 tcpdump -n -v -c 2 udp port 5000 > logs/dscp.txt 2>&1 &`, send a CRITICAL alert, `sh cat logs/dscp.txt` | `tos 0xb8` | tc10_dscp.png | pass |
| TC11 | Alerts use the priority queue, ACKs use the ACK queue | `sh ovs-ofctl -O OpenFlow13 queue-stats s1 3` and `... queue-stats s1 1` | Port 3: queue 2 pkts > 0. Port 1: queue 1 pkts > 0 | tc11_queues.png | pass |
| TC12 | Fake priority is removed (anti-spoofing) | `h4 iperf3 -s -D`, `h6 iperf3 -c 10.0.0.4 -u -b 1M -S 184 -t 3`, then `sh ovs-ofctl -O OpenFlow13 dump-flows s1 \| grep "priority=200" \| grep 10.0.0.4` | The `nw_tos=184` rule to 10.0.0.4 shows n_packets ≈ 260 with `set_field:0->ip_dscp` | tc12_spoof.png | pass |
| TC13 | Latency and loss, no load | `h2 python3 tools/auto_sender.py 10.0.0.1 20 0.5`, then in a normal terminal `python3 tools/collect_results.py ours_noload` | Table with 0% loss, latency in ms | tc13_noload.png | pass |
| TC14 | Latency and loss under congestion, ours vs baseline | See "Experiments" below | Ours: low latency, ~0% loss. Baseline: higher latency and/or loss | tc14_ours.png, tc14_base.png | pass |
| TC15 | Retransmission | Start the app, then `sh pkill -f "app/[s]ubscriber.py 10.0.0.1 5002"`, send 1 alert, `sh cat logs/server.out` | `Retransmit seq ... (try 2)`, `(try 3)`, then `no ACK from [('10.0.0.4', 5002)]`; the other 4 still ACK | tc15_retransmit.png | pass |

## Experiments (TC13 and TC14)

Results are written by `tools/collect_results.py` (run in a normal terminal, not `mininet>`) to `results/<label>.md` and `results/summary.csv`.

**Our controller (Terminal B running `deadlinecast_ryu.py`)**

```
source tools/start_app.mn
h2 python3 tools/auto_sender.py 10.0.0.1 20 0.5
```
Terminal C: `cd ~/emergency-sdn && python3 tools/collect_results.py ours_noload`

```
source tools/start_app.mn
h3 iperf3 -s -D
h6 iperf3 -c 10.0.0.3 -u -b 12M -t 40 &
h2 python3 tools/auto_sender.py 10.0.0.1 20 0.5
```
Terminal C: `python3 tools/collect_results.py ours_congested`

**Baseline (plain learning switch)**

`exit`, `sudo mn -c`. Terminal B: `ryu-manager --ofp-tcp-listen-port 6653 controller/baseline_l2.py`. Terminal A: `sudo python3 topology/topo.py`. Then repeat both runs above with the labels `base_noload` and `base_congested`. (With the baseline the server prints `Controller not reachable`; that is expected, since the baseline has no ARM listener.)

Then copy the four rows from `results/summary.csv` into the README results table.
