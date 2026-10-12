#!/bin/sh
# 共通の関数（他のスクリプトから読み込んで使う）
# SSH から呼ばれたときも KUAL と同じコマンドが使えるように PATH をそろえる
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
EXT=/mnt/us/extensions/signage
DATA=/mnt/us/signage
CACHE=$DATA/.cache
LOG=$DATA/signage.log
SIG_PID=/var/tmp/signage_loop.pid
SIG_WATCH_PID=/var/tmp/signage_watch.pid
SS_PID=/var/tmp/signage_ss.pid
SS_WAIT_PID=/var/tmp/signage_ss_wait.pid
TG_OUT=/var/tmp/signage_touchgrab.out
FROZEN_FLAG=/var/tmp/signage_frozen
SYS_SS=/usr/share/blanket/screensaver   # Kindle標準のスリープ画像の場所
SS_BIND=/var/tmp/signage_ssbind         # native 方式で標準画像の代わりに見せるフォルダ（RAM上）
TIMER_PID=/var/tmp/signage_timer.pid
TICK_PID=/var/tmp/signage_tick.pid
TIMER_FIFO=/var/tmp/signage_input.fifo
TIMER_SHOWN=/var/tmp/signage_timer_shown   # タイマー画面を出している間だけある（signage.sh は描かない）

. "$EXT/config.sh"
# 古い config.sh に無い項目の既定値
: "${SS_DELAY:=1}" "${SS_REDRAW:=3}" "${FREEZE_UI:=1}" "${SS_REFRESH_MIN:=0}"
: "${GRAB_TOUCH:=1}" "${TAP_EXIT:=3}" "${SS_METHOD:=auto}" "${BATT_LOW_PCT:=15}"
: "${BATT_CHG_ICON:=1}" "${TIMER_ENABLE:=1}" "${TIMER_FLASH:=3}" "${TIMER_FLASH_REST:=5}" "${TIMER_FLASH_MIN:=5}" "${TIMER_ECO_SEC:=15}" "${TOUCH_DEBUG:=0}"
: "${TOUCH_SWAP_XY:=0}" "${TOUCH_FLIP_X:=0}" "${TOUCH_FLIP_Y:=0}"
: "${NTP_SERVER=ntp.nict.jp}" "${TIME_SYNC_MIN:=60}" "${DASH_REFRESH_OFFSET:=9}"
TIME_SYNC_STAMP=/var/tmp/signage_timesync
mkdir -p "$CACHE/clock" "$DATA/slides" "$DATA/screensaver"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
  # ログは 200KB を超えたら後半だけ残す
  if [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 204800 ]; then
    tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}

# 画面の下の方に1行メッセージ（英数字のみ）
msg() { eips 1 "${2:-38}" "$1                              " >/dev/null 2>&1; }

# 呼び出し元から切り離してスクリプトを実行
run_detached() {
  if command -v setsid >/dev/null 2>&1; then
    setsid sh "$@" >/dev/null 2>&1 < /dev/null &
  else
    nohup sh "$@" >/dev/null 2>&1 < /dev/null &
  fi
}

is_running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# ---- ネットワーク ----
download() { # URL 保存先
  rm -f "$2.part"
  if command -v curl >/dev/null 2>&1; then
    # 古いファームの CA 証明書では GitHub Pages などの HTTPS を検証できないことがあるので、
    # 失敗したときだけ検証なしで取り直す（取るのは公開画像だけ）
    curl -s -f -L -m 25 -o "$2.part" "$1" ||
      case $1 in https:*) curl -s -f -L -k -m 25 -o "$2.part" "$1" ;; esac
  else
    wget -q -O "$2.part" "$1"
  fi
  [ -s "$2.part" ] && mv "$2.part" "$2" && return 0
  rm -f "$2.part"; return 1
}

