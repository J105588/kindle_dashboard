# =====================================================================
#  Google Drive 同期の設定（ここだけ編集すればOK・LFで保存）
#  python kindlectl.py gdrive-set 項目 値  でも変えられます
# =====================================================================

# 同期元: 「rclone のリモート名:Google Drive 上のフォルダ」
# kindlectl.py gdrive-setup がリモート kgdrive を作ります。フォルダは Drive に先に作っておく
GDRIVE_REMOTE="kgdrive:Kindle"

# 同期先（KOReader で開くフォルダ。Kindle 標準のライブラリには出ない場所）
LOCAL_DIR=/mnt/us/gdrive

# 同期するファイルの種類（拡張子をスペース区切り。大文字小文字は区別しない）
FILE_TYPES="pdf"

# Drive から消えたファイルは、すぐ消さずにここへ移す（KOReader の読書記録も一緒に）
TRASH_DIR=/mnt/us/gdrive_trash
TRASH_DAYS=14          # この日数を過ぎたら本当に消す（0=ゴミ箱を使わずすぐ消す）

# 一度の同期で消してよい最大数（Drive の障害などで大量に消えるのを防ぐ）
MAX_DELETE=20

# ---- 同期のタイミング ----
# 自動で同期するのは「電源ボタンを押して画面がついたとき（スリープからの復帰時）」だけ。
# それ以外は手動: KOReader / KUAL のメニュー、PC の kindlectl.py gdrive-sync
SYNC_ON_WAKE=1          # 1=復帰時に同期する（KOReader / KUAL のメニューでも切り替え可）
MIN_GAP_MIN=3           # 前回の同期からこの分数以内の復帰では同期しない（連続で押したとき用）

# Wi-Fi がオフのとき、同期のために一時的にオンにする（1=する。終わったら元に戻す）
WIFI_AUTO_ON=0
