#!/bin/sh
# タッチの操作とタイマーを受け持つ（signage.sh から起動される）
#
# touchgrab -e の出力（tap / swipe / touch / exit）と、必要なときだけ予約する刻み（tick）を同じ FIFO から読む。
# 読み手をこの1本にしておくと、タイマーの状態を書き換えるのも画面に描くのもここだけになり、取り合いが起きない。
# 時計の画面とは $TIMER_SHOWN の有無で受け渡す（あるあいだ signage.sh は描かない）
#
# 電池のため、刻みは次に表示が変わる時刻に1回だけ予約する（常に回し続けない）
#   ・タイマー画面で操作が無いまま TIMER_ECO_SEC 秒たつと秒の桁を「--」にして、分が変わるときだけ起きる
#   ・時計に戻っている間は、終わる時刻に1回起きるだけ
#   ・残り1分を切ったら、またはタップしたら秒まで表示する
. /mnt/us/extensions/signage/bin/common.sh
echo $$ > "$TIMER_PID"
TD=$EXT/img/timer

state=idle   # idle / run / pause / done
remain=0     # idle・pause のときの残り秒
preset=0     # スタートした時の秒数（リセットでここに戻す）
end=0        # run のとき終わる時刻（date +%s）
eco=0        # 1 = 秒を出さない省電力表示
flash=0      # 終わったときの点滅の残り回数
touched=$(date +%s)
command -v usleep >/dev/null 2>&1 && FAST="usleep 500000" || FAST="sleep 1"

tick_off() {
  [ -f "$TICK_PID" ] && kill "$(cat "$TICK_PID")" 2>/dev/null
  rm -f "$TICK_PID"
}

# 刻みを1回だけ予約する（前の予約は取り消す）。引数=秒、fast=秒表示用の短い間隔
tick_in() {
  tick_off
  if [ "$1" = "fast" ]; then
    ( $FAST; echo tick ) > "$TIMER_FIFO" &
  else
    ( sleep "$1"; echo tick ) > "$TIMER_FIFO" &
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

# 変わった桁だけ書き換える（$2=all で全桁）。省電力表示のときは秒の桁を dm.png（-）にする
draw_time() {
  _t=$1
  [ "$_t" -gt 5999 ] && _t=5999
  _m=$(( _t / 60 )); _s=$(( _t % 60 ))
  if [ "$eco" = "1" ]; then
    _n="$(( _m / 10 ))$(( _m % 10 ))mm"
  else
    _n="$(( _m / 10 ))$(( _m % 10 ))$(( _s / 10 ))$(( _s % 10 ))"
  fi
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

# 動作中の次の刻みを予約する
schedule() {
  [ "$state" = "run" ] || return 0
  _r=$(left)
  if [ ! -f "$TIMER_SHOWN" ]; then
    tick_in "$_r"
  elif [ "$eco" = "1" ]; then
    # 分の桁が変わる瞬間に起きる。残り1分になる時刻は越えない
    _w=$(( _r % 60 + 1 ))
    [ $(( _r - _w )) -lt 60 ] && _w=$(( _r - 60 ))
    [ "$_w" -ge 1 ] || _w=1
    tick_in "$_w"
  else
    tick_in fast
  fi
}

show() {
  load_layout || { log "timer: no timer images in $TD"; return 1; }
  touch "$TIMER_SHOWN"
  # signage.sh が描いている途中かもしれないので、少し待ってから上書きする
  sleep 1
  eco=0
  draw_screen
  schedule
  log "timer: shown"
}

hide() {
  rm -f "$TIMER_SHOWN"
  kill -USR1 "$(cat "$SIG_PID")" 2>/dev/null
  schedule
  log "timer: hidden"
}

finish() {
  state=done; remain=0; eco=0
  log "timer: done"
  if [ ! -f "$TIMER_SHOWN" ]; then
    touch "$TIMER_SHOWN"; sleep 1
  fi
  eips -f -g "$TD/done.png" >/dev/null 2>&1
  # 音が出ないので白黒の切り替えを繰り返して知らせる。1回ずつ予約するので、途中のタップでもすぐ止まる
  flash=$(( TIMER_FLASH * 2 - 2 ))
  if [ "$flash" -gt 0 ]; then tick_in 1; else tick_off; fi
}

flash_step() {
  flash=$(( flash - 1 ))
  if [ $(( flash % 2 )) = 1 ]; then
    draw_screen
  else
    eips -f -g "$TD/done.png" >/dev/null 2>&1
  fi
  if [ "$flash" -gt 0 ]; then tick_in 1; else tick_off; fi
}

dismiss() {
  tick_off
  flash=0; state=idle; remain=$preset
  draw_screen
}

# 桁ごとに上下（繰り上がりはしない）。動作中は変えない
step_digit() { # $1=桁(0-3) $2=+1/-1
  [ "$state" = "idle" ] || [ "$state" = "pause" ] || return 0
  _m=$(( remain / 60 )); _s=$(( remain % 60 ))
  _a=$(( _m / 10 )); _b=$(( _m % 10 )); _c=$(( _s / 10 )); _e=$(( _s % 10 ))
  case $1 in
    0) _a=$(( (_a + $2 + 10) % 10 )) ;;
    1) _b=$(( (_b + $2 + 10) % 10 )) ;;
    2) _c=$(( (_c + $2 + 6) % 6 )) ;;
    3) _e=$(( (_e + $2 + 10) % 10 )) ;;
  esac
  remain=$(( (_a * 10 + _b) * 60 + _c * 10 + _e ))
  draw_time "$remain"
}

