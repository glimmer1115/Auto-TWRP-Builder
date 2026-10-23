#!/sbin/sh
# Delay in the recovery SELinux domain; init has no sleep builtin.
requested="${1:-2}"
case "$requested" in
	0|1|2|3|4|5) timeout="$requested" ;;
	*) timeout=5 ;;
esac
sleep "$timeout"
