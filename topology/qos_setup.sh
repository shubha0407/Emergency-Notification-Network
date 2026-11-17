#!/bin/bash
# usage: sudo bash topology/qos_setup.sh s1 s2 s3 s4
# Creates 3 queues on every port of every given switch, each port capped at 10 Mbps (linux-htb):
#   q2 = alerts (served first), q1 = ACKs/heartbeats/registrations, q0 = everything else.
# Old QoS records are deleted first: Open vSwitch keeps them in its database even after "mn -c".
ovs-vsctl --all destroy qos -- --all destroy queue 2>/dev/null
n=0
for br in "$@"; do
  for p in $(ovs-vsctl list-ports "$br"); do
    ovs-vsctl -- set port $p qos=@q \
      -- --id=@q create qos type=linux-htb other-config:max-rate=10000000 \
           queues:0=@q0 queues:1=@q1 queues:2=@q2 \
      -- --id=@q0 create queue other-config:max-rate=10000000 other-config:priority=2 \
      -- --id=@q1 create queue other-config:min-rate=500000 other-config:max-rate=2000000 other-config:priority=1 \
      -- --id=@q2 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 other-config:priority=0 > /dev/null
    n=$((n+1))
  done
done
echo "QoS queues created on $n ports of: $*"