press() {
  case $1 in
    up?) step_digit "${1#up}" 1 ;;
    dn?) step_digit "${1#dn}" -1 ;;
    start)
      case $state in
        idle)
          [ "$remain" -gt 0 ] || return 0
          preset=$remain
          end=$(( $(date +%s) + remain )); state=run ;;
        run)
          remain=$(left); state=pause; tick_off ;;
        pause)
          end=$(( $(date +%s) + remain )); state=run ;;
      esac
      draw_button
      schedule
      ;;
    reset)
      tick_off
      if [ "$state" = "idle" ]; then
        remain=0; preset=0
      else
        state=idle; remain=$preset
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
    tap|touch|swipe) touched=$(date +%s) ;;
  esac

  # 終わった画面では、どんな触り方でもすぐ止める（点滅の途中でも）
  if [ "$state" = "done" ]; then
    case $ev in
      tick) [ "$flash" -gt 0 ] && flash_step ;;
      tap|touch) dismiss ;;
      swipe)
        if [ "$a" = "right" ]; then
          tick_off; flash=0; state=idle; remain=$preset; hide
        else
          dismiss
        fi ;;
      exit) run_detached "$EXT/bin/stop.sh" ;;
    esac
    continue
  fi

  case $ev in
    exit)
      log "tap x$TAP_EXIT -> stop signage"
      run_detached "$EXT/bin/stop.sh"
      ;;
    tick)
      [ "$state" = "run" ] || continue
      r=$(left)
      if [ "$r" -le 0 ]; then
        finish
        continue
      fi
      if [ -f "$TIMER_SHOWN" ]; then
        if [ "$eco" = "0" ] && [ "$r" -gt 60 ] && [ $(( $(date +%s) - touched )) -ge "$TIMER_ECO_SEC" ] && [ "$TIMER_ECO_SEC" -gt 0 ]; then
          eco=1
        elif [ "$eco" = "1" ] && [ "$r" -le 60 ]; then
          eco=0
        fi
        draw_time "$r"
      fi
      schedule
      ;;
    swipe)
      if [ -f "$TIMER_SHOWN" ]; then
        [ "$a" = "right" ] && hide
      else
        [ "$a" = "left" ] && show
      fi
      ;;
    tap|touch)
      [ -f "$TIMER_SHOWN" ] || continue
      if [ "$eco" = "1" ]; then
        eco=0
        draw_time "$(left)"
        schedule
      fi
      [ "$ev" = "tap" ] || continue
      btn=$(hit "$a" "$b")
      [ "$TOUCH_DEBUG" = "1" ] && log "tap $a $b -> ${btn:-none}"
      [ -n "$btn" ] && press "$btn"
      ;;
  esac
done
