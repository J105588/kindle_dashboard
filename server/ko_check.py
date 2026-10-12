"""KOReader の PDF 表示設定を Kindle から読み出す（読むだけ・何も変更しない）
   python server\\ko_check.py   → 結果を画面に出し、server\\ko_check.txt にも保存する
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kindlectl  # noqa: E402

SCRIPT = r'''
KO=/mnt/us/koreader
KEYS='kopt_|copt_|rotation_mode|zoom|page_scroll|text_wrap|trim|crop|view_mode|document_metadata_folder|reader_footer|footer'
echo "== date"; date
echo "== koreader"; ls -d $KO 2>/dev/null || echo "koreader not found"; cat $KO/git-rev 2>/dev/null; echo
echo "== screen"; eips -i 2>/dev/null | grep -iE "xres|yres|rotate" | head -5
echo "== settings.reader.lua (display keys only)"
grep -E "$KEYS" $KO/settings.reader.lua 2>/dev/null || echo "(none)"
echo "== defaults.custom.lua"; grep -v '^ *--' $KO/defaults.custom.lua 2>/dev/null | grep -v '^ *$' || echo "(none)"
echo "== PDF files"
find /mnt/us/gdrive /mnt/us/documents -iname '*.pdf' 2>/dev/null | head -40
echo "== per-book settings"
{ find /mnt/us/gdrive /mnt/us/documents -name 'metadata.pdf.lua' 2>/dev/null
  find $KO/docsettings -name '*.lua' 2>/dev/null; } | head -20 | while read -r f; do
  echo "--- $f"
  grep -E "$KEYS|last_page|\"page\"" "$f" | head -40
done
true
'''


def main():
    _, k = kindlectl.load_cfg()
    try:
        out = kindlectl.remote(k, SCRIPT)
    except kindlectl.KindleError as e:
        out = f"エラー: {e}"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ko_check.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    print(f"\n→ {path} に保存しました")


if __name__ == "__main__":
    main()
