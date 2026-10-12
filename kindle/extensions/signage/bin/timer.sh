#!/bin/sh
# タッチの操作とタイマーを受け持つ（signage.sh から起動される）
# 画面は iPhone の時計アプリのタイマーにならって、設定画面（時間・分・秒のホイール）と動作画面（リング）の2つ
#
# touchgrab -e の出力（tap / swipe / touch / exit）と、必要なときだけ予約する刻み（tick）を同じ FIFO から読む。
# 読み手をこの1本にしておくと、タイマーの状態を書き換えるのも画面に描くのもここだけになり、取り合いが起きない。
# 時計の画面とは $TIMER_SHOWN の有無で受け渡す（あるあいだ signage.sh は描かない）
#
# 電池のため、刻みは次に表示が変わる時刻に1回だけ予約する（常に回し続けない）
#   ・動作画面で操作が無いまま TIMER_ECO_SEC 秒たつと秒の桁を「--」にして、分かリングが変わるときだけ起きる
#   ・時計に戻っている間は、終わる時刻に1回起きるだけ
#   ・残り1分を切ったら、またはタップしたら秒まで表示する
. /mnt/us/extensions/signage/bin/common.sh
echo $$ > "$TIMER_PID"
TD=$EXT/img/timer

state=idle   # idle（設定画面）/ run / pause / done
H=0; M=0; S=0  # 設定画面のホイールの値（動作中も保持して、キャンセル・終了後にこの値へ戻る）
remain=0     # pause のときの残り秒
preset=0     # スタートした時の秒数（リングの割合に使う）
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

# layout.txt の位置を変数に読み込む（btn の行は hit で都度引く）
load_layout() {
  [ -f "$TD/layout.txt" ] && [ -f "$TD/set_bg.png" ] || return 1
  eval "$(awk '
    $1=="col"    {print "COL_" $2 "_X=" $3 "; COL_" $2 "_Y=" $4}
    $1=="wheel"  {print "WHEEL_CY=" $2 "; ROW=" $3}
    $1=="sprite" {print "RX=" $3 "; RY=" $4}
    $1=="big"    {print "BIG_DW=" $2 "; BIG_CW=" $3 "; BIG_Y=" $4 "; BIG_CX=" $5}
    $1=="end"    {print "END_X=" $2 "; END_Y=" $3 "; END_DW=" $4 "; END_CW=" $5}
    $1=="ring"   {s = "RING_N=" $2; for (i = 3; i < NF; i += 3) s = s "; RING_" $i "_X=" $(i+1) "; RING_" $i "_Y=" $(i+2); print s}
  ' "$TD/layout.txt")"
  [ -n "$COL_h_X" ] && [ -n "$BIG_DW" ] && [ -n "$RING_N" ]
}

total() { echo $(( H * 3600 + M * 60 + S )); }

left() {
  if [ "$state" = "run" ]; then
    _r=$(( end - $(date +%s) ))
    [ "$_r" -gt 0 ] || _r=0
    echo "$_r"
  else
    echo "$remain"
  fi
}

img() { eips -g "$TD/$1.png" -x "$2" -y "$3" >/dev/null 2>&1; }

# ---- 設定画面
draw_wheel() { # $1 = h / m / s
  case $1 in
    h) _v=$H; _f=wh; _x=$COL_h_X; _y=$COL_h_Y ;;
    m) _v=$M; _f=wm; _x=$COL_m_X; _y=$COL_m_Y ;;
    s) _v=$S; _f=wm; _x=$COL_s_X; _y=$COL_s_Y ;;
  esac
  [ "$_v" -lt 10 ] && _v=0$_v
  img "${_f}_$_v" "$_x" "$_y"
}

draw_right() {
  case $state in
    run) _b=pause ;;
    pause) _b=resume ;;
    done) return 0 ;;
    *) [ "$(total)" -gt 0 ] && _b=start || _b=start_off ;;
  esac
  [ "$_b" = "$right" ] && return 0
  img "$_b" "$RX" "$RY"
  right=$_b
}

draw_set() {
  eips -f -g "$TD/set_bg.png" >/dev/null 2>&1
  right=start_off   # 背景に描いてある
  draw_wheel h; draw_wheel m; draw_wheel s
  draw_right
}

# ホイールを n 段まわす（端は反対側へ回り込む）
turn() { # $1 = h / m / s, $2 = 段数（+で増える）
  case $1 in
    h) H=$(( ((H + $2) % 24 + 24) % 24 )) ;;
    m) M=$(( ((M + $2) % 60 + 60) % 60 )) ;;
    s) S=$(( ((S + $2) % 60 + 60) % 60 )) ;;
    *) return 0 ;;
  esac
  draw_wheel "$1"
  draw_right
}

