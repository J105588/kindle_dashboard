# =====================================================================
#  Signage 設定ファイル（ここだけ編集すればOK）
#  ※ 保存するときは改行コード「LF」で保存してください
# =====================================================================

# ---- 配信サーバー（PCで serve.py を起動したときに表示されるURL）----
SERVER_URL="http://192.168.1.134:8080"
# PC なしで使う場合（GitHub Actions が描画して公開。README「PCなしで動かす」参照）
#SERVER_URL="https://j105588.github.io/kindle_dashboard"

# ---- サイネージ（Signage: Start で起動）----
# dashboard … 時計・カレンダー・天気（サーバーが必要）
# slides    … /mnt/us/signage/slides の画像を順番に表示（サーバー不要）
SIGNAGE_SOURCE=dashboard

DASH_REFRESH_MIN=10     # 背景（天気・カレンダー）を取り直す間隔（分）。60 を割り切れる値に
DASH_REFRESH_OFFSET=9   # 取り直す分のずれ。10分間隔なら 09,19,29…分に取る（GitHub Actions の公開直後）
SLIDE_INTERVAL=60       # slides のとき 1枚あたりの秒数

# Kindle内部の時計は世界標準時(UTC)のことが多いので、日本時間との差を分で指定
# 時計が9時間ずれる場合は 0 に変えてください
TZ_OFFSET_MIN=540

# 時計のずれを直す時刻サーバー（空にすると合わせない）と、合わせる間隔（分）
NTP_SERVER=ntp.nict.jp
TIME_SYNC_MIN=60

# サイネージ中はタッチパネルを独占して、裏の画面にタップが届かないようにする（1=する）
GRAB_TOUCH=1
# サイネージを終了するタップ回数（2秒以内にこの回数タップで終了。0=タップで終了しない）
TAP_EXIT=3
# タッチを独占できたときだけ、Kindle 本来の画面を一時停止して上に重なるのを防ぐ（1=する）
FREEZE_UI=1

# 時計の画面で右から左へスワイプするとタイマー画面を出す（1=出す / 0=出さない）。左から右へのスワイプで戻る
TIMER_ENABLE=1
# タイマーが終わったときに白黒を切り替えて知らせる回数
TIMER_FLASH=3
# 動作画面で操作が無いままこの秒数たつと、秒を「--」にして画面の書き換えを分ごとに減らす（電池の節約。0=しない）
# 残り1分を切るか、画面をタップすると秒の表示に戻る
TIMER_ECO_SEC=15
# タップの位置がずれる場合の補正（1=する）。TOUCH_DEBUG=1 にすると signage.log にタップの座標が出る
TOUCH_SWAP_XY=0
TOUCH_FLIP_X=0
TOUCH_FLIP_Y=0
TOUCH_DEBUG=0

# 電池残量がこの値(%)以下になったら右上に電池マークを出す（充電中は出さない。0=出さない）
BATT_LOW_PCT=15
# 充電中は右上に充電マークを出す（1=出す / 0=出さない）
BATT_CHG_ICON=1

# Signage: Start 時に Wi-Fi を自動でONにする（1=する / 0=しない）
WIFI_AUTO_ON=1

# ---- スクリーンセーバー（Screensaver: ON / OFF で切り替え）----
# images    … /mnt/us/signage/screensaver の画像を表示（スリープのたびに次の画像）
# dashboard … サーバーのダッシュボードを表示
SS_SOURCE=images
SS_ORDER=sequential     # sequential=順番 / random=ランダム
# 表示の方式
# auto   … 使えるなら Kindle 自身に自分の画像を表示させる（標準画像が一瞬出ない・ちらつかない）
#          使えない場合（dashboard 表示など）は自動で draw になる
# draw   … スリープ画面の上から描く（標準画像が出た後、1回だけ切り替わる）
SS_METHOD=auto

# dashboard のとき、スリープ中に何分ごとに起きて更新するか（0=更新しない）
# 起きるたびにWi-Fiを使うので電池を消費します。充電しながらの利用がおすすめ
SS_REFRESH_MIN=0
