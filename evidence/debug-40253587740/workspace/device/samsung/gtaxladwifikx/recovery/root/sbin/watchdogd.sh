#!/sbin/sh
# Exynos 7870 resets recovery in a few seconds unless sec_wdt is stopped.
while true; do
  echo 0 > /sys/devices/virtual/sec/sec_wdt/wdt_control
  echo 0 > /sys/class/sec/sec_wdt/wdt_control
  echo 1 > /sys/devices/virtual/sec/sec_wdt/wdt_pet
  echo 1 > /dev/watchdog
  sleep 2
done
