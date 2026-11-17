#!/usr/bin/env python3
"""
Runs every D2 scenario against each controller, several times, unattended.
For each run it starts a fresh Ryu controller and a fresh Mininet network, starts both servers and
the 5 subscribers, applies the scenario, sends alerts automatically, then saves all logs and metrics.

  sudo python3 experiments/run_experiments.py                       everything, 3 repetitions (~25 min)
  sudo python3 experiments/run_experiments.py --reps 1              quick pass (~9 min)
  sudo python3 experiments/run_experiments.py --only linkfail,abuse  some scenarios only

Output: results/d2/summary.csv (one row per run), results/d2/raw/<scenario>/<config>/rep<k>/ (all logs).
Then:   python3 experiments/plot.py && python3 experiments/report.py
"""
import argparse, csv, os, shutil, signal, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "topology"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import netconf as C
import analyze

CONTROLLERS = {"full": "controller/deadlinecast_ryu.py",
               "static": "controller/baseline_static.py",
               "plain": "controller/baseline_plain.py"}

# config name -> (controller mode, extra environment for the apps, start the standby server?)
CONFIGS = {
    "plain": ("plain", {}, True),
    "static": ("static", {}, True),
    "full": ("full", {}, True),
    "full_noretx": ("full", {"DC_MAX_TRIES": "1"}, True),
    "full_nostandby": ("full", {}, False),
}

SCENARIOS = {
    "normal": {"desc": "no background traffic", "configs": ["plain", "static", "full"]},
    "congestion": {"desc": "h6 floods h3 with 12 Mbps of normal UDP over 10 Mbps links",
                   "flood": 0, "configs": ["plain", "static", "full"]},
    "abuse": {"desc": "h6 floods h3 with 12 Mbps marked DSCP 46 (fake urgent)",
              "flood": 184, "configs": ["plain", "static", "full"]},
    "linkfail": {"desc": "link s1-s2 goes down 4 s into the run", "event": "link_down",
                 "configs": ["plain", "static", "full"]},
    "loss": {"desc": "10% random packet loss on the server's link", "netem": "10%",
             "configs": ["full_noretx", "full"]},
    "failover": {"desc": "primary server crashes 4 s into the run", "event": "kill_primary",
                 "configs": ["full_nostandby", "full"]},
    "fakealert": {"desc": "h6 sends fake unsigned alerts to subscribers, half with a forged server IP",
                  "rogue": True, "configs": ["static", "full"]},
}

EVENT_AT = 4.0          # seconds after the first alert
FLOOD_LEAD = 3.0        # flood starts this long before the first alert


def sh(cmd):
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_ryu():
    cands = [os.environ.get("RYU_MANAGER", "")]
    user = os.environ.get("SUDO_USER")
    if user:
        cands.append(os.path.expanduser(f"~{user}/ryu-env/bin/ryu-manager"))
    cands += [os.path.expanduser("~/ryu-env/bin/ryu-manager"), shutil.which("ryu-manager") or ""]
    for c in cands:
        if c and os.path.exists(c):
            return c
    sys.exit("ryu-manager not found: set RYU_MANAGER=/path/to/ryu-env/bin/ryu-manager")


def cleanup():
    sh("mn -c")
    sh("pkill -f 'app/[s]erver.py'; pkill -f 'app/[s]ubscriber.py'; pkill -f 'tools/[a]uto_sender.py'; "
       "pkill -f 'tools/[r]ogue_alerts.py'; pkill -f '[i]perf3'; pkill -f '[r]yu-manager --ofp-tcp-listen-port'")
    sh(f"ip link del {C.MGMT_BRIDGE}")


