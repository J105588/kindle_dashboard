#!/bin/sh
# Status … 状態を画面下に表示
. /mnt/us/extensions/signage/bin/common.sh
if is_running "$SIG_PID"; then a="running"; else a="stopped"; fi
if is_running "$SS_PID"; then b="ON ($SS_SOURCE)"; else b="OFF"; fi
if download "$SERVER_URL/layout.txt" "$CACHE/ping.txt"; then c="OK"; else c="unreachable"; fi
rm -f "$CACHE/ping.txt"
ip=$(ifconfig wlan0 2>/dev/null | sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p')
msg "Kindle IP: ${ip:-not connected}" 33
msg "Signage: $a ($SIGNAGE_SOURCE)" 34
msg "Screensaver: $b" 35
msg "Server: $c  $SERVER_URL" 36
msg "Clock now: $(now_hhmm | sed 's/../&:/')" 37
msg "Slides: $(list_pngs "$DATA/slides" | wc -l)  Screensaver imgs: $(list_pngs "$DATA/screensaver" | wc -l)" 38
