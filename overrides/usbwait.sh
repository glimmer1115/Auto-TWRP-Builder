#!/sbin/sh
# Block init until adbd has written the functionfs descriptors.
# watchdogd.sh already runs under u:r:recovery:s0 through /sbin/sh.
sleep 2