# ---- 動作画面
fmt_time() { # 残り秒 → 「1:05:30」など。形は動作中に変えない（スタート時の長さで決める）
  _h=$(( $1 / 3600 )); _m=$(( $1 % 3600 / 60 )); _s=$(( $1 % 60 ))
  if [ "$eco" = "1" ]; then _ss="--"; else _ss="$(( _s / 10 ))$(( _s % 10 ))"; fi
  case $fmt in
    hh) echo "$(( _h / 10 ))$(( _h % 10 )):$(( _m / 10 ))$(( _m % 10 )):$_ss" ;;
    h)  echo "$_h:$(( _m / 10 ))$(( _m % 10 )):$_ss" ;;
    *)  echo "$(( _m / 10 ))$(( _m % 10 )):$_ss" ;;
  esac
}

# 変わった文字だけ書き換える（$2=all で全部）。fork を増やさないよう1文字ずつ切り出す
draw_big() {
  _n=$(fmt_time "$1")
  [ "$2" = "all" ] && _o="" || _o=$big
  case $fmt in
    hh) _w=$(( BIG_DW * 6 + BIG_CW * 2 )) ;;
    h)  _w=$(( BIG_DW * 5 + BIG_CW * 2 )) ;;
    *)  _w=$(( BIG_DW * 4 + BIG_CW )) ;;
  esac
  _x=$(( (BIG_CX - _w / 2) / 8 * 8 ))
  big=$_n
  while [ -n "$_n" ]; do
    _c=${_n%"${_n#?}"}; _n=${_n#?}
    _p=${_o%"${_o#?}"}; _o=${_o#?}
    case $_c in
      :) _f=dc; _a=$BIG_CW ;;
      -) _f=dm; _a=$BIG_DW ;;
      *) _f=d$_c; _a=$BIG_DW ;;
    esac
    [ "$_c" = "$_p" ] || img "$_f" "$_x" "$BIG_Y"
    _x=$(( _x + _a ))
  done
}

ring_step() { # 残り秒 → リングの段（切り上げ。0 は終わったときだけ）
  [ "$preset" -gt 0 ] || { echo 0; return; }
  echo $(( ($1 * RING_N + preset - 1) / preset ))
}

draw_ring() {
  _k=$(ring_step "$1")
  [ "$_k" = "$ringk" ] && return 0
  for _b in t b l r; do
    eval "img ring_${_k}_$_b \"\$RING_${_b}_X\" \"\$RING_${_b}_Y\""
  done
  ringk=$_k
}

