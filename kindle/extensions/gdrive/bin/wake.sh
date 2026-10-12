#!/bin/sh
# 電源ボタンでの復帰時の同期を切り替える
#   sh wake.sh on      … 有効にして待ち受けを始める（設定も 1 にする）
#   sh wake.sh off     … 無効にして待ち受けを止める（設定も 0 にする）
#   sh wake.sh ensure  … 設定が 1 なら待ち受けが動いていることを確かめる（再起動後の復旧用）
#   sh wake.sh stop    … 設定はそのままで待ち受けだけ止める（更新時）
#   sh wake.sh status  … on / off を出力
#   --silent を付けると画面に出さない
. /mnt/us/extensions/gdrive/bin/common.sh
SILENT=0; [ "$2" = "--silent" ] && SILENT=1
say() { [ "$SILENT" = "1" ] || msg "$1"; }
set_conf() { sed -i "s/^SYNC_ON_WAKE=[0-9]*/SYNC_ON_WAKE=$1/" "$GD/config.sh"; grep -q '^SYNC_ON_WAKE=' "$GD/config.sh" || echo "SYNC_ON_WAKE=$1" >> "$GD/config.sh"; }
start() { is_running "$WAKE_PID" || run_detached "$GD/bin/wakesync.sh"; }
stop() {
  for f in "$WAKE_WAIT_PID" "$WAKE_PID"; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; done
  pkill -f "extensions/gdrive/bin/wakesync.sh" 2>/dev/null
  rm -f /var/tmp/gdrive_wake.fifo
}
case "$1" in
  on)     set_conf 1; start; say "GDrive: sync on power button ON" ;;
  off)    set_conf 0; stop;  say "GDrive: sync on power button OFF" ;;
  ensure) [ "$SYNC_ON_WAKE" = "1" ] && start ;;
  stop)   stop ;;
  status) if [ "$SYNC_ON_WAKE" = "1" ] && is_running "$WAKE_PID"; then echo on; elif [ "$SYNC_ON_WAKE" = "1" ]; then echo "on(not running)"; else echo off; fi ;;
esac
exit 0