def wait_for(path, needles, timeout, count=1):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(path, errors="ignore") as f:
                txt = f.read()
            if all(txt.count(n) >= count for n in needles):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def run_one(topo, ryu, scn_name, cfg_name, rep, out_dir, n_alerts, gap):
    scn = SCENARIOS[scn_name]
    mode, app_env, standby = CONFIGS[cfg_name]
    d = os.path.join(out_dir, "raw", scn_name, cfg_name, f"rep{rep}")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    print(f"\n=== {scn_name} / {cfg_name} / rep {rep}: {scn['desc']}", flush=True)
    cleanup()
    env = dict(os.environ, DC_LOG_DIR=d)
    ryu_p = subprocess.Popen([ryu, "--ofp-tcp-listen-port", "6653", CONTROLLERS[mode]], cwd=ROOT, env=env,
                             stdout=open(os.path.join(d, "controller.log"), "w"), stderr=subprocess.STDOUT)
    procs, net = [], None
    try:
        time.sleep(1.5)
        net = topo.build()
        if not wait_for(os.path.join(d, "controller.log"), ["ALL_SWITCHES_CONNECTED", "control channel on"], 25):
            print("  ! controller did not come up, skipping this run")
            return None
        h = {name: net.get(name) for name in C.HOSTS}
        for a, b in [("h1", "h3"), ("h1", "h4"), ("h1", "h5"), ("h7", "h3"), ("h2", "h1"), ("h2", "h7"),
                     ("h6", "h3"), ("h1", "h7")]:
            h[a].cmd(f"ping -c1 -W1 {C.ip(b)}")          # warm up ARP so the first alert is not an outlier

        aenv = dict(os.environ, **app_env)

        def start(host, args, out):
            p = h[host].popen(["python3", "-u"] + args, cwd=ROOT, env=aenv,
                              stdout=open(os.path.join(d, out), "w"), stderr=subprocess.STDOUT)
            procs.append(p)
            return p

        primary = start("h1", ["app/server.py"], "server.out")
        if standby:
            start("h7", ["app/server.py", "--standby"], "standby.out")
        time.sleep(1.0)
        for host, port, name, hb in C.SUBSCRIBERS:
            start(host, ["app/subscriber.py", C.ip("h1"), str(port), os.path.join(d, f"{name}.csv"), str(hb)],
                  f"{name}.out")
        n = len(C.SUBSCRIBERS)
        if not wait_for(os.path.join(d, "server.out"), ["Registered"], 20, count=n):
            print("  ! not all subscribers registered in time")

        duration = n_alerts * gap + 8
        if "flood" in scn:
            procs.append(h["h3"].popen(["iperf3", "-s", "-1", "-J"],
                                       stdout=open(os.path.join(d, "flood_server.json"), "w"),
                                       stderr=subprocess.STDOUT))
            time.sleep(0.5)
            procs.append(h["h6"].popen(["iperf3", "-c", C.ip("h3"), "-u", "-b", "12M", "-l", "1200",
                                        "-t", str(int(duration + FLOOD_LEAD)), "-S", str(scn["flood"])],
                                       stdout=open(os.path.join(d, "flood_client.txt"), "w"),
                                       stderr=subprocess.STDOUT))
            time.sleep(FLOOD_LEAD)
        if "netem" in scn:                                # random loss on everything h1 sends into the network
            err = h["h1"].cmd(f"tc qdisc add dev h1-eth0 root netem loss {scn['netem']} 2>&1")
            how = f"netem loss {scn['netem']}"
            if "rror" in err:                             # kernel without sch_netem: same effect with iptables
                prob = float(scn["netem"].rstrip("%")) / 100
                h["h1"].cmd(f"iptables -A OUTPUT -o h1-eth0 -m statistic --mode random --probability {prob} -j DROP")
                how = f"iptables random drop p={prob}"
            with open(os.path.join(d, "loss_method.txt"), "w") as f:
                f.write(how + "\n")
        if scn.get("rogue"):
            procs.append(h["h6"].popen(["python3", "-u", "tools/rogue_alerts.py", "20", str(duration)],
                                       cwd=ROOT, stdout=open(os.path.join(d, "rogue.out"), "w"),
                                       stderr=subprocess.STDOUT))
            time.sleep(1.0)

        events = open(os.path.join(d, "events.csv"), "w")
        events.write("ts,event\n")

        def fire():
            time.sleep(EVENT_AT)
            if scn.get("event") == "link_down":
                net.configLinkStatus("s1", "s2", "down")
            elif scn.get("event") == "kill_primary":
                primary.terminate()
            events.write(f"{time.time():.4f},{scn.get('event')}\n")
            events.flush()

        sender = h["h2"].popen(["python3", "-u", "tools/auto_sender.py", C.ip("h1"), str(n_alerts), str(gap),
                                "CRITICAL", os.path.join(d, "sender.csv")], cwd=ROOT, env=aenv,
                               stdout=open(os.path.join(d, "sender.out"), "w"), stderr=subprocess.STDOUT)
        procs.append(sender)
        if scn.get("event"):
            threading.Thread(target=fire, daemon=True).start()
        try:
            sender.wait(timeout=n_alerts * (gap + 2.5) + 30)
        except subprocess.TimeoutExpired:
            print("  ! sender timed out")
        time.sleep(3.0)                                   # let retransmissions and deadlines finish
        events.close()
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGINT)
            except OSError:
                pass
        time.sleep(0.5)
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass
        if net is not None:
            topo.stop(net)
        ryu_p.terminate()
        try:
            ryu_p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ryu_p.kill()

    m, rows = analyze.analyze_run(d)
    analyze.write_alerts(d, rows)
    print(f"  delivered {m['delivery_pct']:.1f}%  deadline-miss {m['deadline_miss_pct']:.1f}%  "
          f"p95 {m['lat_p95_ms']:.1f} ms  retx {m['retransmissions']}  fake reached {m['fake_reached']}  "
          f"quarantines {m['quarantines']}", flush=True)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated scenario names")
    ap.add_argument("--alerts", type=int, default=20)
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "d2"))
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run with sudo: sudo python3 experiments/run_experiments.py")

    from mininet.log import setLogLevel
    setLogLevel("warning")
    import topo
    ryu = find_ryu()
    names = [s for s in a.only.split(",") if s] or list(SCENARIOS)
    os.makedirs(a.out, exist_ok=True)
    summary = os.path.join(a.out, "summary.csv")
    total = sum(len(SCENARIOS[s]["configs"]) for s in names) * a.reps
    print(f"{total} runs, about {total * 28 // 60} minutes. Controller: {ryu}")
    done = 0
    for rep in range(1, a.reps + 1):
        for s in names:
            for cfg in SCENARIOS[s]["configs"]:
                m = run_one(topo, ryu, s, cfg, rep, a.out, a.alerts, a.gap)
                done += 1
                if m is None:
                    continue
                row = {"scenario": s, "config": cfg, "rep": rep, **m}
                new = not os.path.exists(summary)
                with open(summary, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(row))
                    if new:
                        w.writeheader()
                    w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()})
                print(f"  [{done}/{total}] saved", flush=True)
    cleanup()
    user = os.environ.get("SUDO_USER")
    if user:
        sh(f"chown -R {user}:{user} {a.out} {os.path.join(ROOT, 'logs')}")
    print(f"\nDone. Summary: {summary}\nNext: python3 experiments/plot.py && python3 experiments/report.py")


if __name__ == "__main__":
    main()
