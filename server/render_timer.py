#!/usr/bin/env python3
"""
タイマー画面の画像を描いて Kindle の拡張フォルダに入れる（サーバー不要で動くよう、画像は拡張に同梱する）

出力（../kindle/extensions/signage/img/timer/）:
  bg.png                     … 背景（見出し・矢印・リセット・コロン）
  d0.png〜d9.png             … 数字（変わった桁だけ差し替える）
  dm.png                     … 省電力表示で秒の桁に出す「-」
  start/pause/resume.png     … スタートボタンの3つの状態
  done.png                   … 終わったときの画面
  layout.txt                 … 数字の位置とボタンの枠（timer.sh がタップの判定に使う）

使い方:
  python render_timer.py               … config.json の機種で描く
  python render_timer.py --device kt2  … 機種を指定（描いたら kindlectl.py update で送る）
"""
import argparse
import math
import os

from PIL import Image, ImageDraw

from render import (BLACK, DEVICES, GRAY, HERE, SS, WHITE, Canvas, Fonts, build_clock, ceil8,
                    floor8, load_config)

OUT = os.path.join(HERE, "..", "kindle", "extensions", "signage", "img", "timer")
DIGIT_TOP, DIGIT_SIZE = 340, 300
ARROW_H, ARROW_GAP = 140, 20


def button(c, x1, y1, x2, y2, label, size, primary=False):
    fill, fg = (BLACK, WHITE) if primary else (WHITE, BLACK)
    c.d.rounded_rectangle([c.X(x1), c.Y(y1), c.X(x2), c.Y(y2)], radius=c.P(24), fill=fill,
                          outline=BLACK, width=int(c.P(4)))
    c.text((x1 + x2) / 2, (y1 + y2) / 2, label, "bold", size, fg, anchor="mm")


def arrow(c, x1, y1, x2, y2, up):
    c.d.rounded_rectangle([c.X(x1), c.Y(y1), c.X(x2), c.Y(y2)], radius=c.P(20), fill=WHITE,
                          outline=BLACK, width=int(c.P(4)))
    cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, 72, 42
    tip, base = (cy - h / 2, cy + h / 2) if up else (cy + h / 2, cy - h / 2)
    c.d.polygon([(c.X(cx), c.Y(tip)), (c.X(cx - w / 2), c.Y(base)), (c.X(cx + w / 2), c.Y(base))], fill=BLACK)


def px_box(c, x1, y1, x2, y2):
    """基準座標の枠 → 実ピクセル。eips で差し替える部分は x と幅を 8 の倍数にそろえる"""
    return (floor8(c.ox + x1 * c.s), int(c.oy + y1 * c.s),
            min(c.w, ceil8(c.ox + x2 * c.s)), int(math.ceil(c.oy + y2 * c.s)))


def render_timer(device):
    W, H = DEVICES.get(device, DEVICES["pw3"])
    fonts = Fonts()
    c = Canvas(W, H, fonts)
    c.text(72, 110, "タイマー", "bold", 44, BLACK)
    c.text(1000, 110, "右へスワイプで時計に戻る", "regular", 24, GRAY, anchor="rs")
    c.rule(72, 140, 1000, 3)

    sprites, lay, digit_h = build_clock(c, fonts, 0, DIGIT_TOP, DIGIT_SIZE)
    lay["x"] = floor8((W - (lay["dw"] * 4 + lay["cw"])) / 2)

    buttons = []
    up_y = DIGIT_TOP - ARROW_GAP - ARROW_H
    dn_y = DIGIT_TOP + digit_h + ARROW_GAP
    for i in range(4):
        px = lay["x"] + lay["dw"] * i + (lay["cw"] if i >= 2 else 0)
        x1 = (px - c.ox) / c.s + 10
        x2 = (px + lay["dw"] - c.ox) / c.s - 10
        arrow(c, x1, up_y, x2, up_y + ARROW_H, True)
        arrow(c, x1, dn_y, x2, dn_y + ARROW_H, False)
        buttons += [(f"up{i}", (x1, up_y, x2, up_y + ARROW_H)), (f"dn{i}", (x1, dn_y, x2, dn_y + ARROW_H))]

    bw, gap = (928 - 2 * 28) / 3, 28
    y_ctl = dn_y + ARROW_H + 90
    reset = (72, y_ctl, 72 + bw, y_ctl + 200)
    start = (72 + bw + gap, y_ctl, 1000, y_ctl + 200)
    button(c, *reset, "リセット", 52)
    buttons += [("reset", reset), ("start", start)]
    c.text(536, 1400, "時計の画面で3回タップ、または電源ボタンでサイネージ終了", "regular", 22, GRAY, anchor="ms")
    bg = c.finish()
    bg.paste(sprites[":"], (lay["x"] + lay["dw"] * 2, lay["y"]))

    sb = px_box(c, *start)
    states = {}
    for name, label in (("start", "スタート"), ("pause", "一時停止"), ("resume", "再開")):
        cc = Canvas(W, H, fonts)
        button(cc, *start, label, 60, primary=True)
        states[name] = cc.finish().crop(sb)
    bg.paste(states["start"], sb[:2])

    cd = Canvas(W, H, fonts)
    cd.d.rectangle([0, 0, W * SS, H * SS], fill=BLACK)
    cd.text(536, 640, "時間です", "bold", 150, WHITE, anchor="ms")
    cd.text(536, 780, "画面をタップして止める", "medium", 40, WHITE, anchor="ms")
    done = cd.finish()

    dw, dh = sprites["0"].size
    t = max(4, int(DIGIT_SIZE * c.s * .03))
    dash = Image.new("L", (dw, dh), WHITE)
    ImageDraw.Draw(dash).rectangle([dw * .25, dh / 2 - t / 2, dw * .75, dh / 2 + t / 2], fill=BLACK)

    lines = [f"digits {lay['x']} {lay['y']} {lay['dw']} {lay['cw']} {lay['h']}",
             f"sprite start {sb[0]} {sb[1]}"]
    lines += ["btn %s %d %d %d %d" % ((name,) + px_box(c, *box)) for name, box in buttons]
    images = {"bg": bg, "done": done, **states, "dm": dash, **{f"d{ch}": sp for ch, sp in sprites.items() if ch != ":"}}
    return images, lines


def main():
    ap = argparse.ArgumentParser(description="タイマー画面の画像を描く")
    ap.add_argument("--device", help="pw3 / voyage / kt2 / pw2（config.json より優先）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    images, lines = render_timer(a.device or load_config().get("device", "pw3"))
    os.makedirs(a.out, exist_ok=True)
    for name, im in sorted(images.items()):
        im.save(os.path.join(a.out, name + ".png"), "PNG", optimize=True)
    with open(os.path.join(a.out, "layout.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"描画しました → {os.path.normpath(a.out)}")


if __name__ == "__main__":
    main()
