#!/sbin/sh
mark_stage() {
	stage="$1"
	printf '%s\n' "usb:$stage" >> /cache/recovery/sm-stage 2>/dev/null || true
	printf '<6>SM-T583 USB: %s\n' "$stage" > /dev/kmsg 2>/dev/null || true
}

if [ "${1:-}" = "mark" ]; then
	case "${2:-}" in
		ffs-mount-command-issued|adbd-start-command-issued|descriptor-grace-window-ended|gadget-enable-command-issued) ;;
		*) exit 0 ;;
	esac
	mark_stage "$2"
	exit 0
fi

# Delay in the recovery SELinux domain; init has no sleep builtin.
requested="${1:-2}"
case "$requested" in
	0|1|2|3|4|5) timeout="$requested" ;;
	*) timeout=5 ;;
esac
mark_stage "delay-${timeout}s-start"
sleep "$timeout"
mark_stage "delay-${timeout}s-end"
