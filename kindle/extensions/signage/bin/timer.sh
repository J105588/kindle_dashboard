#!/bin/sh
# タッチの操作とタイマーを受け持つ（signage.sh から起動される）
#
# touchgrab -e の出力（tap / swipe / exit）と、動作中だけ動かす刻み（tick）を同じ FIFO から読む。
# 読み手をこの1本にしておくと、タイマーの状態を書き換えるのも画面に描くのもここだけになり、取り合いが起きない。
# 時計の画面とは $TIMER_SHOWN の有無で受け渡す（あるあいだ signage.sh は描かない）
. /mnt/us/extensions/signage/bin/common.sh
echo $$ > "$TIMER_PID"
TD=$CACHE/timer

state=idle   # idle / run / pause / done
remain=0     # idle・pause のときの残り秒
preset=0     # スタートした時の秒数（リセットでここに戻す）
end=0        # run のとき終わる時刻（date +%s）
shown=-1     # 画面に出ている秒数（-1 = 描き直しが必要）

tick_off() {
  [ -f "$TICK_PID" ] && kill "$(cat "$TICK_PID")" 2>/dev/null
  rm -f "$TICK_PID"
}

tick_on() {
  is_running "$TICK_PID" && return 0
  # 1秒ちょうどで刻むと処理の分だけ遅れて表示の秒が飛ぶので、使えるなら 0.25秒ごとに見て変わったときだけ描く
  if command -v usleep >/dev/null 2>&1; then
    ( while :; do usleep 250000; echo tick; done ) > "$TIMER_FIFO" &
  else
    ( while :; do sleep 1; echo tick; done ) > "$TIMER_FIFO" &
  fi
  echo $! > "$TICK_PID"
}

load_layout() {
  [ -f "$TD/layout.txt" ] && [ -f "$TD/bg.png" ] || return 1
  set -- $(awk '$1=="digits"{print $2, $3, $4, $5}' "$TD/layout.txt")
  TX=$1; TY=$2; TDW=$3; TCW=$4
  set -- $(awk '$1=="sprite" && $2=="start"{print $3, $4}' "$TD/layout.txt")
  SX=$1; SY=$2
  [ -n "$TX" ] && [ -n "$SX" ]
}

left() {
  if [ "$state" = "run" ]; then
    _r=$(( end - $(date +%s) ))
    [ "$_r" -gt 0 ] || _r=0
    echo "$_r"
  else
    echo "$remain"
  fi
}

# 変わった桁だけ書き換える（$2=all で全桁）
draw_time() {
  _t=$1
  [ "$_t" -gt 5999 ] && _t=5999
  _m=$(( _t / 60 )); _s=$(( _t % 60 ))
  _n="$(( _m / 10 ))$(( _m % 10 ))$(( _s / 10 ))$(( _s % 10 ))"
  [ "$2" = "all" ] && _o="" || _o=$digits
  for _pos in 1 2 3 4; do
    _d=$(echo "$_n" | cut -c$_pos)
    [ "$_d" = "$(echo "$_o" | cut -c$_pos)" ] && continue
    case $_pos in
      1) _x=$TX ;;
      2) _x=$((TX + TDW)) ;;
      3) _x=$((TX + TDW * 2 + TCW)) ;;
      4) _x=$((TX + TDW * 3 + TCW)) ;;
    esac
    eips -g "$TD/d$_d.png" -x "$_x" -y "$TY" >/dev/null 2>&1
  done
  digits=$_n
  shown=$1
}

draw_button() {
  case $state in
    run) _b=pause ;;
    pause) _b=resume ;;
    *) _b=start ;;
  esac
  eips -g "$TD/$_b.png" -x "$SX" -y "$SY" >/dev/null 2>&1
}

draw_screen() {
  eips -f -g "$TD/bg.png" >/dev/null 2>&1
  draw_time "$(left)" all
  draw_button
}

show() {
  if ! load_layout; then
    fetch_timer
    load_layout || { log "timer: no timer images (server too old?)"; return 1; }
  fi
  touch "$TIMER_SHOWN"
  # signage.sh が描いている途中かもしれないので、少し待ってから上書きする
  sleep 1
  draw_screen
  log "timer: shown"
}

hide() {
  rm -f "$TIMER_SHOWN"
  kill -USR1 "$(cat "$SIG_PID")" 2>/dev/null
  log "timer: hidden"
}

finish() {
  state=done; remain=0
  tick_off
  log "timer: done"
  load_layout || return 0
  [ -f "$TIMER_SHOWN" ] || { touch "$TIMER_SHOWN"; sleep 1; }
  # 音が出ないので、白黒の全面切り替えを繰り返して目に付くようにする
  _i=0
  while [ "$_i" -lt "$TIMER_FLASH" ]; do
    eips -f -g "$TD/done.png" >/dev/null 2>&1; sleep 1
    draw_screen; sleep 1
    _i=$((_i + 1))
  done
  eips -f -g "$TD/done.png" >/dev/null 2>&1
}

dismiss() {
  state=idle; remain=$preset
  draw_screen
}

press() {
  case $1 in
    add*)
      _n=${1#add}
      if [ "$state" = "run" ]; then
        _r=$(left)
        [ $(( _r + _n )) -gt 5999 ] && _n=$(( 5999 - _r ))
        end=$(( end + _n ))
      else
        remain=$(( remain + _n ))
        [ "$remain" -gt 5999 ] && remain=5999
      fi
      draw_time "$(left)"
      ;;
    start)
      case $state in
        idle)
          [ "$remain" -gt 0 ] || return 0
          preset=$remain
          end=$(( $(date +%s) + remain )); state=run; tick_on ;;
        run)
          remain=$(left); state=pause; tick_off ;;
        pause)
          end=$(( $(date +%s) + remain )); state=run; tick_on ;;
      esac
      draw_button
      ;;
    reset)
      if [ "$state" = "idle" ]; then
        remain=0; preset=0
      else
        tick_off; state=idle; remain=$preset
      fi
      draw_time "$remain"
      draw_button
      ;;
  esac
}

hit() { # タップした座標にあるボタンの名前
  awk -v x="$1" -v y="$2" '$1=="btn" && x>=$3 && x<=$5 && y>=$4 && y<=$6 {print $2; exit}' "$TD/layout.txt"
}

load_layout
exec 3<> "$TIMER_FIFO"
while read -r ev a b <&3; do
  [ "$TOUCH_DEBUG" = "1" ] && [ "$ev" != "tick" ] && log "input: $ev $a $b"
  case $ev in
    exit)
      log "tap x$TAP_EXIT -> stop signage"
      run_detached "$EXT/bin/stop.sh"
      ;;
    tick)
      [ "$state" = "run" ] || { tick_off; continue; }
      r=$(left)
      if [ "$r" -le 0 ]; then
        finish
      elif [ -f "$TIMER_SHOWN" ] && [ "$r" != "$shown" ]; then
        draw_time "$r"
      fi
      ;;
    swipe)
      if [ -f "$TIMER_SHOWN" ]; then
        [ "$a" = "right" ] || continue
        [ "$state" = "done" ] && { state=idle; remain=$preset; }
        hide
      else
        [ "$a" = "left" ] && show
      fi
      ;;
    tap)
      [ -f "$TIMER_SHOWN" ] || continue
      if [ "$state" = "done" ]; then
        dismiss
        continue
      fi
      btn=$(hit "$a" "$b")
      [ "$TOUCH_DEBUG" = "1" ] && log "tap $a $b -> ${btn:-none}"
      [ -n "$btn" ] && press "$btn"
      ;;
  esac
done
