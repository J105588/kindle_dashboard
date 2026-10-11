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
  rm -f "$TG_OUT"
  (
    "$EXT/bin/touchgrab" -t "$TAP_EXIT" -w 2000 > "$TG_OUT" 2>&1
    rc=$?
    if [ "$rc" = "10" ]; then
      log "tap x$TAP_EXIT -> stop signage"
      run_detached "$EXT/bin/stop.sh"
    fi
  ) &
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

# ---- 画像スライドショー ----
if [ "$SIGNAGE_SOURCE" = "slides" ]; then
  while :; do
    f=$(next_image "$DATA/slides" "$CACHE/slide_index" sequential)
    if [ -n "$f" ]; then
      eips -f -g "$f" >/dev/null 2>&1
      batt_low && draw_batt_icon
    else
      eips -c; msg "No PNG images in /mnt/us/signage/slides" 10
    fi
    sleep "$SLIDE_INTERVAL" & wait $!
  done
fi

# ---- ダッシュボード ----
[ "$WIFI_AUTO_ON" = "1" ] && wifi_wait 30
last=0; fail=0; prev=""; batt_on=0
while :; do
  now=$(date +%s)
  if [ $((now - last)) -ge $((DASH_REFRESH_MIN * 60)) ]; then
    if fetch_dashboard; then
      fail=0; last=$now
    else
      fail=$((fail + 1))
      # 失敗したら1分後にもう一度（何度も失敗したら間隔を空ける）
      if [ "$fail" -lt 5 ]; then last=$((now - DASH_REFRESH_MIN * 60 + 60)); else last=$now; fi
    fi
    batt_on=0
    if draw_dashboard; then
      prev=$(now_hhmm)
      [ "$fail" -ge 3 ] && msg "offline - showing last data" 1
    else
      eips -c; msg "Cannot reach $SERVER_URL" 10; msg "Check SERVER_URL in config.sh" 12
      prev=""
    fi
  elif [ -n "$prev" ]; then
    prev=$(draw_clock "$prev")
  fi
  if batt_low; then
    [ "$batt_on" = "1" ] || { draw_batt_icon; batt_on=1; }
  elif [ "$batt_on" = "1" ]; then
    # 充電などで回復したらマークを消すために描き直す
    batt_on=0
    draw_dashboard && prev=$(now_hhmm)
  fi
  s=$(date +%s)
  sleep $((60 - s % 60)) & wait $!   # 次の「00秒」まで待つ（止められたらすぐ抜ける）
done
