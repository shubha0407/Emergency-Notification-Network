# D2 demo script (about 15 minutes)

Three PowerShell windows, each logged into the VM with `ssh shubha@127.0.0.1 -p 2222`, each in
`cd ~/emergency-sdn`. **A** = Mininet, **B** = controller (watch it), **C** = live subscriber view.
Paste one line at a time. Lines ending in `&` must give the `mininet>` prompt back immediately.

Before the evaluators arrive: run the automated experiments once (`sudo python3 experiments/run_experiments.py`,
then `python3 experiments/plot.py && python3 experiments/report.py`) so the results and charts are ready to show.

## 0. Start (1 min)

| Window | Type | Show / say |
|---|---|---|
| A | `sudo mn -c` | Clean start |
| B | `source ~/ryu-env/bin/activate` | |
| B | `ryu-manager --ofp-tcp-listen-port 6653 controller/deadlinecast_ryu.py` | "Our controller, mode full" |
| A | `sudo python3 topology/topo.py` | Banner with the roles; B prints 4 × `SWITCH_READY` |
| A | `pingall` | "0% dropped across a looped topology: the controller answers ARP itself, so nothing is flooded" |
| A | `source tools/start_app.mn` | Both servers show 5 `Registered` lines, `HMAC ON` |
| C | `tail -F logs/h3a.out` | Subscriber h3a is up and sees both servers |

## 1. Normal alert, prioritisation (2 min), Member 1 + Member 2

| Window | Type | Show / say |
|---|---|---|
| A | `h2 python3 app/sender.py 10.0.0.1` → `3`, `Block A`, `Intruder at main gate`, then `5` | Sender: accepted by PRIMARY, 5 subscribers |
| C | (watch) | Alert box `(from PRIMARY)`, latency, `deadline 1000 ms: met` |
| B | (watch) | `ARM ... alert path s1-s2-s4` then `DISARM`: "priority exists only while an alert is in flight" |
| A | `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0` | "Table 0 is security: fake-alert filter, DSCP re-marking, source-IP validation" |

## 2. Congestion and load-aware path (3 min), Member 2

| Window | Type | Show / say |
|---|---|---|
| A | `h3 iperf3 -s -D` | |
| A | `h6 iperf3 -c 10.0.0.3 -u -b 12M -t 60 &` | 12 Mbps into 10 Mbps links |
| B | (wait 5 s) | `LOAD s1->s2 10.0, s2->s4 9.9, s1->s3 0.0 ...`: "port statistics every second" |
| A | `h1 python3 tools/arm_test.py` | B: `ARM ... alert path s1-s3-s4`: "alerts avoid the busy core link" |
| A | `sh ovs-ofctl -O OpenFlow13 dump-flows s3 table=1 \| grep priority=350` | Alert rules on the backup core, `set_queue:2`, `hard_timeout` |
| A | send an alert from h2 as in step 1 | Still delivered fast while the flood runs |
| A | `sh pkill -f iperf3` | Stop the flood |

## 3. Link failure (2 min), Member 2

| Window | Type | Show / say |
|---|---|---|
| A | `link s1 s2 down` | B: `LINK_DOWN: s1-s2`, `REROUTED: 28 routes reinstalled in X ms, s1 to s4 now via s1-s3-s4` |
| A | send an alert from h2 | Delivered to all 5 |
| A | `link s1 s2 up` | B: `LINK_UP`, `REROUTED ... via s1-s2-s4` |

Say: "The static baselines keep their old routes: in our experiment they lose every alert after the cut."

## 4. Server crash and failover (2 min), Member 1

| Window | Type | Show / say |
|---|---|---|
| A | `sh pkill -f "app/[s]erver.py$"` | Kills only the primary (the standby's command line ends in `--standby`) |
| A | send an alert from h2 | Sender: `No reply from PRIMARY`, `STANDBY server accepted`; C: `(from STANDBY)`; B: `ARM: server 10.0.0.7 ... path s4` |
| A | (wait 10 s) `sh tail -3 logs/standby.out` | `PRIMARY heartbeat lost (>9s): STANDBY is now ACTIVE` |
| A | `h1 python3 -u app/server.py >> logs/server.out 2>&1 &` | Primary back; subscribers re-register by themselves |

## 5. Attacks (3 min), Member 2 + Member 3

| Window | Type | Show / say |
|---|---|---|
| A | `h3 iperf3 -s -D` | Receiver for the attack traffic (step 2's pkill stopped the old one) |
| A | `h6 iperf3 -c 10.0.0.3 -u -b 2M -S 184 -t 8 &` | h6 marks its traffic as urgent |
| B | (watch) | `QUARANTINE: h6: fake urgent DSCP at N pkt/s -> s1 port 3 blocked for 30s` |
| A | `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 \| grep priority=400` | The drop rule, with its 30 s hard timeout |
| B | (wait for `RELEASE: h6 quarantine over`) | |
| A | `h6 python3 tools/rogue_alerts.py 20 5` | Fake alerts, half with the server's forged IP |
| C | (watch) | Nothing appears |
| A | `sh ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 \| grep -E "0xc006\|0xd006"` | Drop counters on the 0xc006 rules: every fake, even those forging the server's IP, stopped at the first switch (0xd006 catches forged-IP traffic to anything else) |

Say: "Even if a fake got through, subscribers reject anything without a valid HMAC signature."

## 6. Results (2 min), Member 3

| Window | Type | Show / say |
|---|---|---|
| C | Ctrl+C, then `cat results/d2/RESULTS.md \| head -30` | Key findings, generated from the data |
| Windows | open `results/d2/figs/` | Deadline misses, robustness, link-failure timeline, attack traffic |

## After

A: `exit`, `sudo mn -c`. B: Ctrl+C.

## If something goes wrong

| Symptom | Fix |
|---|---|
| `Unable to contact the remote controller` | Start Ryu in B before `topo.py` |
| `Address already in use` | `exit`, `sudo pkill -f app/server.py; sudo pkill -f app/subscriber.py; sudo mn -c`, start again |
| `RTNETLINK answers: File exists` / mgmt0 errors | `sudo ip link del mgmt0`, then start again |
| h6 commands do nothing | h6 is probably quarantined: wait for `RELEASE` in B |
| Sender says neither server responded | `sh cat logs/server.out`; restart the servers with `source tools/start_app.mn` |
