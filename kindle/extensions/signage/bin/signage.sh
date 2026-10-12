#!/bin/sh
# サイネージ本体（start.sh から起動される）
#
# 安全のための順番:
#   1. タッチパネルを独占（touchgrab）。裏の画面にタップが届かなくなる
#   2. 独占に成功したときだけ、Kindle本来の画面を一時停止（上に重なるのを防ぐ）
#   3. 終了（3回タップ / 電源ボタン / Stop）では stop.sh が逆の順に元に戻す
. /mnt/us/extensions/signage/bin/common.sh
echo $$ > "$SIG_PID"

# 終了の合図を受けたら即座に抜ける（画面の再開は stop.sh が行う）
trap 'exit 0' TERM INT HUP
# タイマー画面から戻ったときの合図。待ちを打ち切ってすぐ描き直す
trap 'kick=1' USR1

# ---- 電源ボタン → サイネージ終了 ----
(
  lipc-wait-event com.lab126.powerd goingToScreenSaver >/dev/null 2>&1
  log "power button -> stop signage"
  run_detached "$EXT/bin/stop.sh" quiet
) &
echo $! > "$SIG_WATCH_PID"

sleep 3   # KUALの画面が閉じきるのを待つ

# ---- 1. タッチパネルを独占 ----
grabbed=0
if [ "$GRAB_TOUCH" = "1" ] && [ -f "$EXT/bin/touchgrab" ]; then
  rm -f "$TG_OUT" "$TIMER_SHOWN"
  if [ "$TIMER_ENABLE" = "1" ]; then
    # 操作は timer.sh が FIFO から読む。ここでも読み書きで開いておくと、touchgrab が書き込み口を開くときに待たされない
    rm -f "$TIMER_FIFO"; mkfifo "$TIMER_FIFO"
    exec 4<> "$TIMER_FIFO"
    sh "$EXT/bin/timer.sh" &
    set -- -e -i "$TIMER_SHOWN" -W $(screen_size)
    [ "$TOUCH_SWAP_XY" = "1" ] && set -- "$@" -s
    [ "$TOUCH_FLIP_X" = "1" ] && set -- "$@" -x
    [ "$TOUCH_FLIP_Y" = "1" ] && set -- "$@" -y
    "$EXT/bin/touchgrab" -t "$TAP_EXIT" -w 2000 "$@" > "$TIMER_FIFO" 2> "$TG_OUT" &
  else
    (
      "$EXT/bin/touchgrab" -t "$TAP_EXIT" -w 2000 > "$TG_OUT" 2>&1
      rc=$?
      if [ "$rc" = "10" ]; then
        log "tap x$TAP_EXIT -> stop signage"
        run_detached "$EXT/bin/stop.sh"
      fi
    ) &
  fi
  i=0
  while [ "$i" -lt 6 ]; do
    grep -q "grabbed" "$TG_OUT" 2>/dev/null && { grabbed=1; break; }
    sleep 1; i=$((i + 1))
  done
  log "$(cat "$TG_OUT" 2>/dev/null | tr '\n' ' ')"
  if [ "$grabbed" != "1" ]; then
    log "touch grab FAILED - UI will NOT be paused (safe mode)"
    killall touchgrab 2>/dev/null
  fi
fi

# ---- 2. 独占できたときだけ Kindle 本来の画面を一時停止 ----
if [ "$FREEZE_UI" = "1" ] && [ "$grabbed" = "1" ]; then
  killall -STOP cvm 2>/dev/null && { echo 1 > "$FROZEN_FLAG"; log "UI paused"; }
fi

# cvm を止めても上部ステータスバー（電池・時刻）は別プロセスが時刻・残量の変化のたびに描き直す。
# 旧ファームは pillow、5.16 以降は JunoStatusBarDriver が描くので両方止める（stop.sh で再開）
lipc-set-prop com.lab126.pillow disableEnablePillow disable 2>/dev/null
killall -STOP JunoStatusBarDriver 2>/dev/null && log "status bar paused"

timer_shown() { [ -f "$TIMER_SHOWN" ]; }

# 次の区切りまで待つ。USR1 で起こされたら残りの sleep は片付ける
# （描いている最中に合図が来ていたら待たない。来た合図を次の区切りまで放置しないため）
pause_until() {
  [ "$kick" = "1" ] && return 0
  sleep "$1" & _sp=$!
  wait $_sp
  kill $_sp 2>/dev/null
}

# ---- 画像スライドショー ----
if [ "$SIGNAGE_SOURCE" = "slides" ]; then
  while :; do
    kick=0
    if ! timer_shown; then
      f=$(next_image "$DATA/slides" "$CACHE/slide_index" sequential)
      if [ -n "$f" ]; then
        eips -f -g "$f" >/dev/null 2>&1
        draw_batt_icon "$(batt_state)"
      else
        eips -c; msg "No PNG images in /mnt/us/signage/slides" 10
      fi
    fi
    pause_until "$SLIDE_INTERVAL"
  done
fi

# ---- ダッシュボード ----
[ "$WIFI_AUTO_ON" = "1" ] && wifi_wait 30
last=-1; fail=0; prev=""; batt_shown=""; full=1
while :; do
  slot=$(refresh_slot)
  if [ "$slot" != "$last" ]; then
    if fetch_dashboard; then
      fail=0; last=$slot
      time_sync
      [ "$TIMER_ENABLE" = "1" ] && fetch_timer
    else
      fail=$((fail + 1))
      # 失敗したら1分後にもう一度（何度も失敗したら次の取得時刻まで待つ）
      [ "$fail" -lt 5 ] || last=$slot
    fi
    full=1
  fi
  [ "$kick" = "1" ] && full=1
  kick=0
  if timer_shown; then
    # タイマー画面の間は描かない。戻ったら全面を描き直す
    full=1
  else
    if [ "$full" = "1" ]; then
      batt_shown=""
      if draw_dashboard; then
        prev=$(now_hhmm)
        [ "$fail" -ge 3 ] && msg "offline - showing last data" 1
      else
        eips -c; msg "Cannot reach $SERVER_URL" 10; msg "Check SERVER_URL in config.sh" 12
        prev=""
      fi
      full=0
    elif [ -n "$prev" ]; then
      prev=$(draw_clock "$prev")
    fi
    batt=$(batt_state)
    if [ "$batt" != "$batt_shown" ]; then
      # 前のマークを消すには背景ごと描き直すしかない
      if [ -n "$batt_shown" ] && draw_dashboard; then
        prev=$(now_hhmm)
      fi
      draw_batt_icon "$batt"
      batt_shown=$batt
    fi
  fi
  s=$(date +%s)
  pause_until $((60 - s % 60))   # 次の「00秒」まで待つ（止められたらすぐ抜ける）
done
