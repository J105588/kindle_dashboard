#!/bin/sh
# Screensaver: ON
. /mnt/us/extensions/signage/bin/common.sh

if is_running "$SS_PID"; then
  msg "Screensaver: already ON"; exit 0
fi
if [ "$SS_SOURCE" = "images" ] && [ "$(list_pngs "$DATA/screensaver" | wc -l)" -eq 0 ]; then
  msg "No PNG in /mnt/us/signage/screensaver"; exit 0
fi

# ---- native 方式: Kindle の標準スリープ画像の場所に、自分の画像を見せる ----
# 本体のファイルは一切書き換えない（RAM 上のフォルダを上に重ねるだけ）。OFF か再起動で必ず元に戻る
method=draw
if [ "$SS_METHOD" != "draw" ] && [ "$SS_SOURCE" = "images" ]; then
  grep -q " $SYS_SS " /proc/mounts 2>/dev/null && umount "$SYS_SS" 2>/dev/null
  names=$(ls "$SYS_SS" 2>/dev/null | grep -i '\.png$')
  if [ -n "$names" ]; then
    rm -rf "$SS_BIND"; mkdir -p "$SS_BIND"
    echo "$names" > "$SS_BIND/.names"
    ss_link "$(list_pngs "$DATA/screensaver" | head -1)"
    if mount --bind "$SS_BIND" "$SYS_SS" 2>/dev/null; then
      method=native
    else
      log "native: mount failed -> draw"; rm -rf "$SS_BIND"
    fi
  else
    log "native: no stock images in $SYS_SS -> draw"
  fi
fi

# 見張り役（native でも保険として動かす）
[ "$SS_SOURCE" = "dashboard" ] && fetch_dashboard
run_detached "$EXT/bin/ssaver.sh"
log "screensaver ON ($method, $SS_SOURCE)"
msg "Screensaver: ON ($method)"
