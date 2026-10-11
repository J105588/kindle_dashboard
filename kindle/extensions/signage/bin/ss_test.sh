#!/bin/sh
# Screensaver: Preview … スリープ画面に出る画像を今すぐ表示（10秒後にホームへ戻る）
. /mnt/us/extensions/signage/bin/common.sh
sleep 2
if [ "$SS_SOURCE" = "dashboard" ]; then
  fetch_dashboard; draw_dashboard || msg "Cannot reach $SERVER_URL" 10
else
  f=$(next_image "$DATA/screensaver" "$CACHE/ss_index" "$SS_ORDER")
  if [ -n "$f" ]; then eips -f -g "$f" >/dev/null 2>&1; else eips -c; msg "No PNG in /mnt/us/signage/screensaver" 10; fi
fi
sleep 10
eips -c >/dev/null 2>&1
lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home 2>/dev/null
