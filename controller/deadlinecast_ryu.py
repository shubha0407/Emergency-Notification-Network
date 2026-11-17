"""
DeadlineCast D2 controller (Ryu, OpenFlow 1.3)

  ryu-manager --ofp-tcp-listen-port 6653 controller/deadlinecast_ryu.py    full system
  ryu-manager --ofp-tcp-listen-port 6653 controller/baseline_static.py     baseline B1: static DSCP priority
  ryu-manager --ofp-tcp-listen-port 6653 controller/baseline_plain.py      baseline B0: static routing only

Pipeline on every switch:
  table 0 (ingress security)  : IPv6 drop, quarantine drops, fake-alert filter, DSCP re-marking,
                                source-IP validation                      -> goto table 1
  table 1 (forwarding + QoS)  : 350 ARM alert rules (temporary, least-loaded path) -> queue 2
                                340 sender -> servers, urgent            -> queue 2
                                330 control traffic (REG/ACK/heartbeat)  -> queue 1
                                 10 destination routing                  -> queue 0
                                  0 table-miss -> controller (proxy ARP)
Dynamic behaviour (full mode only):
  ARM/DISARM from the servers, port-stats monitoring (1 s), load-aware alert paths,
  link-failure rerouting, flow-stats based detection and quarantine of misbehaving hosts.
"""
import os, sys, json, time, socket, heapq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "topology"))
import netconf as C

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ether_types
from ryu.lib import hub

T_SEC, T_FWD = 0, 1
COOKIE_ROUTE, COOKIE_ARM = 0x1000, 0xA000
COOKIE_REMARK, COOKIE_FAKE, COOKIE_IPSPOOF, COOKIE_QUAR = 0xB000, 0xC000, 0xD000, 0xE000

POLL_S = 1.0             # monitoring interval
HYSTERESIS = 1.0         # an alert path must be this much cheaper before alerts are moved
LOAD_WEIGHT = 10.0       # cost added per fully loaded link (cost = hops + LOAD_WEIGHT * utilisation)
LOAD_FLOOR_MBPS = 1.0    # below this a link counts as idle
LIMIT_PPS = {COOKIE_REMARK: 50, COOKIE_FAKE: 5, COOKIE_IPSPOOF: 5}   # packets/s before a host is quarantined
QUARANTINE_S = 30
REASON = {COOKIE_REMARK: "fake urgent DSCP", COOKIE_FAKE: "fake alerts to subscriber ports",
          COOKIE_IPSPOOF: "spoofed source IP"}


def hnum(h):
    return int(h[1:])


