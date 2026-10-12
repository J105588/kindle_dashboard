#!/bin/sh
# 電源ボタンでの復帰（スリープ解除）を待ち受けて同期する常駐プログラム（wake.sh から起動）
. /mnt/us/extensions/gdrive/bin/common.sh
echo $$ > "$WAKE_PID"
FIFO=/var/tmp/gdrive_wake.fifo
rm -f "$FIFO"; mkfifo "$FIFO"
lipc-wait-event -m com.lab126.powerd outOfScreenSaver > "$FIFO" 2>/dev/null &
echo $! > "$WAKE_WAIT_PID"
trap 'kill "$(cat "$WAKE_WAIT_PID" 2>/dev/null)" 2>/dev/null; exit 0' TERM INT HUP
log "wake sync listener started"
while read -r ev rest; do
  [ "$ev" = "outOfScreenSaver" ] || continue
  # 復帰直後は Wi-Fi がまだつながっていない。sync.sh --wake が最大45秒待つ
  ( sh "$GD/bin/sync.sh" --wake ) &
done < "$FIFO"
