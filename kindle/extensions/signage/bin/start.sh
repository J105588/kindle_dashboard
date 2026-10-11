#!/bin/sh
# Signage: Start
. /mnt/us/extensions/signage/bin/common.sh

if is_running "$SIG_PID"; then
  msg "Signage is already running"; exit 0
fi

lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null   # 自動スリープを止める
if [ "$SIGNAGE_SOURCE" = "dashboard" ] && [ "$WIFI_AUTO_ON" = "1" ]; then
  lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
fi
chmod +x "$EXT/bin/touchgrab" 2>/dev/null

log "signage start ($SIGNAGE_SOURCE)"
run_detached "$EXT/bin/signage.sh"
exit 0
