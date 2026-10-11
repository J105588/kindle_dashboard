#!/bin/sh
# Screensaver: OFF（標準のスリープ画面に戻る）
. /mnt/us/extensions/signage/bin/common.sh
for f in "$SS_WAIT_PID" "$SS_PID"; do
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null
  rm -f "$f"
done
pkill -f "extensions/signage/bin/ssaver.sh" 2>/dev/null
rm -f /var/tmp/signage_ss.fifo /var/tmp/signage_ss_state /var/tmp/signage_ss_cur
grep -q " $SYS_SS " /proc/mounts 2>/dev/null && umount "$SYS_SS" 2>/dev/null
rm -rf "$SS_BIND"
log "screensaver OFF"
msg "Screensaver: OFF"
