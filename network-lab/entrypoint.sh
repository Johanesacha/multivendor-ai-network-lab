#!/bin/bash
# Enable required FRR daemons
sed -i 's/^bgpd=no/bgpd=yes/' /etc/frr/daemons
sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons
sed -i 's/^staticd=no/staticd=yes/' /etc/frr/daemons
sed -i 's/^bfdd=no/bfdd=yes/' /etc/frr/daemons

# Copy device-specific FRR config if it exists
if [ -f /lab-config/frr.conf ]; then
    cp /lab-config/frr.conf /etc/frr/frr.conf
    chown frr:frr /etc/frr/frr.conf
fi

# Start SSH
/usr/sbin/sshd

# Stagger FRR daemon startup across containers. On a resource-constrained
# host (this lab's dev machine: a 2-core/4-thread ULV CPU with WSL2 capped
# at 2 vCPUs), starting all 10 routers' daemons (zebra/bgpd/ospfd/staticd/
# bfdd/watchfrr = 60 processes) at the exact same instant causes a
# synchronized BGP/OSPF/BFD convergence burst that starves the CPU badly
# enough to make watchfrr see simultaneous "read returned EOF" across every
# router and, on the worst runs, segfault zebra/bgpd mid-startup (signal
# 11) -- reproduced with `docker compose restart`, not just cold boot.
# Deriving a per-hostname delay (pure bash, no extra packages) spreads the
# 10 routers' daemon starts over ~0-27s instead of all landing on the same
# tick.
HOSTNAME_STR="$(hostname)"
HOSTNAME_SUM=0
for (( i=0; i<${#HOSTNAME_STR}; i++ )); do
    HOSTNAME_SUM=$(( HOSTNAME_SUM + $(printf '%d' "'${HOSTNAME_STR:$i}") ))
done
STAGGER=$(( HOSTNAME_SUM % 10 * 3 ))
echo "Staggering FRR daemon startup by ${STAGGER}s (host=${HOSTNAME_STR}) to avoid a simultaneous-convergence CPU spike"
sleep "$STAGGER"

# Start FRR
/usr/lib/frr/docker-start &

# Start CLI-over-HTTPS proxy on port 8080
python3 /usr/local/bin/cli_proxy.py &>/var/log/cli_proxy.log &

# Keep running
tail -f /dev/null
