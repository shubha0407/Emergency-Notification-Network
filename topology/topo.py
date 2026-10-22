#!/usr/bin/env python3
import subprocess
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, Node
from mininet.cli import CLI
from mininet.log import setLogLevel, info

# h1 = server (broker), h2 = sender, h3-h5 = subscribers, h6 = noise host
def run():
    net = Mininet(controller=None, switch=OVSSwitch, autoSetMacs=True)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    h = {}
    for i in range(1, 7):
        h[i] = net.addHost(f'h{i}', ip=f'10.0.0.{i}/24')
        net.addLink(h[i], s1, port2=i)          # host i -> switch port i
    root = Node('root', inNamespace=False)      # the VM itself, where Ryu runs
    oob = net.addLink(h[1], root)               # private control cable h1 <-> controller
    net.start()
    h[1].setIP('10.200.0.1/24', intf=oob.intf1)
    root.setIP('10.200.0.254/24', intf=oob.intf2)
    info('*** Setting up QoS queues on s1\n')
    subprocess.run(['bash', 'topology/qos_setup.sh', 's1'])
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()
