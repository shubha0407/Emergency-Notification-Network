# What changed from D1 to D2

## Member 1's D2 files (app/)

Member 1 delivered `common.py`, `server.py`, `sender.py`, `subscriber.py` with a standby server,
heartbeats, DISARM, per-alert deadlines and optional HMAC. Integration changes:

| File | Change | Why |
|---|---|---|
| `server.py` | **none** | Works unchanged with the D2 controller |
| `common.py` | `STANDBY_IP` 10.0.0.6 (dummy) → **10.0.0.7** | h7 is the standby host in the D2 topology (h6 is the attacker) |
| `common.py` | `USE_HMAC = True` | As agreed: signatures on |
| `common.py` | `MAX_TRIES` can be overridden with the `DC_MAX_TRIES` environment variable (default still 3) | The experiments compare with and without retransmission |
| `common.py` | comment on `CTRL_BIND`: 10.200.0.2 is real now | The topology gives h7 that management address |
| `subscriber.py` | CSV TODO done: `seq, alert_id, srv, priority, alert_type, sent_ts, recv_ts, latency_ms, try, deadline_ms, late` | The analysis needs alert_id (to join with the sender log and drop fakes), srv (failover), late (deadline misses) |
| `sender.py` | delivery logic moved into an `AlertClient` class; menu unchanged and runs under `if __name__ == "__main__"` | `tools/auto_sender.py` reuses exactly the same delivery and failover code |
| `sender.py` | **sticky failover**: after the standby answers, it is tried first; the primary is retried every 10 s | Without it, every alert after a crash waited 1 s for the dead primary |

Standby port (Member 1's question): the standby uses the same UDP port **7000** on **10.0.0.7**;
heartbeats use 8000/8001 as in `common.py`.

## Member 2 (network and controller)

| File | Status |
|---|---|
| `topology/netconf.py` | **new**: single definition of hosts, ports, links, management IPs, subscriber processes |
| `topology/topo.py` | **rewritten**: diamond (s1, s2, s3, s4), standby h7, `mgmt0` bridge for h1 and h7, QoS on all switches; importable by the runner |
| `topology/topo_d1.py` | the D1 topology, unchanged |
| `topology/qos_setup.sh` | takes several switches at once |
| `controller/deadlinecast_ryu.py` | **rewritten**: two tables, proxy ARP, routing, ingress security, ARM/DISARM on paths, monitoring, load-aware alert paths, link-failure rerouting, detection + quarantine, three modes |
| `controller/baseline_static.py`, `baseline_plain.py` | **new** baselines (same code, features off) |
| `controller/deadlinecast_d1.py` | the D1 controller, unchanged |

D1's `baseline_l2.py` (Ryu's learning switch) cannot be used on the D2 topology: the two core paths form
a loop, and a learning switch floods ARP around it forever.

## Member 3 (integration, experiments, documentation)

| File | Status |
|---|---|
| `tools/start_app.mn` | starts both servers and the 5 subscribers with their heartbeat ports |
| `tools/auto_sender.py` | rewritten on top of `AlertClient`; logs one row per alert (`sender.csv`) |
| `tools/rogue_alerts.py` | **new** attacker: fake unsigned alerts, half with a forged server IP (raw socket) |
| `experiments/run_experiments.py` | **new**: 7 scenarios × controllers × repetitions, unattended |
| `experiments/analyze.py`, `plot.py`, `report.py` | **new**: metrics, 6 charts, `results/d2/RESULTS.md` |
| `docs/` | `architecture_d2.png`, `test_cases_d2.md` (17 tests), `demo_script_d2.md`, this file; D1 docs kept |

## Bugs found while testing D2 in Mininet (all fixed)

| Bug | Effect | Fix |
|---|---|---|
| Reroute waited for barrier replies inside an OpenFlow event handler | Recovery took 4000 ms instead of ~15 ms | Reroute runs in its own green thread |
| A link's two ends report "down" at slightly different times | Link flapped DOWN/UP/DOWN in the log | A link is up only if both ports are up |
| Heartbeats counted as link load | Alerts took the backup core with no real traffic | Load under 1 Mbps counts as idle |
| `pkill -f ryu-manager` in cleanup could match unrelated command lines | Could kill the terminal running the experiments | Matches only `ryu-manager --ofp-tcp-listen-port` |
