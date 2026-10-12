#!/usr/bin/env python3
"""
タイマー画面の画像を描いて Kindle の拡張フォルダに入れる（サーバー不要で動くよう、画像は拡張に同梱する）
iPhone の時計アプリのタイマーを手本に、設定画面（時間・分・秒のホイール）と動作画面（残りを示すリング）の2つ

出力（../kindle/extensions/signage/img/timer/）:
  set_bg.png / run_bg.png          … 設定画面・動作画面の背景
  wh_00〜23.png / wm_00〜59.png    … ホイール1列ぶん（時間 / 分・秒）
  d0〜9.png, dc.png, dm.png        … 残り時間の数字・コロン・省電力表示の「-」
  e0〜9.png, g0〜9.png             … 終了予定時刻の数字（動作中 / 一時停止中は薄く）
  ring_K_t|b|l|r.png               … 残りのリング（K=0〜RING_STEPS）。数字に重ならない上下左右の4つの帯に分ける
  start/start_off/pause/resume.png … 右の丸ボタン
  done.png                         … 終わったときの画面
  layout.txt                       … 位置とタップの枠（timer.sh が読む）

使い方:
  python render_timer.py               … config.json の機種で描く
  python render_timer.py --device kt2  … 機種を指定（描いたら kindlectl.py update で送る）
"""
import argparse
import glob
import math
import os

from PIL import Image, ImageDraw

from render import (BLACK, DEVICES, FAINT, GRAY, HERE, INK, LIGHT, SS, WHITE, Canvas, Fonts, build_clock,
                    ceil8, floor8, load_config, quantize16)

OUT = os.path.join(HERE, "..", "kindle", "extensions", "signage", "img", "timer")
RING_STEPS = 20

# 基準座標（1072×1448）での配置
CX, CY, RING_R, RING_W = 536, 640, 380, 16
HOLE = (230, 520, 842, 790)            # リングの内側で数字と終了時刻を置く範囲（リングの帯はここを避ける）
BTN_Y, BTN_R, BTN_LX, BTN_RX = 1250, 100, 196, 876
COLS = [("h", 248, "時間", 24), ("m", 536, "分", 60), ("s", 824, "秒", 60)]   # 枠の端が 8 の倍数になる位置
ROW = 130                              # ホイールの1段の高さ
WHEEL = (CY - 320, CY + 300)           # ホイール1列の画像の上下
ARROW_UP, ARROW_DN = (230, 320), (990, 1080)


def px_box(c, x1, y1, x2, y2):
    """基準座標の枠 → 実ピクセル。eips で差し替える部分は x と幅を 8 の倍数にそろえる"""
    return (floor8(c.ox + x1 * c.s), int(c.oy + y1 * c.s),
            min(c.w, ceil8(c.ox + x2 * c.s)), int(math.ceil(c.oy + y2 * c.s)))


def fade(im, k):
    return quantize16(im.point(lambda v: int(255 - (255 - v) * k)))


def header(c):
    c.text(72, 110, "タイマー", "bold", 44, BLACK)
    c.text(1000, 110, "右へスワイプで時計に戻る", "regular", 24, GRAY, anchor="rs")
    c.rule(72, 140, 1000, 3)


def round_button(c, cx, label, fill, fg, ring=None, size=36):
    """iPhone の丸ボタン（外側に細い輪）"""
    has_ring = ring is not None   # BLACK は 0 なので真偽で判定しない
    c.circle(cx, BTN_Y, BTN_R + 10, outline=ring if has_ring else fill, width=3)
    c.circle(cx, BTN_Y, BTN_R, fill=fill, outline=ring, width=5 if has_ring else 0)
    c.text(cx, BTN_Y, label, "bold", size, fg, anchor="mm")


def arrow(c, box, up):
    x1, y1, x2, y2 = box
    cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, 64, 34
    tip, base = (cy - h / 2, cy + h / 2) if up else (cy + h / 2, cy - h / 2)
    c.d.polygon([(c.X(cx), c.Y(tip)), (c.X(cx - w / 2), c.Y(base)), (c.X(cx + w / 2), c.Y(base))], fill=INK)


def bell(c, cx, cy, s):
    c.d.chord([c.X(cx - s * .38), c.Y(cy - s * .5), c.X(cx + s * .38), c.Y(cy + s * .3)], 180, 360, fill=GRAY)
    c.d.rectangle([c.X(cx - s * .38), c.Y(cy - s * .1), c.X(cx + s * .38), c.Y(cy + s * .25)], fill=GRAY)
    c.d.rectangle([c.X(cx - s * .5), c.Y(cy + s * .22), c.X(cx + s * .5), c.Y(cy + s * .32)], fill=GRAY)
    c.circle(cx, cy + s * .42, s * .1, fill=GRAY)


