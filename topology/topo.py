#!/usr/bin/env python3
"""
D2 topology: two edge switches joined by two core paths (primary s2, backup s3),
a standby server, and a private management network for ARM/DISARM.

  interactive:  sudo python3 topology/topo.py
  in scripts:   from topo import build, stop      (used by experiments/run_experiments.py)
"""
import os, subprocess, sys
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, Node
from mininet.cli import CLI
from mininet.log import setLogLevel, info

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import netconf as C


def sh(cmd):
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build(datapath=None, qos=True):
    """Create and start the network. datapath='user' runs OVS in userspace (no kernel module)."""
    datapath = datapath or os.environ.get("DC_DATAPATH")
    sh(f"ip link del {C.MGMT_BRIDGE}")
    net = Mininet(controller=None, switch=OVSSwitch, autoSetMacs=False, autoStaticArp=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    sw = {}
    for dpid, name in C.SWITCHES.items():
        opts = dict(dpid=f"{dpid:016x}", protocols="OpenFlow13")
        if datapath:
            opts["datapath"] = datapath
        sw[dpid] = net.addSwitch(name, **opts)

    hosts = {}
    for name, (ip, mac, dpid, port, role) in C.HOSTS.items():
        hosts[name] = net.addHost(name, ip=ip + "/24", mac=mac)
        net.addLink(hosts[name], sw[dpid], port2=port)          # hN-eth0 <-> switch port

    for a, pa, b, pb in C.LINKS:
        net.addLink(sw[a], sw[b], port1=pa, port2=pb)

    root = Node("root", inNamespace=False)                      # the VM itself, where Ryu runs
    mgmt = {h: net.addLink(hosts[h], root) for h in C.MGMT_HOSTS}

    net.start()

    for h in hosts.values():                                     # no IPv6 chatter in the experiments
        h.cmd("sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
        if datapath == "user":                                   # userspace OVS cannot finish offloaded checksums
            for intf in h.intfNames():
                h.cmd(f"ethtool -K {intf} tx off >/dev/null 2>&1")
    sh(f"ip link add {C.MGMT_BRIDGE} type bridge")
    for h, link in mgmt.items():
        hosts[h].setIP(C.MGMT_HOSTS[h] + "/24", intf=link.intf1)
        sh(f"ip link set {link.intf2.name} master {C.MGMT_BRIDGE}")
        sh(f"ip link set {link.intf2.name} up")
    sh(f"ip addr add {C.MGMT_CTRL_IP}/24 dev {C.MGMT_BRIDGE}")
    sh(f"ip link set {C.MGMT_BRIDGE} up")

    if qos:
        info("*** Setting up QoS queues\n")
        out = subprocess.run(["bash", os.path.join(HERE, "qos_setup.sh")] + list(C.SWITCHES.values()),
                             capture_output=True, text=True)
        info(out.stdout)
    return net


def stop(net):
    net.stop()
    sh(f"ip link del {C.MGMT_BRIDGE}")


BANNER = """
*** D2 topology is up
    h1 = primary server 10.0.0.1      h7 = standby server 10.0.0.7
    h2 = sender 10.0.0.2              h6 = noise / attacker 10.0.0.6
    h3, h4, h5 = subscriber hosts     s2 = primary core, s3 = backup core
    management: h1 10.200.0.1, h7 10.200.0.2 -> controller 10.200.0.254:6000
    next: pingall, then  source tools/start_app.mn
"""

if __name__ == "__main__":
    setLogLevel("info")
    net = build()
    info(BANNER)
    CLI(net)
    stop(net)
