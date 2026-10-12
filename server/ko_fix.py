"""KOReader で PDF を「1ページ＝1画面」で表示するように設定する
   python server\\ko_fix.py            … ページ全体を画面に収める（余白も含めて全部見える）
   python server\\ko_fix.py --content  … 余白を除いた中身を画面に収める（少し文字が大きくなる）

   ・すでに開いた PDF の設定（*.sdr）と、これから開く PDF の既定値の両方を直す
   ・読んでいたページ（last_page）やしおりはそのまま
   ・KOReader が起動中だと終了時に上書きされるので、KOReader を閉じてから実行する
   ・変更前のファイルは Kindle の /mnt/us/koreader_bak/<日時>/ に保存する
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kindlectl  # noqa: E402

SCRIPT = r'''
KO=/mnt/us/koreader
G=$KO/settings.reader.lua
if ps w 2>/dev/null | grep -v grep | grep -q "reader.lua"; then
  echo "KOREADER_RUNNING"; exit 0
fi
[ -f "$G" ] || { echo "settings.reader.lua が見つかりません"; exit 1; }
BAK=/mnt/us/koreader_bak/$(date +%Y%m%d-%H%M%S)
mkdir -p "$BAK"

# 一番外側（字下げ4つ）のキーだけを書きかえる。無ければ「return {」の直後に足す
setkey() { # file key value
  if grep -q "^    \[\"$2\"\] = " "$1"; then
    sed -i "s#^    \[\"$2\"\] = .*#    [\"$2\"] = $3,#" "$1"
  else
    sed -i "s#^return {\$#return {\n    [\"$2\"] = $3,#" "$1"
  fi
}
fix() { # file
  setkey "$1" kopt_page_scroll 0          # 表示モード: ページ（連続スクロールをやめる）
  setkey "$1" kopt_text_wrap 0            # リフロー: オフ
  setkey "$1" kopt_zoom_mode_genus GENUS  # ズーム対象
  setkey "$1" kopt_zoom_mode_type 2       # 合わせ方: 全体
  setkey "$1" zoom_mode '"ZMODE"'
  setkey "$1" normal_zoom_mode '"ZMODE"'
  setkey "$1" kopt_rotation_mode 0        # 縦向き
}
check() { # file backup
  if "$KO/luajit" -e "assert(loadfile('$1'))" >/dev/null 2>&1; then echo "OK  $1"
  else cp "$2" "$1"; echo "NG（元に戻しました） $1"; fi
}

n=0
cp "$G" "$BAK/settings.reader.lua"
fix "$G"; check "$G" "$BAK/settings.reader.lua"
find /mnt/us/gdrive /mnt/us/documents -name 'metadata.pdf.lua' 2>/dev/null | while read -r f; do
  n=$((n+1)); b="$BAK/book$n.lua"
  cp "$f" "$b"; echo "$f" >> "$BAK/books.txt"
  fix "$f"; check "$f" "$b"
done
echo "バックアップ: $BAK"
true
'''


def main():
    content = "--content" in sys.argv
    genus, zmode = ("3", "content") if content else ("4", "page")
    script = SCRIPT.replace("GENUS", genus).replace("ZMODE", zmode)
    _, k = kindlectl.load_cfg()
    try:
        out = kindlectl.remote(k, script)
    except kindlectl.KindleError as e:
        sys.exit(f"エラー: {e}")
    if "KOREADER_RUNNING" in out:
        sys.exit("KOReader が起動中です。KOReader を終了してから（メニュー → 終了）もう一度実行してください。")
    print(out)
    print("完了: KOReader を起動して PDF を開くと、1ページが1画面に収まって表示されます"
          + ("（余白を除いた中身に合わせています）" if content else ""))


if __name__ == "__main__":
    main()
