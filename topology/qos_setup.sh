#!/bin/bash
# q2 = alerts (served first), q1 = ACKs, q0 = everything else. Ports capped at 10 Mbps.
for p in $(ovs-vsctl list-ports "$1"); do
  ovs-vsctl -- set port $p qos=@q \
    -- --id=@q create qos type=linux-htb other-config:max-rate=10000000 \
         queues:0=@q0 queues:1=@q1 queues:2=@q2 \
    -- --id=@q0 create queue other-config:max-rate=10000000 other-config:priority=2 \
    -- --id=@q1 create queue other-config:min-rate=500000 other-config:max-rate=2000000 other-config:priority=1 \
    -- --id=@q2 create queue other-config:min-rate=2000000 other-config:max-rate=10000000 other-config:priority=0 > /dev/null
done
echo "QoS queues created on $(ovs-vsctl list-ports "$1" | wc -l) ports of $1"
