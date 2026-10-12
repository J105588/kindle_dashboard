#!/bin/sh
# Google Drive → Kindle 同期（一方向ミラー）
#   Drive に追加 → Kindle に入る / Drive から削除 → Kindle からも消える（ゴミ箱へ）
#   KOReader の読書記録（*.sdr）は同期の対象外なので消えない
#
#   sh sync.sh          … 同期して終わる
#   sh sync.sh --bg          … 裏で同期（KOReader の「今すぐ同期」から）
#   sh sync.sh --wake        … 電源ボタンでの復帰時（Wi-Fi が無い・前回から MIN_GAP_MIN 分以内なら何もしない）
#   sh sync.sh --bg --wake   … 同上を裏で（KOReader の復帰時）
#   sh sync.sh --ui          … 終わったら画面下に結果を出す（KUAL から）
. /mnt/us/extensions/gdrive/bin/common.sh

MODE=$1
if [ "$MODE" = "--bg" ]; then
  if [ "$2" = "--wake" ]; then run_detached "$GD/bin/sync.sh" --wake; else run_detached "$GD/bin/sync.sh" --run; fi
  exit 0
fi

# 復帰時: 少し前に同期したばかりなら何もしない（状態ファイルも変えない）
if [ "$MODE" = "--wake" ]; then
  last=$(cat "$LAST" 2>/dev/null || echo 0)
  [ $(( $(date +%s) - last )) -lt $((MIN_GAP_MIN * 60)) ] && exit 0
fi

if ! take_lock; then
  [ "$MODE" = "--ui" ] && msg "GDrive: sync is already running"
  exit 0
fi
date +%s > "$LAST"
trap 'drop_lock' EXIT INT TERM HUP

fail() { # message
  set_state error "$1"
  log "ERROR: $1"
  [ "$MODE" = "--ui" ] && msg "GDrive: ERROR $1"
  exit 1
}

[ -x "$RCLONE" ] || chmod +x "$RCLONE" 2>/dev/null
[ -f "$RCLONE" ] || fail "rclone not installed (run kindlectl.py gdrive-setup)"
[ -f "$CONF" ]   || fail "not authorized (run kindlectl.py gdrive-setup)"

set_state running "syncing" "$(get_state added)" "$(get_state deleted)" "$(get_state updated)"
[ "$MODE" = "--ui" ] && msg "GDrive: syncing..."

# ---- Wi-Fi ----
wifi_was_off=0
if ! wifi_connected; then
  if [ "$WIFI_AUTO_ON" = "1" ]; then
    wifi_was_off=1
    lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
    wifi_wait 40 || { lipc-set-prop com.lab126.cmd wirelessEnable 0 2>/dev/null; fail "no Wi-Fi"; }
  elif [ "$MODE" = "--wake" ] && ! wifi_wait 45; then
    set_state "$(get_state status | sed 's/running/ok/')" "skipped (no Wi-Fi)" \
      "$(get_state added)" "$(get_state deleted)" "$(get_state updated)"
    exit 0
  else
    wifi_wait 15 || fail "no Wi-Fi"
  fi
fi
wifi_restore() { [ "$wifi_was_off" = "1" ] && lipc-set-prop com.lab126.cmd wirelessEnable 0 2>/dev/null; }

# 時計が大きくずれていると HTTPS が失敗するので、合わせられるなら合わせる
if [ "$(date +%Y)" -lt 2025 ] && command -v ntpdate >/dev/null 2>&1; then
  ntpdate -b -u -p 2 ntp.nict.jp >/dev/null 2>&1 && log "clock fixed by ntp"
fi

export GOGC=50 SSL_CERT_FILE="$CACERT" RCLONE_CACHE_DIR=/var/tmp/gdrive_cache
mkdir -p "$LOCAL_DIR"

# 同期する種類だけを対象にする（それ以外＝KOReader の .sdr などは触らない）
set -- --config "$CONF" --ignore-case --contimeout 30s --timeout 120s \
       --retries 3 --low-level-retries 5 --transfers 2 --checkers 4 \
       --buffer-size 4M --use-mmap --drive-skip-gdocs
for e in $FILE_TYPES; do set -- "$@" --filter "+ *.$e"; done
set -- "$@" --filter "- *"
[ -f "$CACERT" ] && set -- "$@" --ca-cert "$CACERT"

# ---- 安全確認: 一度にたくさん消えることにならないか、同期の前に数える ----
RUNLOG=/var/tmp/gdrive_run.log
LIST=/var/tmp/gdrive_list.txt
"$RCLONE" lsf -R --files-only "$GDRIVE_REMOTE" "$@" > "$LIST" 2>"$RUNLOG"
lsf_rc=$?
remote_n=$(wc -l < "$LIST")
if [ "$lsf_rc" -ne 0 ]; then
  wifi_restore
  err=$(grep -m1 "ERROR\|Failed\|not found" "$RUNLOG" | sed 's/.*: //' | cut -c1-80)
  fail "cannot read Drive: ${err:-unknown error}"