class DeadlineCast(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    MODE = os.environ.get("DC_MODE", "full")      # full | static | plain

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.mode = self.MODE
        self.dps = {}
        self.link_up = [True] * len(C.LINKS)
        self.port_ok = {}               # (dpid, port) -> True/False for switch-to-switch ports
        self.port_link = {}
        for i, (a_, pa, b, pb) in enumerate(C.LINKS):
            self.port_link[(a_, pa)] = (i, b)
            self.port_link[(b, pb)] = (i, a_)
        self.access = {(v[2], v[3]): h for h, v in C.HOSTS.items()}
        self.tx, self.mbps = {}, {}
        self.flowcnt = {}
        self.armed = {}                 # server ip -> expiry time
        self.paths = {}                 # (server ip, subscriber switch) -> [dpid, ...]
        self.quarantined = {}           # host -> until
        self.barriers = {}
        self.last_load_print = 0.0
        logdir = os.environ.get("DC_LOG_DIR", os.path.join("logs", "controller"))
        os.makedirs(logdir, exist_ok=True)
        self.ev_f = self._csv(os.path.join(logdir, "controller_events.csv"), "ts,mode,event,detail")
        self.ls_f = self._csv(os.path.join(logdir, "linkstats.csv"), "ts,mode,link,mbps")
        self.say(f"DeadlineCast controller starting in mode '{self.mode}'")
        hub.spawn(self.listen)
        hub.spawn(self.monitor)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _csv(path, header):
        new = not os.path.exists(path)
        f = open(path, "a")
        if new:
            f.write(header + "\n")
            f.flush()
        return f

    def say(self, text):
        print(time.strftime("%H:%M:%S ") + text, flush=True)

    def event(self, name, detail="", show=True):
        self.ev_f.write(f"{time.time():.4f},{self.mode},{name},\"{detail}\"\n")
        self.ev_f.flush()
        if show:
            self.say(f"{name}: {detail}" if detail else name)

    def add(self, dp, table, prio, match, actions=None, goto=None, hard=0, cookie=0):
        p, o = dp.ofproto_parser, dp.ofproto
        ins = []
        if actions:
            ins.append(p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions))
        if goto is not None:
            ins.append(p.OFPInstructionGotoTable(goto))
        dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=table, priority=prio, match=match,
                                 instructions=ins, hard_timeout=hard, cookie=cookie))

    def delete(self, dp, table, cookie, mask=0xFFFFFFFFFFFFFFFF):
        p, o = dp.ofproto_parser, dp.ofproto
        dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=table, command=o.OFPFC_DELETE,
                                 cookie=cookie, cookie_mask=mask, out_port=o.OFPP_ANY,
                                 out_group=o.OFPG_ANY, match=p.OFPMatch()))

    def barrier(self, dps):
        """Wait until the switches have applied everything sent so far (max 0.5 s)."""
        waits = []
        for dp in dps:
            msg = dp.ofproto_parser.OFPBarrierRequest(dp)
            dp.set_xid(msg)
            ev = hub.Event()
            self.barriers[msg.xid] = ev
            dp.send_msg(msg)
            waits.append((msg.xid, ev))
        for xid, ev in waits:
            ev.wait(timeout=0.5)
            self.barriers.pop(xid, None)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply(self, ev):
        e = self.barriers.get(ev.msg.xid)
        if e:
            e.set()

    def link_name(self, dpid, port):
        if (dpid, port) in self.port_link:
            return f"{C.SWITCHES[dpid]}->{C.SWITCHES[self.port_link[(dpid, port)][1]]}"
        if (dpid, port) in self.access:
            return f"{C.SWITCHES[dpid]}->{self.access[(dpid, port)]}"
        return None

    # ------------------------------------------------------------------ paths
    def edges(self, d):
        for i, (a, pa, b, pb) in enumerate(C.LINKS):
            if self.link_up[i]:
                if a == d:
                    yield b, pa, i
                elif b == d:
                    yield a, pb, i

    def util(self, dpid, port):
        mbps = self.mbps.get((dpid, port), 0.0)
        if mbps < LOAD_FLOOR_MBPS:                       # heartbeats/pings are not "load"
            return 0.0
        return min(mbps / C.LINK_MBPS, 1.5)

    def path(self, src, dst, loaded=False):
        """Dijkstra over live links. loaded=True adds the measured utilisation of each link."""
        pq, seen = [(0.0, src, [src])], set()
        while pq:
            cost, d, pth = heapq.heappop(pq)
            if d == dst:
                return pth, cost
            if d in seen:
                continue
            seen.add(d)
            for nb, port, i in self.edges(d):
                if nb not in seen:
                    c = 1 + 0.001 * i + (LOAD_WEIGHT * self.util(d, port) if loaded else 0)
                    heapq.heappush(pq, (cost + c, nb, pth + [nb]))
        return None, float("inf")

    def path_cost(self, pth):
        cost = 0.0
        for u, v in zip(pth, pth[1:]):
            port = self.port_to(u, v)
            if port is None:
                return float("inf")
            i = self.port_link[(u, port)][0]
            cost += 1 + 0.001 * i + LOAD_WEIGHT * self.util(u, port)
        return cost

    def port_to(self, u, v):
        for nb, port, _ in self.edges(u):
            if nb == v:
                return port
        return None

    def next_port(self, dpid, host):
        _, _, hsw, hport, _ = C.HOSTS[host]
        if dpid == hsw:
            return hport
        pth, _ = self.path(dpid, hsw)
        return self.port_to(dpid, pth[1]) if pth else None

    def pname(self, pth):
        return "-".join(C.SWITCHES[d] for d in pth) if pth else "none"

    # ------------------------------------------------------------------ rule sets
    def install_security(self, dp):
        p = dp.ofproto_parser
        self.add(dp, T_SEC, 0, p.OFPMatch(), goto=T_FWD)
        self.add(dp, T_SEC, 450, p.OFPMatch(eth_type=0x86DD))                       # drop IPv6 chatter
        if self.mode != "full":
            return 0
        n = 0
        for h, (hip, _, sw, port, role) in C.HOSTS.items():
            if sw != dp.id:
                continue
            k = hnum(h)
            if h not in C.TRUSTED:
                for ap in C.SUB_ALERT_PORTS:                                            # only servers may send alerts
                    self.add(dp, T_SEC, 360, p.OFPMatch(in_port=port, eth_type=0x0800, ip_proto=17, udp_dst=ap),
                             cookie=COOKIE_FAKE | k)
                    n += 1
                for d in C.URGENT_DSCP:                                                 # untrusted urgent -> normal
                    self.add(dp, T_SEC, 355, p.OFPMatch(in_port=port, eth_type=0x0800, ipv4_src=hip, ip_dscp=d),
                             actions=[p.OFPActionSetField(ip_dscp=0)], goto=T_FWD, cookie=COOKIE_REMARK | k)
                    n += 1
            self.add(dp, T_SEC, 300, p.OFPMatch(in_port=port, eth_type=0x0800, ipv4_src=hip), goto=T_FWD)
            self.add(dp, T_SEC, 290, p.OFPMatch(in_port=port, eth_type=0x0800), cookie=COOKIE_IPSPOOF | k)
            n += 2
        return n

    def install_routes(self, dp):
        p, o = dp.ofproto_parser, dp.ofproto
        n = 0
        for h, (hip, *_rest) in C.HOSTS.items():
            port = self.next_port(dp.id, h)
            if port is None:
                continue
            out = p.OFPActionOutput(port)
            self.add(dp, T_FWD, 10, p.OFPMatch(eth_type=0x0800, ipv4_dst=hip), [out], cookie=COOKIE_ROUTE)
            n += 1
            if self.mode == "static":                                                   # B1: any DSCP 46/34 -> q2
                for d in C.URGENT_DSCP:
                    self.add(dp, T_FWD, 300, p.OFPMatch(eth_type=0x0800, ipv4_dst=hip, ip_dscp=d),
                             [p.OFPActionSetQueue(2), out], cookie=COOKIE_ROUTE)
            if self.mode != "full":
                continue
            if h in C.SERVER_HOSTS:
                for d in C.URGENT_DSCP:                                                 # operator alert -> server
                    self.add(dp, T_FWD, 340, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_src=C.ip(C.SENDER),
                                                         ipv4_dst=hip, ip_dscp=d),
                             [p.OFPActionSetQueue(2), out], cookie=COOKIE_ROUTE)
                self.add(dp, T_FWD, 330, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=hip,
                                                     udp_dst=C.SERVER_PORT),
                         [p.OFPActionSetQueue(1), out], cookie=COOKIE_ROUTE)            # REG / ACK / ALERT
            if h in C.SUB_HOSTS or h == C.STANDBY:
                for hb in C.HB_PORTS:                                                   # heartbeats
                    self.add(dp, T_FWD, 330, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=hip, udp_dst=hb),
                             [p.OFPActionSetQueue(1), out], cookie=COOKIE_ROUTE)
        return n

    # ------------------------------------------------------------------ switch life cycle
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        p, o = dp.ofproto_parser, dp.ofproto
        if dp.id not in C.SWITCHES:
            self.say(f"ignoring unknown switch dpid {dp.id}")
            return
        self.dps[dp.id] = dp
        for t in (T_SEC, T_FWD):                                                        # start clean
            dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=t, command=o.OFPFC_DELETE,
                                     out_port=o.OFPP_ANY, out_group=o.OFPG_ANY, match=p.OFPMatch()))
        for k in [k for k in self.tx if k[0] == dp.id]:
            self.tx.pop(k, None)
            self.mbps.pop(k, None)
        sec = self.install_security(dp)
        self.add(dp, T_FWD, 0, p.OFPMatch(), [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])
        routes = self.install_routes(dp)
        self.event("SWITCH_READY", f"{C.SWITCHES[dp.id]} mode={self.mode} security_rules={sec} routes={routes}")
        if len(self.dps) == len(C.SWITCHES):
            self.event("ALL_SWITCHES_CONNECTED", f"{len(self.dps)} switches")
        for srv in list(self.armed):                                    # restore active alert rules (own thread:
            hub.spawn(self.install_alert_rules, srv)                     # it waits for barrier replies)

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def gone(self, ev):
        dp = ev.datapath
        if dp.id in self.dps and self.dps[dp.id] is dp:
            del self.dps[dp.id]
            self.event("SWITCH_DISCONNECTED", C.SWITCHES.get(dp.id, str(dp.id)), show=False)

    # ------------------------------------------------------------------ proxy ARP
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        a = pkt.get_protocol(arp.arp)
        if eth is None or a is None or a.opcode != arp.ARP_REQUEST:
            return                                     # IPv4 never needs the controller: all routes are proactive
        target = next((h for h, v in C.HOSTS.items() if v[0] == a.dst_ip), None)
        if target is None:
            return
        tmac = C.HOSTS[target][1]
        rep = packet.Packet()
        rep.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP, dst=eth.src, src=tmac))
        rep.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=tmac, src_ip=a.dst_ip,
                                 dst_mac=a.src_mac, dst_ip=a.src_ip))
        rep.serialize()
        p, o = dp.ofproto_parser, dp.ofproto
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=o.OFP_NO_BUFFER, in_port=o.OFPP_CONTROLLER,
                                   actions=[p.OFPActionOutput(msg.match["in_port"])], data=rep.data))

    # ------------------------------------------------------------------ ARM / DISARM
    def server_of(self, mgmt_ip):
        for h, mip in C.MGMT_HOSTS.items():
            if mip == mgmt_ip:
                return h
        return None

    def alert_targets(self, srv_ip):
        srv_sw = next(v[2] for v in C.HOSTS.values() if v[0] == srv_ip)
        return srv_sw, sorted(set(C.HOSTS[h][2] for h in C.SUB_HOSTS))

    def choose_paths(self, srv_ip, keep=True):
        """Least-loaded path from the server's switch to each subscriber switch (with hysteresis)."""
        srv_sw, sub_sws = self.alert_targets(srv_ip)
        changed = []
        for ssw in sub_sws:
            best, best_cost = self.path(srv_sw, ssw, loaded=True)
            cur = self.paths.get((srv_ip, ssw))
            cur_cost = self.path_cost(cur) if cur else float("inf")
            if best and (cur is None or not keep or cur_cost == float("inf") or best_cost < cur_cost - HYSTERESIS):
                if best != cur:
                    changed.append((ssw, cur, best, cur_cost, best_cost))
                self.paths[(srv_ip, ssw)] = best
        return changed

    def install_alert_rules(self, srv_ip, window=None):
        idx = 1 if srv_ip == C.ip(C.PRIMARY) else 2
        window = window or max(1, int(self.armed.get(srv_ip, time.time()) - time.time()) + 1)
        used = set()
        for h in C.SUB_HOSTS:
            hip, _, hsw, hport, _ = C.HOSTS[h]
            pth = self.paths.get((srv_ip, hsw))
            if not pth:
                continue
            for i, d in enumerate(pth):
                dp = self.dps.get(d)
                if dp is None:
                    continue
                port = hport if i == len(pth) - 1 else self.port_to(d, pth[i + 1])
                if port is None:
                    continue
                p = dp.ofproto_parser
                for dscp in C.URGENT_DSCP:
                    self.add(dp, T_FWD, 350, p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_src=srv_ip,
                                                         ipv4_dst=hip, ip_dscp=dscp),
                             [p.OFPActionSetQueue(2), p.OFPActionOutput(port)], hard=window, cookie=COOKIE_ARM | idx)
                used.add(d)
        for d, dp in self.dps.items():                    # remove alert rules left on switches no longer used
            if d not in used:
                self.delete(dp, T_FWD, COOKIE_ARM | idx)
        self.barrier(list(self.dps.values()))

    def load_text(self):
        parts = []
        for i, (a, pa, b, pb) in enumerate(C.LINKS):
            st = "" if self.link_up[i] else " DOWN"
            parts.append(f"{C.SWITCHES[a]}->{C.SWITCHES[b]} {self.mbps.get((a, pa), 0):.1f}{st}")
        return ", ".join(parts) + " Mbps"

    def arm(self, srv_ip, window):
        self.armed[srv_ip] = time.time() + window
        self.choose_paths(srv_ip, keep=True)
        self.install_alert_rules(srv_ip, window)
        paths = "; ".join(self.pname(v) for (s, _), v in self.paths.items() if s == srv_ip)
        self.event("ARM", f"server {srv_ip} window {window}s alert path {paths} [{self.load_text()}]")
        return paths

    def disarm(self, srv_ip):
        idx = 1 if srv_ip == C.ip(C.PRIMARY) else 2
        self.armed.pop(srv_ip, None)
        for dp in self.dps.values():
            self.delete(dp, T_FWD, COOKIE_ARM | idx)
        self.barrier(list(self.dps.values()))
        self.event("DISARM", f"server {srv_ip}: alert rules removed")

    def listen(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        while True:                                         # wait until the topology creates 10.200.0.254
            try:
                s.bind((C.MGMT_CTRL_IP, C.CTRL_UDP_PORT))
                break
            except OSError:
                hub.sleep(1)
        self.say(f"control channel on {C.MGMT_CTRL_IP}:{C.CTRL_UDP_PORT}")
        while True:
            data, addr = s.recvfrom(2048)
            try:
                m = json.loads(data.decode())
                kind = m["type"]
            except (ValueError, KeyError, TypeError):
                continue
            host = self.server_of(addr[0])
            if host is None or kind not in ("ARM", "DISARM"):
                continue
            srv_ip = C.ip(host)
            if self.mode != "full":                         # baselines answer at once, so timing stays fair
                s.sendto(json.dumps({"type": kind + "_OK", "mode": self.mode}).encode(), addr)
                self.event(kind + "_IGNORED", f"{self.mode} mode", show=False)
                continue
            try:
                if kind == "ARM":
                    paths = self.arm(srv_ip, int(m.get("window", 30)))
                    s.sendto(json.dumps({"type": "ARM_OK", "path": paths}).encode(), addr)
                else:
                    self.disarm(srv_ip)
                    s.sendto(b'{"type":"DISARM_OK"}', addr)
            except Exception as e:                           # never let one bad message kill the listener
                self.say(f"control message error: {e!r}")

    # ------------------------------------------------------------------ link failure / recovery
    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status(self, ev):
        msg = ev.msg
        dp, o = msg.datapath, msg.datapath.ofproto
        key = (dp.id, msg.desc.port_no)
        if key not in self.port_link:
            return
        i, _ = self.port_link[key]
        self.port_ok[key] = (msg.reason != o.OFPPR_DELETE and not (msg.desc.state & o.OFPPS_LINK_DOWN)
                             and not (msg.desc.config & o.OFPPC_PORT_DOWN))
        a, pa, b, pb = C.LINKS[i]
        up = self.port_ok.get((a, pa), True) and self.port_ok.get((b, pb), True)   # up only if BOTH ends are up
        if self.link_up[i] == up:
            return
        self.link_up[i] = up
        name = f"{C.SWITCHES[a]}-{C.SWITCHES[b]}"
        self.event("LINK_UP" if up else "LINK_DOWN", name)
        if self.mode != "full":
            self.event("NO_REACTION", f"{self.mode} mode keeps its static routes")
            return
        # reroute in its own green thread: waiting for barrier replies inside an event handler would
        # block the very event loop that delivers those replies
        hub.spawn(self.reroute, time.time())

    def reroute(self, t0):
        n = sum(self.install_routes(d) for d in self.dps.values())
        for srv in list(self.armed):
            self.choose_paths(srv, keep=True)          # a dead path costs infinity, so it is always replaced
            self.install_alert_rules(srv)
        self.barrier(list(self.dps.values()))
        ms = (time.time() - t0) * 1000
        via = self.pname(self.path(1, 4)[0])
        self.event("REROUTED", f"{n} routes reinstalled in {ms:.1f} ms, s1 to s4 now via {via}")

    # ------------------------------------------------------------------ monitoring
    def monitor(self):
        while True:
            hub.sleep(POLL_S)
            for dp in list(self.dps.values()):
                p, o = dp.ofproto_parser, dp.ofproto
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, o.OFPP_ANY))
                if self.mode == "full":
                    dp.send_msg(p.OFPFlowStatsRequest(dp, table_id=T_SEC))
            if self.mode == "full":
                self.react()

    def react(self):
        now = time.time()
        for h in [h for h, t in self.quarantined.items() if t <= now]:
            del self.quarantined[h]
            self.event("RELEASE", f"{h} quarantine over")
        for srv in [s for s, t in self.armed.items() if t <= now]:
            del self.armed[srv]
            self.event("ARM_EXPIRED", f"server {srv}", show=False)
        for srv in list(self.armed):
            changed = self.choose_paths(srv, keep=True)
            if changed:
                self.install_alert_rules(srv)
                for ssw, old, new, oc, nc in changed:
                    self.event("ALERT_REROUTE", f"server {srv}: {self.pname(old)} (cost {oc:.1f}) -> "
                                                f"{self.pname(new)} (cost {nc:.1f}) [{self.load_text()}]")

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats(self, ev):
        dp, now = ev.msg.datapath, time.time()
        for st in ev.msg.body:
            key = (dp.id, st.port_no)
            name = self.link_name(*key)
            if name is None:
                continue
            prev = self.tx.get(key)
            self.tx[key] = (st.tx_bytes, now)
            if prev and now > prev[1]:
                mbps = (st.tx_bytes - prev[0]) * 8 / (now - prev[1]) / 1e6
                if not 0 <= mbps <= 10 * C.LINK_MBPS:          # counter reset/glitch (e.g. port re-created)
                    continue
                self.mbps[key] = mbps
                self.ls_f.write(f"{now:.3f},{self.mode},{name},{mbps:.3f}\n")
        self.ls_f.flush()
        busy = any(v > 1.0 for k, v in self.mbps.items() if k in self.port_link)
        if dp.id == max(self.dps, default=0) and busy and now - self.last_load_print >= 5:
            self.last_load_print = now
            self.say("LOAD " + self.load_text())

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats(self, ev):
        dp, now = ev.msg.datapath, time.time()
        for st in ev.msg.body:
            kind = st.cookie & 0xF000
            if kind not in LIMIT_PPS:
                continue
            key = (dp.id, st.cookie, st.priority, str(st.match))
            prev = self.flowcnt.get(key)
            self.flowcnt[key] = (st.packet_count, now)
            if not prev or now <= prev[1]:
                continue
            rate = (st.packet_count - prev[0]) / (now - prev[1])
            host = f"h{st.cookie & 0xFF}"
            if rate > LIMIT_PPS[kind] and host not in self.quarantined and host not in C.TRUSTED:
                self.quarantine(host, REASON[kind], rate)

    def quarantine(self, host, reason, rate):
        _, _, sw, port, _ = C.HOSTS[host]
        dp = self.dps.get(sw)
        if dp is None:
            return
        p = dp.ofproto_parser
        self.add(dp, T_SEC, 400, p.OFPMatch(in_port=port), hard=QUARANTINE_S, cookie=COOKIE_QUAR | hnum(host))
        self.quarantined[host] = time.time() + QUARANTINE_S
        self.event("QUARANTINE", f"{host}: {reason} at {rate:.0f} pkt/s -> {C.SWITCHES[sw]} port {port} "
                                 f"blocked for {QUARANTINE_S}s")
