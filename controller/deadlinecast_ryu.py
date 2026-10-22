import socket, json
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub

SERVER, SENDER = '10.0.0.1', '10.0.0.2'
SUBS = ['10.0.0.3', '10.0.0.4', '10.0.0.5']
PORT = {f'10.0.0.{i}': i for i in range(1, 7)}
URGENT = [46, 34]                      # DSCP for CRITICAL and HIGH
CTRL = ('10.200.0.254', 6000)          # control channel (ARM messages)

class DeadlineCast(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.mac, self.dps = {}, {}
        self.barrier = hub.Event()
        hub.spawn(self.listen)

    def add(self, dp, prio, match, acts, hard=0):
        p = dp.ofproto_parser
        ins = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, acts)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                 instructions=ins, hard_timeout=hard))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath; p = dp.ofproto_parser; o = dp.ofproto
        self.dps[dp.id] = dp
        # 0: table-miss -> controller
        self.add(dp, 0, p.OFPMatch(), [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])
        # 200: anyone claiming urgent without permission -> re-mark to 0, normal queue
        for ip, port in PORT.items():
            for d in URGENT:
                m = p.OFPMatch(eth_type=0x0800, ipv4_dst=ip, ip_dscp=d)
                self.add(dp, 200, m, [p.OFPActionSetField(ip_dscp=0),
                                      p.OFPActionSetQueue(0), p.OFPActionOutput(port)])
        # 300: operator console -> server, urgent -> alert queue
        for d in URGENT:
            m = p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_src=SENDER, ipv4_dst=SERVER, ip_dscp=d)
            self.add(dp, 300, m, [p.OFPActionSetQueue(2), p.OFPActionOutput(PORT[SERVER])])
        # 150: subscriber ACKs -> ACK queue
        for ip in SUBS:
            m = p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_src=ip, ipv4_dst=SERVER, udp_dst=7000)
            self.add(dp, 150, m, [p.OFPActionSetQueue(1), p.OFPActionOutput(PORT[SERVER])])
        self.logger.info('switch %s ready: spoof, sender and ACK rules installed', dp.id)

    def arm(self, window):
        # 300 (temporary): server -> subscribers, urgent -> alert queue, for `window` seconds
        for dp in self.dps.values():
            p = dp.ofproto_parser
            for ip in SUBS:
                for d in URGENT:
                    m = p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_src=SERVER, ipv4_dst=ip, ip_dscp=d)
                    self.add(dp, 300, m, [p.OFPActionSetQueue(2), p.OFPActionOutput(PORT[ip])], hard=window)
            # barrier: wait until the switch has really installed the rules before saying ARM_OK
            self.barrier.clear()
            dp.send_msg(p.OFPBarrierRequest(dp))
            self.barrier.wait(timeout=0.5)
        self.logger.info('ARMED: alert rules live for %ss', window)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply(self, ev):
        self.barrier.set()

    def listen(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while True:                    # wait until Mininet creates 10.200.0.254
            try:
                s.bind(CTRL); break
            except OSError:
                hub.sleep(1)
        self.logger.info('control channel on %s:%d', *CTRL)
        while True:
            data, addr = s.recvfrom(2048)
            try:
                m = json.loads(data.decode())
            except ValueError:
                continue
            if m.get('type') == 'ARM':
                self.arm(int(m.get('window', 30)))
                s.sendto(b'{"type":"ARM_OK"}', addr)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        # 1: normal learning switch for everything else (ARP, ping, registrations...)
        msg = ev.msg; dp = msg.datapath; p = dp.ofproto_parser; o = dp.ofproto
        inp = msg.match['in_port']
        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        t = self.mac.setdefault(dp.id, {}); t[eth.src] = inp
        out = t.get(eth.dst, o.OFPP_FLOOD)
        acts = [p.OFPActionOutput(out)]
        if out != o.OFPP_FLOOD:
            self.add(dp, 1, p.OFPMatch(in_port=inp, eth_dst=eth.dst), acts)
        data = msg.data if msg.buffer_id == o.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                   in_port=inp, actions=acts, data=data))
