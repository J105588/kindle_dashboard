#!/usr/bin/env python3
"""
写真・イラストを Kindle で綺麗に表示できる PNG に変換する

  python convert_images.py 写真フォルダ
      → ./kindle_images に 001_xxx.png … を出力

  python convert_images.py 写真フォルダ --kindle E:/ --to screensaver
      → Kindle の signage/screensaver に直接書き込む（Mac なら --kindle /Volumes/Kindle）

主なオプション
  --device pw3|voyage|kt2|pw2   機種（解像度）。既定は config.json の device
  --fit cover|contain           cover=画面いっぱいに切り抜き（既定） / contain=全体を収めて余白
  --bg white|black              contain の余白の色
  --rotate none|auto            auto=横長の画像を90°回して縦画面いっぱいに使う
  --no-dither                   ディザ（点描）を使わない。イラスト・文字向け
  --contrast 1.15  --brightness 1.05  --sharpen 1.0   見た目の調整
"""
import argparse
import json
import os
import re
import shutil
import sys

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICES = {"pw3": (1072, 1448), "voyage": (1072, 1448), "kt2": (600, 800), "pw2": (758, 1024)}
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}

try:  # iPhone の HEIC にも対応（pip install pillow-heif を入れた場合）
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass


def default_device():
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("device", "pw3")
    except Exception:
        return "pw3"


def collect(inputs):
    files = []
    for p in inputs:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if os.path.splitext(name)[1].lower() in EXTS:
                    files.append(os.path.join(p, name))
        elif os.path.isfile(p):
            files.append(p)
    return files


def safe_name(path, i):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:40]  # Kindleで扱いやすい英数字だけに
    return f"{i:03d}_{stem or 'image'}.png"


# E-ink の16階調パレット
PALETTE = Image.new("P", (1, 1))
PALETTE.putpalette(sum(([v, v, v] for v in range(0, 256, 17)), []) + [0, 0, 0] * (256 - 16))


def to_eink(img, size, a):
    W, H = size
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")

    if a.rotate == "auto" and img.width > img.height:
        img = img.rotate(90, expand=True)

    if a.fit == "cover":
        img = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    else:
        img = ImageOps.contain(img, (W, H), Image.LANCZOS)
        canvas = Image.new("L", (W, H), 255 if a.bg == "white" else 0)
        canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
        img = canvas

    # E-ink は中間調が暗く沈みやすいので、少し明るく・メリハリを付ける
    img = ImageOps.autocontrast(img, cutoff=0.5)
    if a.brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(a.brightness)
    if a.contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(a.contrast)
    if a.sharpen > 0:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(80 * a.sharpen), threshold=2))

    # 16階調に落とす（ディザあり＝写真向けでグラデーションが滑らか）
    dither = Image.Dither.NONE if a.no_dither else Image.Dither.FLOYDSTEINBERG
    return img.convert("RGB").quantize(palette=PALETTE, dither=dither).convert("L")


def main():
    ap = argparse.ArgumentParser(description="画像を Kindle 用 PNG に変換")
    ap.add_argument("inputs", nargs="+", help="画像ファイルまたはフォルダ")
    ap.add_argument("-o", "--out", default="kindle_images", help="出力フォルダ（既定: kindle_images）")
    ap.add_argument("--device", default=default_device(), choices=DEVICES)
    ap.add_argument("--fit", default="cover", choices=["cover", "contain"])
    ap.add_argument("--bg", default="white", choices=["white", "black"])
    ap.add_argument("--rotate", default="none", choices=["none", "auto"])
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--contrast", type=float, default=1.15)
    ap.add_argument("--brightness", type=float, default=1.05)
    ap.add_argument("--sharpen", type=float, default=1.0)
    ap.add_argument("--kindle", help="Kindle のドライブ（例: E:/ や /Volumes/Kindle）")
    ap.add_argument("--to", default="screensaver", choices=["screensaver", "slides"],
                    help="--kindle と一緒に使う: 書き込み先フォルダ")
    a = ap.parse_args()

    files = collect(a.inputs)
    if not files:
        sys.exit("変換できる画像が見つかりませんでした")
    out = os.path.join(a.kindle, "signage", a.to) if a.kindle else a.out
    if a.kindle and not os.path.isdir(os.path.join(a.kindle, "documents")):
        sys.exit(f"{a.kindle} は Kindle のドライブではないようです（documents フォルダがありません）")
    os.makedirs(out, exist_ok=True)

    size = DEVICES[a.device]
    ok = 0
    for i, path in enumerate(files, 1):
        try:
            with Image.open(path) as im:
                res = to_eink(im, size, a)
            dst = os.path.join(out, safe_name(path, i))
            res.save(dst, "PNG", optimize=True)
            ok += 1
            print(f"  ✓ {os.path.basename(path)} → {os.path.basename(dst)}")
        except Exception as e:
            print(f"  ✗ {os.path.basename(path)}: {e}")
    print(f"{ok}/{len(files)} 枚を変換しました（{size[0]}×{size[1]}） → {out}")


if __name__ == "__main__":
    main()