fi
local_n=$(count_files)
# Kindle にあって Drive に無いファイル＝今回消えるファイル（名前で比べる）
LOCAL_LIST=/var/tmp/gdrive_local.txt
: > "$LOCAL_LIST"
for e in $FILE_TYPES; do
  find "$LOCAL_DIR" -type f -iname "*.$e" 2>/dev/null | sed "s#^$LOCAL_DIR/##" >> "$LOCAL_LIST"
done
to_delete=$(awk 'NR == FNR { r[$0] = 1; next } !($0 in r)' "$LIST" "$LOCAL_LIST" | wc -l)
if [ "$to_delete" -gt "$MAX_DELETE" ]; then
  wifi_restore
  fail "stopped: $to_delete files would be deleted (MAX_DELETE=$MAX_DELETE)"
fi

# ---- 同期 ----
RUN_TRASH=""
if [ "$TRASH_DAYS" -gt 0 ] 2>/dev/null; then
  RUN_TRASH="$TRASH_DIR/$(date +%Y%m%d-%H%M%S)"
  set -- "$@" --backup-dir "$RUN_TRASH"
fi
log "sync start: $GDRIVE_REMOTE -> $LOCAL_DIR (drive $remote_n / kindle $local_n)"
: > "$RUNLOG"
"$RCLONE" sync "$GDRIVE_REMOTE" "$LOCAL_DIR" "$@" --max-delete "$MAX_DELETE" \
  --log-level INFO --log-file "$RUNLOG" --stats 0
rc=$?
wifi_restore

# 結果を数える（rclone のログから）
# 追加 = Copied (new) のうち置き換えでないもの / 更新 = 置き換え / 削除 = バックアップへ移動 or Deleted
counts=$(awk '
  / INFO  : / {
    line = $0; sub(/^.* INFO  : /, "", line)
    n = split(line, p, ": "); what = p[n]; name = substr(line, 1, length(line) - length(what) - 2)
    if (what == "Copied (new)") c[name] = 1
    else if (what == "Copied (replaced existing)") r[name] = 1
    else if (what == "Moved (server-side)") m[name] = 1
    else if (what == "Moved into backup dir") b[name] = 1
    else if (what == "Deleted") d[name] = 1
  }
  END {
    add = 0; upd = 0; del = 0
    for (k in c) { if ((k in m) && !(k in b)) upd++; else add++ }
    for (k in r) upd++
    for (k in b) del++
    for (k in d) del++
    print add, del, upd
  }' "$RUNLOG")
set -- $counts
added=${1:-0}; deleted=${2:-0}; updated=${3:-0}
cat "$RUNLOG" >> "$LOG"

if [ "$rc" -ne 0 ]; then
  err=$(grep -m1 "ERROR" "$RUNLOG" | sed 's/^.*ERROR : //' | cut -c1-80)
  set_state error "rclone error $rc: ${err:-see gdrive.log}" "$added" "$deleted" "$updated"
  log "sync failed (rc=$rc)"
  [ "$MODE" = "--ui" ] && msg "GDrive: ERROR (rc=$rc) see gdrive.log"
  exit 1
fi

# ---- 後片付け ----
# 本体が無くなった KOReader の読書記録（book.sdr）をゴミ箱へ
find "$LOCAL_DIR" -type d -name "*.sdr" 2>/dev/null | while read -r sdr; do
  base=${sdr%.sdr}; found=0
  for e in $FILE_TYPES; do
    ls "$base".* 2>/dev/null | grep -qi "\.$e\$" && { found=1; break; }
  done
  if [ "$found" = "0" ]; then
    if [ -n "$RUN_TRASH" ]; then
      rel=${sdr#"$LOCAL_DIR"/}
      mkdir -p "$RUN_TRASH/$(dirname "$rel")" && mv "$sdr" "$RUN_TRASH/$rel"
    else
      rm -rf "$sdr"
    fi
    log "orphan sidecar removed: $sdr"
  fi
done
# 中身が無くなったフォルダを消す（同期先の中だけ）
find "$LOCAL_DIR" -depth -mindepth 1 -type d 2>/dev/null | while read -r d; do rmdir "$d" 2>/dev/null; done
# 古いゴミ箱を空にする
if [ -n "$RUN_TRASH" ] && [ -d "$TRASH_DIR" ]; then
  find "$TRASH_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$TRASH_DAYS" 2>/dev/null | while read -r d; do rm -rf "$d"; done
fi

set_state ok "synced" "$added" "$deleted" "$updated"
log "sync done: +$added -$deleted ~$updated (files $(count_files))"
[ "$MODE" = "--ui" ] && msg "GDrive: OK  +$added  -$deleted  ~$updated  ($(count_files) files)"
exit 0
