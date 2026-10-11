#!/bin/sh
# サイネージ終了（3回タップ・電源ボタン・KUALの Stop から呼ばれる）
. /mnt/us/extensions/signage/bin/common.sh

# 1. Kindle本来の画面を再開し、自動スリープを元に戻す（最優先）
killall -CONT cvm 2>/dev/null
rm -f "$FROZEN_FLAG"
lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null

# 2. タッチの独占を解除（画面が再開してから解除するので、溜まったタップは無い）
killall touchgrab 2>/dev/null

# 3. サイネージの処理を止める
for f in "$SIG_PID" "$SIG_WATCH_PID"; do
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null
  rm -f "$f"
done
pkill -f "extensions/signage/bin/signage.sh" 2>/dev/null
pkill -f "lipc-wait-event com.lab126.powerd goingToScreenSaver$" 2>/dev/null
log "signage stopped"

# 4. 電源ボタンのときはそのままスリープへ。それ以外はホーム画面を描き直させる
if [ "$1" != "quiet" ]; then
  eips -c >/dev/null 2>&1
  # ホームにいたままだと画面が描き直されないので、設定画面を経由してホームへ戻る
  lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.settings 2>/dev/null
  sleep 2
  lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home 2>/dev/null
fi
exit 0
