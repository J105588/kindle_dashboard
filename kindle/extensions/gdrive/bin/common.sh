#!/bin/sh
# Google Drive 同期の共通部分
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
GD=/mnt/us/extensions/gdrive
. "$GD/config.sh"
: "${GDRIVE_REMOTE:=kgdrive:Kindle}" "${LOCAL_DIR:=/mnt/us/gdrive}" "${FILE_TYPES:=pdf}"
: "${TRASH_DIR:=/mnt/us/gdrive_trash}" "${TRASH_DAYS:=14}" "${MAX_DELETE:=20}"
: "${WIFI_AUTO_ON:=0}" "${SYNC_ON_WAKE:=1}" "${MIN_GAP_MIN:=3}"

RCLONE=${GD_RCLONE:-$GD/bin/rclone}
CONF=$GD/rclone.conf
CACERT=$GD/cacert.pem
STATE=$GD/state.txt            # 前回の結果（KOReader プラグインも読む）
LOG=$GD/gdrive.log
LOCK=/var/tmp/gdrive_sync.lock
LAST=/var/tmp/gdrive_last_sync          # 最後に同期を始めた時刻（復帰時の連続実行を防ぐ）
WAKE_PID=/var/tmp/gdrive_wake.pid       # 復帰を待ち受ける常駐プログラム
WAKE_WAIT_PID=/var/tmp/gdrive_wake_wait.pid

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
  if [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 262144 ]; then
    tail -n 600 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}
msg() { eips 1 "${2:-38}" "$1                                        " >/dev/null 2>&1; }

run_detached() {
  if command -v setsid >/dev/null 2>&1; then
    setsid sh "$@" >/dev/null 2>&1 < /dev/null &
  else
    nohup sh "$@" >/dev/null 2>&1 < /dev/null &
  fi
}
is_running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

wifi_connected() { [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ]; }
wifi_wait() {
  _i=0
  while [ "$_i" -lt "${1:-40}" ]; do
    wifi_connected && return 0
    sleep 1; _i=$((_i + 1))
  done
  return 1
}

# 状態ファイルに key=value で書く（1行目 status）
set_state() { # status message [added deleted updated]
  {
    echo "status=$1"
    echo "time=$(date '+%Y-%m-%d %H:%M')"
    echo "message=$2"
    echo "added=${3:-0}"
    echo "deleted=${4:-0}"
    echo "updated=${5:-0}"
    echo "files=$(count_files)"
  } > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}
get_state() { sed -n "s/^$1=//p" "$STATE" 2>/dev/null; }

count_files() {
  _n=0
  for _e in $FILE_TYPES; do
    _c=$(find "$LOCAL_DIR" -type f -iname "*.$_e" 2>/dev/null | wc -l)
    _n=$((_n + _c))
  done
  echo "$_n"
}

# 二重に動かないようにする（古いロックは自動で外す）
take_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  if ! kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null && { echo $$ > "$LOCK/pid"; return 0; }
  fi
  return 1
}
drop_lock() { rm -rf "$LOCK"; }