wifi_wait() { # 最大 $1 秒、Wi-Fi接続を待つ
  i=0
  while [ "$i" -lt "${1:-30}" ]; do
    [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ] && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

# サーバーからダッシュボード一式を取得（時計の数字は版が変わったときだけ）
fetch_dashboard() {
  download "$SERVER_URL/layout.txt" "$CACHE/layout.new" || { log "server unreachable: $SERVER_URL"; return 1; }
  newver=$(awk '{print $6}' "$CACHE/layout.new")
  oldver=$(awk '{print $6}' "$CACHE/layout.txt" 2>/dev/null)
  if [ "$newver" != "$oldver" ] || [ ! -f "$CACHE/clock/colon.png" ]; then
    for n in 0 1 2 3 4 5 6 7 8 9 colon; do
      download "$SERVER_URL/clock/$n.png" "$CACHE/clock/$n.png" || { log "sprite $n failed"; return 1; }
    done
    log "clock sprites updated ($newver)"
  fi
  mv "$CACHE/layout.new" "$CACHE/layout.txt"
  download "$SERVER_URL/dashboard.png" "$CACHE/dashboard.png" || { log "dashboard download failed"; return 1; }
  return 0
}

# Kindle の時計は数秒ずれていくことがあるので、TIME_SYNC_MIN ごとに NTP で合わせる
time_sync() {
  [ -n "$NTP_SERVER" ] && command -v ntpdate >/dev/null 2>&1 || return 0
  _last=$(cat "$TIME_SYNC_STAMP" 2>/dev/null || echo 0)
  [ $(( $(date +%s) - _last )) -ge $((TIME_SYNC_MIN * 60)) ] || return 0
  # -u: 他の時刻サービスと 123 番ポートを取り合わない / -b: 少しずつではなく一度で合わせる
  if _r=$(ntpdate -b -u -p 2 "$NTP_SERVER" 2>&1); then
    log "time sync: $(echo "$_r" | grep -o 'offset [-0-9.]*' | tail -1)"
  else
    log "time sync failed: $(echo "$_r" | tail -1)"
  fi
  date +%s > "$TIME_SYNC_STAMP"
}

# 背景を取り直す区切りの番号。開始時刻からではなく時計の分にそろえて進めることで、
# GitHub Actions が公開し終わった直後（10分間隔・オフセット9なら 09,19,…,59分）に取りに行ける
refresh_slot() {
  _m=$(( ($(date +%s) + TZ_OFFSET_MIN * 60) / 60 - DASH_REFRESH_OFFSET ))
  echo $(( _m / DASH_REFRESH_MIN ))
}

# ---- 時計 ----
now_hhmm() {
  s=$(( $(date +%s) + TZ_OFFSET_MIN * 60 ))
  printf '%02d%02d' $(( (s / 3600) % 24 )) $(( (s / 60) % 60 ))
}

# 変わった桁だけ書き換える。引数=前回の時刻(HHMM)、空なら全桁。新しい時刻を出力
draw_clock() {
  [ -f "$CACHE/layout.txt" ] || return 1
  read _X _Y _DW _CW _H _V < "$CACHE/layout.txt"
  _t=$(now_hhmm); _prev=$1
  for _pos in 1 2 3 4; do
    _d=$(echo "$_t" | cut -c$_pos)
    _p=$(echo "$_prev" | cut -c$_pos)
    case $_pos in
      1) _x=$_X ;;
      2) _x=$((_X + _DW)) ;;
      3) _x=$((_X + _DW * 2 + _CW)) ;;
      4) _x=$((_X + _DW * 3 + _CW)) ;;
    esac
    if [ "$_d" != "$_p" ]; then
      eips -g "$CACHE/clock/$_d.png" -x "$_x" -y "$_Y" >/dev/null 2>&1
    fi
  done
  echo "$_t"
}

draw_dashboard() { # 全面リフレッシュで背景を描き、時計を重ねる
  [ -f "$CACHE/dashboard.png" ] || return 1
  eips -f -g "$CACHE/dashboard.png" >/dev/null 2>&1
  draw_clock "" >/dev/null
}

# ---- 電池残量 ----
batt_level() {
  _b=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
  [ -n "$_b" ] || _b=$(gasgauge-info -c 2>/dev/null | tr -dc '0-9')
  echo "${_b:-100}"
}

# 出すマークの種類（charging / low / 空）。充電中は残量が少なくても charging を優先
batt_state() {
  if [ "$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)" = "1" ]; then
    [ "$BATT_CHG_ICON" = "1" ] && echo charging
    return 0
  fi
  [ "$BATT_LOW_PCT" -gt 0 ] 2>/dev/null && [ "$(batt_level)" -le "$BATT_LOW_PCT" ] 2>/dev/null && echo low
  return 0
}

# 画面の幅と高さ（eips -i の表記が機種で違っても拾えるよう、行の中を探す）
screen_size() {
  eips -i 2>/dev/null | awk '{for (i = 1; i < NF; i++) {
    if ($i == "xres:" && !x) x = $(i + 1); if ($i == "yres:" && !y) y = $(i + 1) }}
    END {print (x ? x : 1072), (y ? y : 1448)}'
}

# 右上に電池マーク。引数は batt_state の値（x は 8 の倍数にそろえる）
draw_batt_icon() {
  [ -n "$1" ] && [ -f "$EXT/img/batt_$1.png" ] || return 1
  _w=$(eips -i 2>/dev/null | awk '$1=="xres:"{print $2; exit}')
  _x=$(( (${_w:-1072} - 136) / 8 * 8 ))
  eips -g "$EXT/img/batt_$1.png" -x "$_x" -y 24 >/dev/null 2>&1
}

# ---- スクリーンセーバー native 方式 ----
# 標準画像と同じ名前で、指定の画像を SS_BIND に並べる（ハードリンクなので RAM は画像1枚分）
ss_link() {
  [ -f "$1" ] || return 1
  mkdir -p "$SS_BIND"
  cp "$1" "$SS_BIND/.cur.tmp" && mv "$SS_BIND/.cur.tmp" "$SS_BIND/.cur.png" || return 1
  for name in $(cat "$SS_BIND/.names" 2>/dev/null); do
    ln -f "$SS_BIND/.cur.png" "$SS_BIND/$name" 2>/dev/null || cp "$SS_BIND/.cur.png" "$SS_BIND/$name"
  done
}

# ---- 画像フォルダ ----
list_pngs() { ls "$1"/*.png "$1"/*.PNG 2>/dev/null | sort; }

# フォルダから次の画像を選ぶ（順番 or ランダム）。引数: フォルダ 状態ファイル 並び順
next_image() {
  n=$(list_pngs "$1" | wc -l)
  [ "$n" -gt 0 ] || return 1
  if [ "$3" = "random" ]; then
    i=$(awk -v n="$n" 'BEGIN{srand(); print int(rand()*n)+1}')
  else
    i=$(( $(cat "$2" 2>/dev/null || echo 0) % n + 1 ))
    echo "$i" > "$2"
  fi
  list_pngs "$1" | sed -n "${i}p"
}