def band(c):
    c.d.rounded_rectangle([c.X(96), c.Y(CY - 62), c.X(976), c.Y(CY + 62)], radius=c.P(22), fill=FAINT)


def ring(c, frac):
    box = [c.X(CX - RING_R), c.Y(CY - RING_R), c.X(CX + RING_R), c.Y(CY + RING_R)]
    w = int(c.P(RING_W))
    c.d.ellipse(box, outline=FAINT, width=w)
    if frac <= 0:
        return
    c.d.arc(box, -90, -90 + 360 * frac, fill=BLACK, width=w)
    for a in (-90, -90 + 360 * frac):   # 端を丸く
        r = math.radians(a)
        c.circle(CX + math.cos(r) * (RING_R - RING_W / 2), CY + math.sin(r) * (RING_R - RING_W / 2),
                 RING_W / 2, fill=BLACK)


def cell_box(col_x):
    return (col_x - 120, WHEEL[0], col_x + 32, WHEEL[1])


def wheel_image(W, H, fonts, col_x, v, n):
    """ホイール1列。上ほど大きい数（▲で増える向き・下へのスワイプで増える向きとそろえる）。帯の背景ごと描いて切り出す"""
    c = Canvas(W, H, fonts)
    band(c)
    for off, size, weight, fill in ((-2, 44, "regular", LIGHT), (-1, 60, "regular", GRAY), (0, 92, "medium", BLACK),
                                     (1, 60, "regular", GRAY), (2, 44, "regular", LIGHT)):
        c.text(col_x - 45, CY + off * ROW, f"{(v - off) % n}", weight, size, fill, anchor="mm")
    return c.finish().crop(px_box(c, *cell_box(col_x)))


