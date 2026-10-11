#!/bin/sh
# スクリーンセーバー常駐プログラム（ss_on.sh から起動される）
#
# 方式 native（auto で使える場合）:
#   Kindle 標準のスリープ画像の場所に「次に見せたい画像」を置いておく。
#   Kindle 自身がその画像を描くので、標準画像が一瞬出ることも、描き直しも無い。
#   このプログラムは、起きるたびに次の画像を置き直すのと、念のための見張りだけをする。
#
# 方式 draw（native が使えない場合・dashboard の場合）:
#   スリープ画面の画面内容を見張り、Kindle が標準画像を描き終わったのを確認してから
#   1回だけ描く（自分→標準→自分 にならない）。その後また上書きされたときだけ描き直す。
. /mnt/us/extensions/signage/bin/common.sh
echo $$ > "$SS_PID"

STATE=/var/tmp/signage_ss_state   # 1=スリープ画面中 / 0=通常
CUR=/var/tmp/signage_ss_cur       # 今表示する（次に表示する）画像
FIFO=/var/tmp/signage_ss.fifo
FB=${SS_FB:-/dev/fb0}
echo 0 > "$STATE"

rm -f "$FIFO"; mkfifo "$FIFO"
lipc-wait-event -m com.lab126.powerd goingToScreenSaver,outOfScreenSaver,readyToSuspend,wakeupFromSuspend > "$FIFO" 2>/dev/null &
echo $! > "$SS_WAIT_PID"

in_ss()  { [ "$(cat "$STATE" 2>/dev/null)" = "1" ]; }
native() { grep -q " $SYS_SS " /proc/mounts 2>/dev/null; }
nap()    { usleep 200000 2>/dev/null || sleep 0.2 2>/dev/null || sleep 1; }

# 画面（フレームバッファ）の中身の指紋。描かれたかどうかの判定に使う
fb_sum() { dd if="$FB" bs=65536 count=32 2>/dev/null | md5sum | cut -c1-32; }

cur_file() {
  f=$(cat "$CUR" 2>/dev/null)
  [ "$f" = "dashboard" ] && f="$CACHE/dashboard.png"
  echo "$f"
}

# 次に見せる画像を決め、native なら Kindle の標準画像の場所にも置く
prepare_next() {
  if [ "$SS_SOURCE" = "dashboard" ] && [ -f "$CACHE/dashboard.png" ]; then
    echo dashboard > "$CUR"
  else
    next_image "$DATA/screensaver" "$CACHE/ss_index" "$SS_ORDER" > "$CUR" || : > "$CUR"
  fi
  native && [ -s "$CUR" ] && [ "$(cat "$CUR")" != "dashboard" ] && ss_link "$(cat "$CUR")"
}

# 描く。$1 = full（フラッシュあり）/ quiet（フラッシュなし）
paint() {
  in_ss || return 1
  f=$(cur_file)
  [ -n "$f" ] && [ -f "$f" ] || return 1
  if [ "$1" = "full" ]; then eips -f -g "$f" >/dev/null 2>&1; else eips -g "$f" >/dev/null 2>&1; fi
  [ "$(cat "$CUR")" = "dashboard" ] && draw_clock "" >/dev/null
  return 0
}

# Kindle が標準のスリープ画面を描き終わるのを待つ
#   画面が変化して、その後 0.6 秒ほど落ち着いたら「描き終わった」とみなす
#   （イベントの時点で既に描き終わっていた場合は 2 秒で打ち切る。最大 8 秒）
wait_settle() {   # 引数 changed = もう変化が起きたことが分かっている
  last=$(fb_sum); changed=0; stable=0; i=0
  [ "$1" = "changed" ] && changed=1
  while [ "$i" -lt 40 ]; do
    nap; i=$((i + 1))
    in_ss || return 1
    now=$(fb_sum)
    if [ "$now" != "$last" ]; then changed=1; stable=0; last=$now; else stable=$((stable + 1)); fi
    [ "$changed" = 1 ] && [ "$stable" -ge 3 ] && return 0
    [ "$changed" = 0 ] && [ "$i" -ge 10 ] && return 0
  done
  return 0
}

on_sleep() {
  wait_settle || return
  if native; then
    # 標準画像の場所に自分の画像を置いてあるので、Kindle が既に描いているはず。
    # 念のためフラッシュなしで重ねる（同じ画像なら見た目は何も変わらない。広告付き端末などの保険）
    paint quiet
  else
    paint full
  fi
  mine=$(fb_sum)
  # その後しばらく見張り、また上書きされたときだけ描き直す
  j=0
  while [ "$j" -lt 25 ]; do
    nap; j=$((j + 1))
    in_ss || return
    if [ "$(fb_sum)" != "$mine" ]; then
      wait_settle changed || return
      paint full; mine=$(fb_sum)
      log "screensaver: redrawn after overwrite"
    fi
  done
}

trap 'exit 0' TERM INT HUP
prepare_next   # 最初のスリープ用の画像を用意

while read ev rest; do
  case "$ev" in
    goingToScreenSaver)
      echo 1 > "$STATE"
      on_sleep &
      log "screensaver: $(cat "$CUR") ($(native && echo native || echo draw))"
      ;;
    readyToSuspend)
      # 眠る直前: 画面が自分の画像でなければ描き直す（フラッシュなし）
      in_ss && paint quiet
      if [ "$SS_SOURCE" = "dashboard" ] && [ "$SS_REFRESH_MIN" -gt 0 ]; then
        lipc-set-prop -i com.lab126.powerd rtcWakeup $((SS_REFRESH_MIN * 60)) 2>/dev/null
      fi
      ;;
    wakeupFromSuspend)
      if in_ss && [ "$SS_SOURCE" = "dashboard" ] && [ "$SS_REFRESH_MIN" -gt 0 ]; then
        lipc-set-prop -i com.lab126.powerd deferSuspend 60 2>/dev/null
        wifi_wait 30 && fetch_dashboard
        paint full
        log "screensaver refreshed"
      fi
      ;;
    outOfScreenSaver)
      echo 0 > "$STATE"
      ( sleep 3; prepare_next ) &   # 次のスリープ用の画像を用意（起きてから少し後に）
      ;;
  esac
done < "$FIFO"
