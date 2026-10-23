#!/sbin/sh
# Delay in the recovery SELinux domain; init has no sleep builtin.
sleep "${1:-2}"