def render_timer(device):
    W, H = DEVICES.get(device, DEVICES["pw3"])
    fonts = Fonts()
    images, lines = {}, []

    # ---- 設定画面
    c = Canvas(W, H, fonts)
    header(c)
    band(c)
    for key, col_x, unit, n in COLS:
        c.text(col_x + 40, CY + 12, unit, "medium", 34, BLACK, anchor="ls")
        cb = cell_box(col_x)
        up, dn = (cb[0], ARROW_UP[0], cb[2], ARROW_UP[1]), (cb[0], ARROW_DN[0], cb[2], ARROW_DN[1])
        arrow(c, up, True)
        arrow(c, dn, False)
        bx = px_box(c, *cb)
        lines += ["btn up_%s %d %d %d %d" % ((key,) + px_box(c, *up)),
                  "btn dn_%s %d %d %d %d" % ((key,) + px_box(c, *dn)),
                  "btn whl_%s %d %d %d %d" % ((key,) + bx),
                  f"col {key} {bx[0]} {bx[1]}"]
    round_button(c, BTN_LX, "クリア", FAINT, BLACK)
    c.text(536, 1410, "矢印・数字のタップ・上下のスワイプで変えられます", "regular", 22, GRAY, anchor="ms")
    set_bg = c.finish()
    lines.append("wheel %d %d" % (int(c.oy + CY * c.s), int(ROW * c.s)))
    w_m, w_s = px_box(c, *cell_box(COLS[1][1])), px_box(c, *cell_box(COLS[2][1]))
    assert w_m[2] - w_m[0] == w_s[2] - w_s[0], "分と秒は同じ画像を使うので列の幅をそろえる"
    for v in range(24):
        images[f"wh_{v:02d}"] = wheel_image(W, H, fonts, COLS[0][1], v, 24)
    for v in range(60):
        images[f"wm_{v:02d}"] = wheel_image(W, H, fonts, COLS[1][1], v, 60)

    for side, cx in (("left", BTN_LX), ("right", BTN_RX)):
        lines.append("btn %s %d %d %d %d" % ((side,) + px_box(c, cx - BTN_R - 10, BTN_Y - BTN_R - 10,
                                                                cx + BTN_R + 10, BTN_Y + BTN_R + 10)))
    rb = px_box(c, BTN_RX - BTN_R - 14, BTN_Y - BTN_R - 14, BTN_RX + BTN_R + 14, BTN_Y + BTN_R + 14)
    for name, label, fill, fg, rg, size in (("start", "開始", BLACK, WHITE, None, 36),
                                            ("start_off", "開始", FAINT, LIGHT, None, 36),
                                            ("pause", "一時停止", WHITE, BLACK, BLACK, 32),
                                            ("resume", "再開", BLACK, WHITE, None, 36)):
        cc = Canvas(W, H, fonts)
        round_button(cc, BTN_RX, label, fill, fg, rg, size)
        images[name] = cc.finish().crop(rb)
    lines.append(f"sprite right {rb[0]} {rb[1]}")
    set_bg.paste(images["start_off"], rb[:2])
    images["set_bg"] = set_bg

    # ---- 動作画面
    c = Canvas(W, H, fonts)
    header(c)
    round_button(c, BTN_LX, "キャンセル", FAINT, BLACK, size=30)
    ring(c, 1)
    hx1, hy1, hx2, hy2 = px_box(c, *HOLE)
    size = 230
    while True:   # 「23:59:59」がリングの内側に収まる大きさ
        sprites, lay, _ = build_clock(c, fonts, 0, 0, size)
        if lay["dw"] * 6 + lay["cw"] * 2 <= hx2 - hx1 or size < 80:
            break
        size -= 6
    dh = lay["h"]
    lines.append(f"big {lay['dw']} {lay['cw']} {int(c.oy + CY * c.s - dh * .62)} {int(c.ox + CX * c.s)}")
    for ch, sp in sprites.items():
        images["dc" if ch == ":" else f"d{ch}"] = sp
    t = max(4, int(size * c.s * .03))
    dash = Image.new("L", (lay["dw"], dh), WHITE)
    ImageDraw.Draw(dash).rectangle([lay["dw"] * .25, dh / 2 - t / 2, lay["dw"] * .75, dh / 2 + t / 2], fill=BLACK)
    images["dm"] = dash

    small, slay, _ = build_clock(c, fonts, 0, 0, 44)
    ex = floor8(c.ox + CX * c.s - (slay["dw"] * 4 + slay["cw"]) / 2 + 20 * c.s)
    ey = int(c.oy + 742 * c.s)
    bell(c, (ex - c.ox) / c.s - 28, (ey - c.oy) / c.s + slay["h"] / c.s * .5, 30)
    lines.append(f"end {ex} {ey} {slay['dw']} {slay['cw']}")
    for ch, sp in small.items():
        if ch != ":":
            images[f"e{ch}"] = sp
            images[f"g{ch}"] = fade(sp, .35)
    run_bg = c.finish()
    run_bg.paste(fade(small[":"], .6), (ex + slay["dw"] * 2, ey))
    images["run_bg"] = run_bg

    rx1, ry1, rx2, ry2 = px_box(c, CX - RING_R - 4, CY - RING_R - 4, CX + RING_R + 4, CY + RING_R + 4)
    bands = {"t": (rx1, ry1, rx2, hy1), "b": (rx1, hy2, rx2, ry2), "l": (rx1, hy1, hx1, hy2), "r": (hx2, hy1, rx2, hy2)}
    for k in range(RING_STEPS + 1):
        cc = Canvas(W, H, fonts)
        ring(cc, k / RING_STEPS)
        im = cc.finish()
        for key, box in bands.items():
            images[f"ring_{k}_{key}"] = im.crop(box)
    lines.append(f"ring {RING_STEPS} " + " ".join(f"{k} {b[0]} {b[1]}" for k, b in bands.items()))

    # ---- 終わったとき
    cd = Canvas(W, H, fonts)
    cd.d.rectangle([0, 0, W * SS, H * SS], fill=BLACK)
    cd.text(536, 620, "タイマー", "medium", 56, LIGHT, anchor="ms")
    cd.text(536, 780, "時間です", "bold", 150, WHITE, anchor="ms")
    cd.circle(536, 1000, 110, fill=WHITE)
    cd.text(536, 1000, "停止", "bold", 48, BLACK, anchor="mm")
    cd.text(536, 1190, "画面のどこに触れても止まります", "regular", 32, LIGHT, anchor="ms")
    images["done"] = cd.finish()
    return images, lines


def main():
    ap = argparse.ArgumentParser(description="タイマー画面の画像を描く")
    ap.add_argument("--device", help="pw3 / voyage / kt2 / pw2（config.json より優先）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    images, lines = render_timer(a.device or load_config().get("device", "pw3"))
    os.makedirs(a.out, exist_ok=True)
    for old in glob.glob(os.path.join(a.out, "*.png")):   # 前の版の不要な画像を残さない
        os.remove(old)
    for name, im in sorted(images.items()):
        im.save(os.path.join(a.out, name + ".png"), "PNG", optimize=True)
    with open(os.path.join(a.out, "layout.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"描画しました → {os.path.normpath(a.out)}（{len(images)} 枚）")


if __name__ == "__main__":
    main()