# 終了予定時刻（一時停止中は薄く、今から再開したときの時刻を出す）
draw_end() {
  if [ "$state" = "run" ]; then _e=$end; _g=e; else _e=$(( $(date +%s) + remain )); _g=g; fi
  _e=$(( _e + TZ_OFFSET_MIN * 60 ))
  _hh=$(( (_e / 3600) % 24 )); _mm=$(( (_e / 60) % 60 ))
  _t="$_g$(( _hh / 10 ))$(( _hh % 10 ))$(( _mm / 10 ))$(( _mm % 10 ))"
  [ "$_t" = "$endshown" ] && return 0
  _d=${_t#?}
  img "$_g${_d%???}" "$END_X" "$END_Y"; _d=${_d#?}
  img "$_g${_d%??}" $(( END_X + END_DW )) "$END_Y"; _d=${_d#?}
  img "$_g${_d%?}" $(( END_X + END_DW * 2 + END_CW )) "$END_Y"; _d=${_d#?}
  img "$_g$_d" $(( END_X + END_DW * 3 + END_CW )) "$END_Y"
  endshown=$_t
}

draw_run() {
  eips -f -g "$TD/run_bg.png" >/dev/null 2>&1
  ringk=$RING_N; endshown=""; right=""   # 背景にはいっぱいのリングが描いてある
  _r=$(left)
  draw_big "$_r" all
  draw_ring "$_r"
  draw_end
  draw_right
}

draw_screen() {
  case $state in
    idle) draw_set ;;
    done) eips -f -g "$TD/done.png" >/dev/null 2>&1 ;;
    *) draw_run ;;
  esac
}

# 動作中の次の刻みを予約する
schedule() {
  [ "$state" = "run" ] || return 0
  _r=$(left)
  if [ ! -f "$TIMER_SHOWN" ]; then
    tick_in "$_r"
  elif [ "$eco" = "1" ]; then
    # 分の桁かリングが変わる瞬間に起きる。残り1分になる時刻は越えない
    _w=$(( _r % 60 + 1 ))
    [ $(( _r - _w )) -lt 60 ] && _w=$(( _r - 60 ))
    _k=$(ring_step "$_r")
    _wr=$(( _r - (_k - 1) * preset / RING_N ))
    [ "$_wr" -lt "$_w" ] && _w=$_wr
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

start() {
  preset=$(total)
  [ "$preset" -gt 0 ] || return 0
  if [ "$preset" -ge 36000 ]; then fmt=hh; elif [ "$preset" -ge 3600 ]; then fmt=h; else fmt=ms; fi
  end=$(( $(date +%s) + preset )); state=run; eco=0
  draw_run
  schedule
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
    draw_run
  else
    eips -f -g "$TD/done.png" >/dev/null 2>&1
  fi
  if [ "$flash" -gt 0 ]; then tick_in 1; else tick_off; fi
}

to_set() { # 動作をやめて設定画面へ（ホイールはスタートしたときの値のまま）
  tick_off
  flash=0; state=idle; remain=0
  [ -f "$TIMER_SHOWN" ] && draw_set
}

hit() { # 座標にあるボタンの名前
  awk -v x="$1" -v y="$2" '$1=="btn" && x>=$3 && x<=$5 && y>=$4 && y<=$6 {print $2; exit}' "$TD/layout.txt"
}

press() { # $1 = ボタン名, $2 = タップした y
  case $state:$1 in
    idle:up_?)  turn "${1#up_}" 1 ;;
    idle:dn_?)  turn "${1#dn_}" -1 ;;
    idle:whl_?)
      # 上下の数字をタップすると、その数字まで回す（上ほど大きい）
      _d=$(( $2 - WHEEL_CY ))
      if [ "$_d" -lt 0 ]; then _n=$(( (ROW / 2 - _d) / ROW )); else _n=$(( -((_d + ROW / 2) / ROW) )); fi
      [ "$_n" != 0 ] && turn "${1#whl_}" "$_n"
      ;;
    idle:left)  H=0; M=0; S=0; draw_wheel h; draw_wheel m; draw_wheel s; draw_right ;;
    idle:right) start ;;
    run:right)  remain=$(left); state=pause; tick_off; draw_right; draw_end ;;
    pause:right)
      end=$(( $(date +%s) + remain )); state=run
      draw_right; draw_end; schedule ;;
    run:left|pause:left) to_set ;;
  esac
}

load_layout
exec 3<> "$TIMER_FIFO"
while read -r ev a b c d <&3; do
  [ "$TOUCH_DEBUG" = "1" ] && [ "$ev" != "tick" ] && log "input: $ev $a $b $c $d"
  case $ev in
    tap|touch|swipe) touched=$(date +%s) ;;
  esac

  # 終わった画面では、どんな触り方でもすぐ止める（点滅の途中でも）
  if [ "$state" = "done" ]; then
    case $ev in
      tick) [ "$flash" -gt 0 ] && flash_step ;;
      tap|touch) to_set ;;
      swipe)
        if [ "$a" = "right" ]; then
          tick_off; flash=0; state=idle; hide
        else
          to_set
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
        if [ "$eco" = "0" ] && [ "$TIMER_ECO_SEC" -gt 0 ] && [ "$r" -gt 60 ] &&
           [ $(( $(date +%s) - touched )) -ge "$TIMER_ECO_SEC" ]; then
          eco=1
        elif [ "$eco" = "1" ] && [ "$r" -le 60 ]; then
          eco=0
        fi
        draw_big "$r"
        draw_ring "$r"
      fi
      schedule
      ;;
    swipe)
      if [ ! -f "$TIMER_SHOWN" ]; then
        [ "$a" = "left" ] && show
      elif [ "$a" = "right" ]; then
        hide
      elif [ "$state" = "idle" ] && { [ "$a" = "up" ] || [ "$a" = "down" ]; }; then
        # ホイールの列を上下にスワイプ: 長さに応じて回す（下へ引くと上の大きい数が来る）
        btn=$(hit "$b" "$c")
        col=${btn#*_}
        case $btn in
          whl_?|up_?|dn_?)
            n=$(( (${d:-0} + ROW / 2) / ROW )); [ "$n" -ge 1 ] || n=1
            [ "$a" = "up" ] && n=$(( -n ))
            turn "$col" "$n" ;;
        esac
      fi
      ;;
    tap|touch)
      [ -f "$TIMER_SHOWN" ] || continue
      if [ "$eco" = "1" ]; then
        eco=0
        draw_big "$(left)"
        schedule
      fi
      [ "$ev" = "tap" ] || continue
      btn=$(hit "$a" "$b")
      [ "$TOUCH_DEBUG" = "1" ] && log "tap $a $b -> ${btn:-none}"
      [ -n "$btn" ] && press "$btn" "$b"
      ;;
  esac
done
