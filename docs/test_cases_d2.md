# D2 test cases

Run from `~/emergency-sdn`. Commands are typed at the `mininet>` prompt unless marked otherwise.
Save screenshots in `docs/screenshots/d2/` with the name in the Evidence column and fill in the Result column.

**Setup for every test:** Terminal A `sudo mn -c` · Terminal B
`source ~/ryu-env/bin/activate && ryu-manager --ofp-tcp-listen-port 6653 controller/deadlinecast_ryu.py` ·
Terminal A `sudo python3 topology/topo.py` · then `source tools/start_app.mn` (except TC01-TC02).
Terminal C: `tail -F logs/h3a.out`.

Background commands in Mininet print nothing, so captures go to files and are read with `sh cat`.

| ID | What it tests | Command | Expected result | Evidence | Result |
|---|---|---|---|---|---|
| TC01 | Looped topology works without flooding | `pingall` | `0% dropped (42/42)`; Terminal B shows 4 × `SWITCH_READY` and `ALL_SWITCHES_CONNECTED` | d2_tc01_pingall.png | |
| TC02 | Two-table pipeline installed | `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0` and `... dump-flows s4 table=1 \| head -20` | Table 0: priorities 450/360/355/300/290/0; table 1: 340/330/10/0 rules | d2_tc02_tables.png | |
| TC03 | Both servers up, 5 subscribers registered with both | `sh cat logs/server.out` and `sh cat logs/standby.out` | Each shows `HMAC ON` and 5 `Registered` lines | d2_tc03_registered.png | |
| TC04 | End-to-end signed alert with deadline | `h2 python3 app/sender.py 10.0.0.1`, choose 3 | Sender: `PRIMARY server accepted alert #1 ... 5 subscribers`; Terminal C shows the alert `(from PRIMARY)` and `deadline 1000 ms: met` | d2_tc04_alert.png | |
| TC05 | ARM / DISARM (prioritisation) | Look at Terminal B after TC04 | `ARM: server 10.0.0.1 window 30s alert path s1-s2-s4 [...]` followed by `DISARM: ... alert rules removed` | d2_tc05_arm.png | |
| TC06 | Monitoring + load-aware alert path | `h3 iperf3 -s -D`, `h6 iperf3 -c 10.0.0.3 -u -b 12M -t 60 &`, wait 3 s, `h1 python3 tools/arm_test.py`, `sh ovs-ofctl -O OpenFlow13 dump-flows s3 table=1 \| grep priority=350` | Terminal B: `LOAD s1->s2 ~10 ...` and `ARM ... alert path s1-s3-s4`; s3 holds the priority-350 alert rules with `set_queue:2` | d2_tc06_reroute.png | |
| TC07 | Link failure recovery | (stop the flood: `sh pkill -f iperf3`) `link s1 s2 down`, then send an alert from h2 | Terminal B: `LINK_DOWN: s1-s2` and `REROUTED: 28 routes reinstalled in X ms, s1 to s4 now via s1-s3-s4`; alert reaches all 5 | d2_tc07_linkdown.png | |
| TC08 | Link restore | `link s1 s2 up` | `LINK_UP: s1-s2`, `REROUTED ... now via s1-s2-s4` | d2_tc08_linkup.png | |
| TC09 | Server crash and failover | `sh pkill -f "app/[s]erver.py$"` (kills only the primary), then send an alert from h2 | Sender: `No reply from PRIMARY ...`, `STANDBY server accepted alert`; Terminal C shows `(from STANDBY)`; Terminal B shows `ARM: server 10.0.0.7 ... path s4` | d2_tc09_failover.png | |
| TC10 | Heartbeat loss detected | wait 10 s after TC09, `sh cat logs/standby.out` and Terminal C | Standby: `PRIMARY heartbeat lost (>9s): STANDBY is now ACTIVE`; subscriber: `WARNING: PRIMARY server silent` | d2_tc10_heartbeat.png | |
| TC11 | Primary returns | `h1 python3 -u app/server.py >> logs/server.out 2>&1 &`, wait 5 s, `sh tail -6 logs/server.out` | Subscribers re-register with the primary (5 `Registered`); standby logs `returns to passive` | d2_tc11_return.png | |
| TC12 | Fake urgent DSCP is detected and blocked | `sh pkill -f iperf3`, `h3 iperf3 -s -D`, `h6 iperf3 -c 10.0.0.3 -u -b 2M -S 184 -t 8 &`, wait 3 s, `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 \| grep -E "priority=(400\|355)"` | Terminal B: `QUARANTINE: h6: fake urgent DSCP at N pkt/s -> s1 port 3 blocked for 30s`; priority-400 drop rule on in_port=3; `RELEASE` 30 s later | d2_tc12_quarantine.png | |
| TC13 | Fake alerts never reach subscribers | after `RELEASE`: `h6 python3 tools/rogue_alerts.py 20 5`, then `sh grep -c REJECTED logs/h3a.out` and `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 \| grep -E "0xc006\|0xd006"` | `0` rejected (nothing arrived); the cookie 0xc006 drop rules (fake alerts, including the ones with a forged server IP, since this filter is checked first) count > 0; `QUARANTINE ... fake alerts to subscriber ports` | d2_tc13_fake.png | |
| TC14 | Unsigned alert rejected by the server | `h6 python3 -c "import socket,json;socket.socket(2,2).sendto(json.dumps({'type':'ALERT','alert_id':'x1','priority':'CRITICAL'}).encode(),('10.0.0.1',7000))"`, then `sh tail -2 logs/server.out` | `REJECTED alert from ('10.0.0.6', ...): missing or invalid signature` | d2_tc14_hmac.png | |
| TC15 | Retransmission under loss | `h1 tc qdisc add dev h1-eth0 root netem loss 30%`, `h2 python3 tools/auto_sender.py 10.0.0.1 10 0.5 CRITICAL logs/sender.csv`, `sh grep Retransmit logs/server.out \| tail -3`, then `h1 tc qdisc del dev h1-eth0 root`. If tc says *qdisc kind is unknown*, use `h1 iptables -A OUTPUT -o h1-eth0 -m statistic --mode random --probability 0.3 -j DROP` instead and remove it with `h1 iptables -F OUTPUT` | `Retransmit seq ... (try 2)` lines; subscribers still get the alerts (`try` column 2 in their CSV) | d2_tc15_retx.png | |
| TC16 | Metrics for an interactive run | (normal terminal) `python3 experiments/analyze.py logs` | delivery %, deadline-miss %, latency, retransmissions printed | d2_tc16_analyze.png | |
| TC17 | Automated comparison | (normal terminal) `sudo python3 experiments/run_experiments.py`, then `python3 experiments/plot.py && python3 experiments/report.py` | `results/d2/RESULTS.md` with all tables and 6 charts in `results/d2/figs/` | d2_tc17_results.png | |
