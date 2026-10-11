#!/usr/bin/env python3
"""
Kindle ダッシュボード描画エンジン

出力（out/ フォルダ）:
  dashboard.png      … 背景（日付・天気・カレンダー）。時計の部分は空白
  clock/0.png〜9.png … 時計の数字スプライト（Kindleが毎分差し替える）
  clock/colon.png    … 時計のコロン
  layout.txt         … 時計を描く位置 "X Y 数字幅 コロン幅 高さ 版"

使い方:
  python render.py            … 1回描画
  python render.py --demo     … ネットに繋がずダミーの天気で描画（デザイン確認用）
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# 機種ごとの画面解像度（縦向き）
DEVICES = {
    "pw3": (1072, 1448),     # Paperwhite 第3世代（第7世代）
    "voyage": (1072, 1448),  # Voyage（第7世代）
    "kt2": (600, 800),       # 無印Kindle（第7世代）
    "pw2": (758, 1024),      # Paperwhite 第2世代
}

BASE_W, BASE_H = 1072, 1448   # デザインの基準サイズ
SS = 2                        # 2倍で描いて縮小 → なめらかな線

# E-inkの16階調にぴったり乗る色だけを使う（17の倍数）
BLACK, INK, GRAY, LIGHT, FAINT, WHITE = 0, 0x44, 0x77, 0xBB, 0xDD, 0xFF

WEEKDAYS = "月火水木金土日"

# ---------------------------------------------------------------- フォント
FONT_CANDIDATES = {
    "light": [
        ("fonts/NotoSansJP-Light.ttf", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Light.ttc", 0),
        ("C:/Windows/Fonts/YuGothL.ttc", 0),
        ("/System/Library/Fonts/ヒラギノ角ゴシック W2.ttc", 0),
    ],
    "regular": [
        ("fonts/NotoSansJP-Regular.ttf", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("C:/Windows/Fonts/YuGothR.ttc", 0),
        ("C:/Windows/Fonts/meiryo.ttc", 0),
        ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", 0),
    ],
    "medium": [
        ("fonts/NotoSansJP-Medium.ttf", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc", 0),
        ("C:/Windows/Fonts/YuGothM.ttc", 0),
        ("/System/Library/Fonts/ヒラギノ角ゴシック W5.ttc", 0),
    ],
    "bold": [
        ("fonts/NotoSansJP-Bold.ttf", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("C:/Windows/Fonts/YuGothB.ttc", 0),
        ("C:/Windows/Fonts/meiryob.ttc", 0),
        ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", 0),
    ],
}
VARIABLE_FONT = ["fonts/NotoSansJP-VariableFont_wght.ttf", "fonts/NotoSansJP[wght].ttf"]
WEIGHT_VALUE = {"light": 300, "regular": 400, "medium": 500, "bold": 700}


def _resolve(path):
    return path if os.path.isabs(path) or ":" in path else os.path.join(HERE, path)


def find_font(weight):
    for path, idx in FONT_CANDIDATES[weight]:
        p = _resolve(path)
        if os.path.exists(p):
            return p, idx, None
    for path in VARIABLE_FONT:
        p = _resolve(path)
        if os.path.exists(p):
            return p, 0, WEIGHT_VALUE[weight]
    # 最後の手段: 太さ違いが無くても regular / bold で代用
    fallback = {"light": "regular", "medium": "bold"}.get(weight)
    if fallback:
        return find_font(fallback)
    sys.exit(f"日本語フォントが見つかりません。server/fonts に Noto Sans JP を入れてください（README参照）")


class Fonts:
    def __init__(self):
        self._paths = {w: find_font(w) for w in FONT_CANDIDATES}
        self._cache = {}

    def get(self, weight, px):
        key = (weight, int(px))
        if key not in self._cache:
            path, idx, var = self._paths[weight]
            f = ImageFont.truetype(path, int(px), index=idx)
            if var:
                f.set_variation_by_axes([var])
            self._cache[key] = f
        return self._cache[key]


# ---------------------------------------------------------------- 天気データ
# WMO天気コード → (表示名, アイコン種別)
WMO = {
    0: ("快晴", "clear"), 1: ("晴れ", "clear"), 2: ("晴れ時々くもり", "partly"), 3: ("くもり", "cloudy"),
    45: ("霧", "fog"), 48: ("霧", "fog"),
    51: ("霧雨", "rain"), 53: ("霧雨", "rain"), 55: ("霧雨", "rain"), 56: ("霧雨", "rain"), 57: ("霧雨", "rain"),
    61: ("小雨", "rain"), 63: ("雨", "rain"), 65: ("大雨", "rain"), 66: ("冷たい雨", "rain"), 67: ("冷たい雨", "rain"),
    71: ("小雪", "snow"), 73: ("雪", "snow"), 75: ("大雪", "snow"), 77: ("雪", "snow"),
    80: ("にわか雨", "rain"), 81: ("にわか雨", "rain"), 82: ("激しいにわか雨", "rain"),
    85: ("にわか雪", "snow"), 86: ("にわか雪", "snow"),
    95: ("雷雨", "thunder"), 96: ("雷雨", "thunder"), 99: ("雷雨", "thunder"),
}


def wmo(code):
    return WMO.get(int(code), ("—", "cloudy"))


def fetch_weather(cfg, tries=3):
    """Open-Meteo から天気を取得。一時的なエラー（503 など）は少し待って取り直す"""
    params = {
        "latitude": cfg["latitude"], "longitude": cfg["longitude"], "timezone": cfg["timezone"],
        "forecast_days": 7, "wind_speed_unit": "ms",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "kindle-dashboard/1.0"})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or n == tries - 1:   # 4xx（設定の誤り）はやり直しても無駄
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if n == tries - 1:
                raise
        time.sleep((5, 15, 30)[n])


def demo_weather(now):
    """ネットが無いときのデザイン確認用ダミーデータ"""
    rnd = random.Random(now.date().toordinal())
    start = now.replace(minute=0, second=0, microsecond=0)
    codes = [1, 2, 3, 61, 2, 0, 80, 3, 1, 2]
    hourly = {"time": [], "temperature_2m": [], "weather_code": [], "precipitation_probability": [], "is_day": []}
    for i in range(-2, 7 * 24):
        t = start + dt.timedelta(hours=i)
        hourly["time"].append(t.strftime("%Y-%m-%dT%H:%M"))
        hourly["temperature_2m"].append(round(17 + 4 * math.sin((t.hour - 9) / 24 * 2 * math.pi) + rnd.uniform(-.5, .5), 1))
        hourly["weather_code"].append(codes[(i // 5) % len(codes)])
        hourly["precipitation_probability"].append(rnd.choice([0, 10, 10, 20, 30, 50, 70]))
        hourly["is_day"].append(1 if 6 <= t.hour < 18 else 0)
    daily = {"time": [], "weather_code": [], "temperature_2m_max": [], "temperature_2m_min": [],
             "precipitation_probability_max": [], "sunrise": [], "sunset": []}
    for d in range(7):
        day = now.date() + dt.timedelta(days=d)
        daily["time"].append(day.isoformat())
        daily["weather_code"].append([2, 1, 61, 3, 0, 80, 2][d])
        daily["temperature_2m_max"].append(22 - d % 3)
        daily["temperature_2m_min"].append(14 + d % 2)
        daily["precipitation_probability_max"].append([20, 10, 80, 40, 0, 60, 20][d])
        daily["sunrise"].append(f"{day}T05:46")
        daily["sunset"].append(f"{day}T17:12")
    return {
        "current": {"temperature_2m": 17.4, "apparent_temperature": 16.8, "relative_humidity_2m": 64,
                    "weather_code": 2, "is_day": 1 if 6 <= now.hour < 18 else 0, "wind_speed_10m": 2.6},
        "hourly": hourly, "daily": daily,
    }


def _reason(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code >= 500:
            return f"HTTP {e.code}: 天気サービス側の一時的な障害"
        return f"HTTP {e.code}: 地点などの設定を確認してください"
    return f"{e}（ネット接続を確認してください）"


def _shift_to_now(w, now):
    """前回取得したデータを「今」に合わせる（日付が変わっていたら今日から始まるように）"""
    d = w["daily"]
    today = now.date().isoformat()
    k = next((i for i, t in enumerate(d["time"]) if t >= today), len(d["time"]))
    for key in d:
        d[key] = d[key][k:]
    if not d["time"]:
        return None
    # 「現在の天気」は時間ごとの予報から今の時刻のものを使う
    h = w["hourly"]
    cur = now.strftime("%Y-%m-%dT%H:00")
    if cur in h["time"]:
        i = h["time"].index(cur)
        w["current"].update(temperature_2m=h["temperature_2m"][i],
                            weather_code=h["weather_code"][i], is_day=h["is_day"][i])
    return w


def update_weather(cfg, out_dir, now=None):
    """天気を取得して保存する（serve.py が毎時 7分・37分 などの空いている時間に呼ぶ）。
    成功したら None、失敗したら理由の文字列を返す"""
    tz = ZoneInfo(cfg["timezone"])
    now = now or dt.datetime.now(tz)
    cache = os.path.join(out_dir, "weather_cache.json")
    try:
        w = fetch_weather(cfg)
    except Exception as e:
        return _reason(e)
    w["_fetched_at"] = now.isoformat()
    with open(cache + ".tmp", "w", encoding="utf-8") as f:
        json.dump(w, f)
    os.replace(cache + ".tmp", cache)
    return None


def _cache_age(cache, now):
    try:
        with open(cache, encoding="utf-8") as f:
            got = json.load(f).get("_fetched_at")
        return now - dt.datetime.fromisoformat(got)
    except Exception:
        return None


def load_weather(cfg, now, demo, out_dir, fetch=True):
    """戻り値: (天気データ or None, 注記 or None)
    保存済みの天気が新しければそれを使い、天気サービスには取りに行かない"""
    cache = os.path.join(out_dir, "weather_cache.json")
    if demo:
        return demo_weather(now), "DEMO"
    fresh = dt.timedelta(minutes=int(cfg.get("weather_minutes", 30)) + 15)
    age = _cache_age(cache, now)
    if age is not None and age < fresh:
        with open(cache, encoding="utf-8") as f:
            w = _shift_to_now(json.load(f), now)
        if w:
            return w, None
    # 毎時ちょうど前後（58分〜2分）は天気サービスが混むので取りに行かない
    if now.minute >= 58 or now.minute <= 2:
        fetch = False
    try:
        if not fetch:
            raise RuntimeError("毎時ちょうど前後は取得を見送り")
        err = update_weather(cfg, out_dir, now)
        if err:
            raise RuntimeError(err)
        with open(cache, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        reason = str(e)
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                w = json.load(f)
            got = dt.datetime.fromisoformat(w.get("_fetched_at")) if w.get("_fetched_at") else \
                dt.datetime.fromtimestamp(os.path.getmtime(cache), now.tzinfo)
            if now - got < dt.timedelta(hours=36):
                w = _shift_to_now(w, now)
                if w:
                    print(f"[warn] 天気: {reason} → {got:%m/%d %H:%M} 取得のデータで描画します",
                          file=sys.stderr)
                    return w, f"天気は {got:%H:%M} 時点" if got.date() == now.date() else f"天気は {got:%m/%d %H:%M} 時点"
        except Exception:
            pass
    print(f"[warn] 天気の取得に失敗（{reason}）。使える前回データもありません", file=sys.stderr)
    return None, None


# ---------------------------------------------------------------- 描画の土台
class Canvas:
    """基準座標（1072×1448）で書くと、機種の解像度に合わせて拡大縮小して描く"""

    def __init__(self, w, h, fonts):
        self.w, self.h = w, h
        self.s = min(w / BASE_W, h / BASE_H)
        self.ox = (w - BASE_W * self.s) / 2
        self.oy = (h - BASE_H * self.s) / 2
        self.k = self.s * SS
        self.img = Image.new("L", (w * SS, h * SS), WHITE)
        self.d = ImageDraw.Draw(self.img)
        self.fonts = fonts

    def X(self, x):
        return (self.ox * SS) + x * self.k

    def Y(self, y):
        return (self.oy * SS) + y * self.k

    def P(self, v):
        return v * self.k

    def font(self, weight, size):
        return self.fonts.get(weight, size * self.k)

    def text(self, x, y, s, weight="regular", size=28, fill=BLACK, anchor="ls"):
        self.d.text((self.X(x), self.Y(y)), s, font=self.font(weight, size), fill=fill, anchor=anchor)

    def width(self, s, weight, size):
        return self.font(weight, size).getlength(s) / self.k

    def rule(self, x1, y, x2, w=2, fill=BLACK):
        self.d.rectangle([self.X(x1), self.Y(y) - self.P(w) / 2, self.X(x2), self.Y(y) + self.P(w) / 2], fill=fill)

    def circle(self, cx, cy, r, fill=None, outline=None, width=0):
        self.d.ellipse([self.X(cx - r), self.Y(cy - r), self.X(cx + r), self.Y(cy + r)],
                       fill=fill, outline=outline, width=int(self.P(width)) if width else 0)

    def line(self, pts, w, fill=BLACK):
        """端が丸い線"""
        xy = [(self.X(x), self.Y(y)) for x, y in pts]
        self.d.line(xy, fill=fill, width=max(1, int(self.P(w))), joint="curve")
        r = self.P(w) / 2
        for x, y in (xy[0], xy[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def finish(self):
        img = self.img.resize((self.w, self.h), Image.LANCZOS)
        return quantize16(img)


def quantize16(img):
    return img.point(lambda v: min(255, int(round(v / 17.0)) * 17))


# ---------------------------------------------------------------- 天気アイコン
def _cloud_parts(cx, cy, s):
    circles = [(cx - .17 * s, cy + .06 * s, .17 * s), (cx + .02 * s, cy - .05 * s, .24 * s), (cx + .23 * s, cy + .07 * s, .16 * s)]
    rect = (cx - .17 * s, cy + .06 * s, cx + .23 * s, cy + .23 * s)
    return circles, rect


def draw_cloud(c, cx, cy, s, fill=WHITE, stroke=BLACK, w=None):
    """円3つ＋長方形で雲を作り、黒で一回り大きく塗ってから中を塗る（＝輪郭線）"""
    w = w or s * .055
    circles, (x1, y1, x2, y2) = _cloud_parts(cx, cy, s)
    for grow, color in ((w, stroke), (0, fill)):
        for (x, y, r) in circles:
            c.circle(x, y, r + grow, fill=color)
        c.d.rectangle([c.X(x1), c.Y(y1), c.X(x2), c.Y(y2 + grow)], fill=color)


def draw_sun(c, cx, cy, s, w=None):
    w = w or s * .055
    r = s * .19
    c.circle(cx, cy, r, outline=BLACK, width=w)
    for i in range(8):
        a = i * math.pi / 4
        c.line([(cx + math.cos(a) * s * .29, cy + math.sin(a) * s * .29),
                (cx + math.cos(a) * s * .40, cy + math.sin(a) * s * .40)], w)


def draw_moon(c, cx, cy, s):
    r = s * .26
    c.circle(cx, cy, r, fill=BLACK)
    c.circle(cx + r * .55, cy - r * .35, r * .88, fill=WHITE)


def draw_icon(c, kind, cx, cy, size, night=False):
    s = size
    w = max(1.6, s * .055)
    if kind == "clear":
        (draw_moon if night else draw_sun)(c, cx, cy, s)
    elif kind == "partly":
        if night:
            draw_moon(c, cx - s * .14, cy - s * .16, s * .78)
        else:
            draw_sun(c, cx - s * .14, cy - s * .16, s * .78, w=w)
        draw_cloud(c, cx + s * .06, cy + s * .08, s * .78, w=w)
    elif kind == "cloudy":
        draw_cloud(c, cx - s * .03, cy - s * .04, s * .95, fill=LIGHT, w=w)
    elif kind in ("rain", "snow", "thunder"):
        draw_cloud(c, cx - s * .03, cy - s * .12, s * .9, fill=LIGHT if kind == "thunder" else WHITE, w=w)
        base = cy + s * .22
        if kind == "rain":
            for dx in (-.2, 0, .2):
                x = cx + dx * s
                c.line([(x + s * .04, base), (x - s * .04, base + s * .17)], w)
        elif kind == "snow":
            for dx, dy in ((-.2, .05), (0, .13), (.2, .05)):
                c.circle(cx + dx * s, base + dy * s, s * .045, fill=BLACK)
        else:
            pts = [(.04, -.01), (-.08, .17), (.0, .17), (-.05, .32), (.12, .11), (.03, .11), (.1, -.01)]
            c.d.polygon([(c.X(cx + x * s), c.Y(base + y * s - s * .02)) for x, y in pts], fill=BLACK)
    elif kind == "fog":
        for i, (a, b) in enumerate(((-.32, .22), (-.22, .32), (-.32, .12))):
            y = cy - s * .14 + i * s * .14
            c.line([(cx + a * s, y), (cx + b * s, y)], w)


# ---------------------------------------------------------------- 時計スプライト
def ceil8(v):
    return int(math.ceil(v / 8.0) * 8)


def floor8(v):
    return int(v // 8 * 8)


def build_clock(c, fonts, base_x, base_top, size):
    """時計の数字を1文字ずつ画像化。Kindle側はこれを並べるだけで時刻を描ける"""
    W = c.w
    fpx = size * c.s                  # 実ピクセルのフォントサイズ
    font = fonts.get("light", fpx * SS)
    adv = max(font.getlength(d) for d in "0123456789") / SS
    _, top, _, bottom = font.getbbox("0123456789", anchor="ls")
    cap = -top / SS                   # 数字の高さ
    pad = max(4, int(fpx * .04))
    dw = ceil8(adv + pad)             # 数字1文字ぶんの幅（8の倍数）
    cw = ceil8(fpx * .26)             # コロンの幅
    h = int(math.ceil(cap + pad * 2))
    baseline = pad + cap

    total = dw * 4 + cw
    x0 = floor8(c.ox + base_x * c.s)
    x0 = max(0, min(x0, floor8(W - total)))
    y0 = int(c.oy + base_top * c.s)

    sprites = {}
    for ch in "0123456789":
        im = Image.new("L", (dw * SS, h * SS), WHITE)
        ImageDraw.Draw(im).text((dw * SS / 2, baseline * SS), ch, font=font, fill=BLACK, anchor="ms")
        sprites[ch] = quantize16(im.resize((dw, h), Image.LANCZOS))
    im = Image.new("L", (cw * SS, h * SS), WHITE)
    dd = ImageDraw.Draw(im)
    r = fpx * .045 * SS
    for frac in (.30, .72):           # コロンは数字の高さに合わせて自前で描く
        cy = (baseline - cap * (1 - frac)) * SS
        dd.ellipse([cw * SS / 2 - r, cy - r, cw * SS / 2 + r, cy + r], fill=BLACK)
    sprites[":"] = quantize16(im.resize((cw, h), Image.LANCZOS))

    layout = {"x": x0, "y": y0, "dw": dw, "cw": cw, "h": h}
    return sprites, layout, (h / c.s)


def paste_time(img, sprites, layout, hhmm):
    x = layout["x"]
    for ch in hhmm:
        sp = sprites[ch]
        img.paste(sp, (x, layout["y"]))
        x += sp.width


# ---------------------------------------------------------------- 各ブロック
def parse_time(s, tz):
    return dt.datetime.fromisoformat(s).replace(tzinfo=tz)


def holiday_name(day):
    try:
        import jpholiday
        return jpholiday.is_holiday_name(day)
    except Exception:
        return None


def draw_header(c, cfg, now, note):
    c.text(72, 96, cfg.get("location_name", ""), "bold", 26, INK)
    right = f"更新 {now:%H:%M}" + (f"・{note}" if note else "")
    c.text(1000, 96, right, "regular", 24, GRAY, anchor="rs")


def draw_date(c, now, y):
    md = f"{now.month}月{now.day}日"
    c.text(72, y, md, "bold", 76, BLACK)
    x = 72 + c.width(md, "bold", 76) + 18
    c.text(x, y, WEEKDAYS[now.weekday()] + "曜日", "medium", 40, INK)
    hol = holiday_name(now.date())
    if hol:
        tw = c.width(hol, "bold", 24)
        bx = 1000 - tw - 32
        c.d.rounded_rectangle([c.X(bx), c.Y(y - 40), c.X(1000), c.Y(y + 4)], radius=c.P(22), fill=BLACK)
        c.text(bx + 16, y - 9, hol, "bold", 24, WHITE)
    else:
        c.text(1000, y, f"{now.year}", "regular", 32, GRAY, anchor="rs")


def draw_current(c, w, tz, now, y):
    cur = w["current"]
    label, kind = wmo(cur["weather_code"])
    draw_icon(c, kind, 72 + 92, y + 96, 190, night=not cur.get("is_day", 1))
    t = f"{round(cur['temperature_2m'])}"
    c.text(292, y + 168, t, "light", 168, BLACK)
    tx = 292 + c.width(t, "light", 168)
    c.text(tx + 4, y + 70, "°C", "regular", 40, INK)

    d = w["daily"]
    rx = 640
    c.text(rx, y + 54, label, "bold", 40, BLACK)
    c.text(rx, y + 104, f"最高 {round(d['temperature_2m_max'][0])}°", "medium", 30, BLACK)
    c.text(rx + 150, y + 104, f"最低 {round(d['temperature_2m_min'][0])}°", "medium", 30, INK)
    pop = d["precipitation_probability_max"][0]
    stats = [("降水", f"{pop if pop is not None else '–'}%"), ("湿度", f"{round(cur['relative_humidity_2m'])}%"),
             ("風", f"{cur['wind_speed_10m']:.0f}m/s")]
    x = rx
    for k, v in stats:
        c.text(x, y + 150, k, "regular", 22, GRAY)
        c.text(x + c.width(k, "regular", 22) + 6, y + 150, v, "medium", 26, BLACK)
        x += 120
    sr = parse_time(d["sunrise"][0], tz)
    ss_ = parse_time(d["sunset"][0], tz)
    c.text(rx, y + 190, f"日の出 {sr:%H:%M}   日の入 {ss_:%H:%M}   体感 {round(cur['apparent_temperature'])}°",
           "regular", 22, GRAY)


def draw_hourly(c, w, tz, now, y, note=None):
    h = w["hourly"]
    times = [parse_time(t, tz) for t in h["time"]]
    start = next((i for i, t in enumerate(times) if t > now), 0)
    idx = [start + 3 * k for k in range(8) if start + 3 * k < len(times)]
    n = len(idx)
    if not n:
        return
    col = (1000 - 72) / n
    temps = [h["temperature_2m"][i] for i in idx]
    lo, hi = min(temps), max(temps)
    span = max(hi - lo, 4)

    c.text(72, y, "3時間ごと" + (f"　（{note}）" if note and note != "DEMO" else ""), "medium", 22, GRAY)
    # 気温の折れ線（上が暖かい）
    gy0, gy1 = y + 172, y + 204
    pts = []
    for k, i in enumerate(idx):
        cx = 72 + col * (k + .5)
        pts.append((cx, gy1 - (h["temperature_2m"][i] - lo) / span * (gy1 - gy0)))
    c.d.line([(c.X(x), c.Y(yy)) for x, yy in pts], fill=LIGHT, width=int(c.P(3)), joint="curve")

    for k, i in enumerate(idx):
        cx = 72 + col * (k + .5)
        t = times[i]
        lab = "明日" if (t.date() != now.date() and t.hour == 0) else f"{t.hour}時"
        c.text(cx, y + 40, lab, "regular", 24, INK, anchor="ms")
        draw_icon(c, wmo(h["weather_code"][i])[1], cx, y + 88, 60, night=not h["is_day"][i])
        px, py = pts[k]
        c.circle(px, py, 6, fill=BLACK)
        c.circle(px, py, 3, fill=WHITE)
        c.text(cx, py - 14, f"{round(h['temperature_2m'][i])}°", "bold", 28, BLACK, anchor="ms")
        pop = h["precipitation_probability"][i] or 0
        # 降水確率のバー
        bw = 44
        c.d.rounded_rectangle([c.X(cx - bw / 2), c.Y(y + 226), c.X(cx + bw / 2), c.Y(y + 233)], radius=c.P(4), fill=FAINT)
        if pop:
            c.d.rounded_rectangle([c.X(cx - bw / 2), c.Y(y + 226), c.X(cx - bw / 2 + bw * pop / 100), c.Y(y + 233)],
                                  radius=c.P(4), fill=BLACK)
        c.text(cx, y + 258, f"{pop}%", "regular", 20, GRAY, anchor="ms")


def draw_calendar(c, now, x, y, wdt):
    import calendar
    c.text(x, y, f"{now.year}年 {now.month}月", "bold", 30, BLACK)
    cell = wdt / 7
    heads = "日月火水木金土"
    for k, ch in enumerate(heads):
        c.text(x + cell * (k + .5), y + 52, ch, "medium", 20, GRAY if k in (0, 6) else INK, anchor="ms")
    cal = calendar.Calendar(firstweekday=6).monthdatescalendar(now.year, now.month)
    rowh = 44
    for r, week in enumerate(cal):
        for k, day in enumerate(week):
            if day.month != now.month:
                continue
            cx = x + cell * (k + .5)
            cy = y + 96 + r * rowh
            hol = holiday_name(day)
            if day == now.date():
                c.circle(cx, cy - 9, 21, fill=BLACK)
                c.text(cx, cy, str(day.day), "bold", 24, WHITE, anchor="ms")
            else:
                off = k in (0, 6) or hol
                c.text(cx, cy, str(day.day), "medium" if hol else "regular", 24, GRAY if off else BLACK, anchor="ms")
                if hol:
                    c.rule(cx - 8, cy + 9, cx + 8, 2, GRAY)


def draw_weekly(c, w, tz, x, y, wdt):
    d = w["daily"]
    c.text(x, y, "週間予報", "bold", 30, BLACK)
    rowh = 56
    for r, k in enumerate(range(1, min(6, len(d["time"])))):
        day = dt.date.fromisoformat(d["time"][k])
        yy = y + 62 + r * rowh
        if r:
            c.rule(x, yy - rowh / 2 - 6, x + wdt, 1, FAINT)
        wk = WEEKDAYS[day.weekday()]
        hol = holiday_name(day) or day.weekday() >= 5
        c.text(x, yy + 8, wk, "bold", 26, GRAY if hol else BLACK)
        c.text(x + 36, yy + 8, f"{day.month}/{day.day}", "regular", 22, GRAY)
        draw_icon(c, wmo(d["weather_code"][k])[1], x + 168, yy - 2, 44)
        c.text(x + 290, yy + 8, f"{round(d['temperature_2m_max'][k])}°", "bold", 26, BLACK, anchor="rs")
        c.text(x + 352, yy + 8, f"{round(d['temperature_2m_min'][k])}°", "regular", 26, GRAY, anchor="rs")
        pop = d["precipitation_probability_max"][k]
        c.text(x + wdt, yy + 8, f"{pop if pop is not None else '–'}%", "regular", 22, INK, anchor="rs")


# ---------------------------------------------------------------- 全体
CLOCK_TOP, CLOCK_SIZE = 100, 270


def render(cfg, out_dir, demo=False, now=None):
    tz = ZoneInfo(cfg["timezone"])
    now = now or dt.datetime.now(tz)
    W, H = DEVICES.get(cfg.get("device", "pw3"), DEVICES["pw3"])
    fonts = Fonts()
    c = Canvas(W, H, fonts)
    weather, note = load_weather(cfg, now, demo, out_dir)

    sprites, layout, clock_h = build_clock(c, fonts, 72, CLOCK_TOP, CLOCK_SIZE)

    # 地名・更新時刻の見出しは表示しない（draw_header は残してあるので戻すときはここで呼ぶ）
    # 縦の配置（基準座標）: 時計 → 日付 → 現在の天気 → 3時間ごと → カレンダー｜週間
    y_date = CLOCK_TOP + clock_h + 96
    draw_date(c, now, y_date)
    c.rule(72, y_date + 38, 1000, 3)
    if weather:
        draw_current(c, weather, tz, now, y_date + 62)
        draw_hourly(c, weather, tz, now, y_date + 316, note)
    else:
        c.text(536, y_date + 260, "天気情報を取得できませんでした", "medium", 32, GRAY, anchor="ms")
    y_bot = y_date + 604
    c.rule(72, y_bot, 1000, 3)
    draw_calendar(c, now, 72, y_bot + 54, 420)
    if weather:
        draw_weekly(c, weather, tz, 560, y_bot + 54, 440)

    img = c.finish()
    paste_time(img, sprites, layout, f"{now:%H:%M}")
    return img, sprites, layout


def save(out_dir, img, sprites, layout):
    os.makedirs(os.path.join(out_dir, "clock"), exist_ok=True)
    names = {ch: ("colon" if ch == ":" else ch) for ch in sprites}
    h = hashlib.sha1()
    for ch, sp in sorted(sprites.items()):
        p = os.path.join(out_dir, "clock", names[ch] + ".png")
        sp.save(p + ".tmp", "PNG", optimize=True)
        os.replace(p + ".tmp", p)
        h.update(sp.tobytes())
    ver = h.hexdigest()[:8]
    with open(os.path.join(out_dir, "layout.txt.tmp"), "w") as f:
        f.write(f"{layout['x']} {layout['y']} {layout['dw']} {layout['cw']} {layout['h']} {ver}\n")
    os.replace(os.path.join(out_dir, "layout.txt.tmp"), os.path.join(out_dir, "layout.txt"))
    p = os.path.join(out_dir, "dashboard.png")
    img.save(p + ".tmp", "PNG", optimize=True)
    os.replace(p + ".tmp", p)


def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Kindle ダッシュボードを描画")
    ap.add_argument("--demo", action="store_true", help="ダミーの天気で描画（ネット不要）")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--device", help="pw3 / voyage / kt2 / pw2（config.json より優先）")
    ap.add_argument("--refresh-weather", action="store_true",
                    help="保存済みの天気が weather_minutes より古ければ取り直す（GitHub Actions 用）")
    a = ap.parse_args()
    cfg = load_config()
    if a.device:
        cfg["device"] = a.device
    os.makedirs(a.out, exist_ok=True)
    if a.refresh_weather and not a.demo:
        # serve.py の weather_loop の代わり。これが無いと load_weather は 45 分までキャッシュを使い続ける
        now = dt.datetime.now(ZoneInfo(cfg["timezone"]))
        age = _cache_age(os.path.join(a.out, "weather_cache.json"), now)
        stale = age is None or age >= dt.timedelta(minutes=int(cfg.get("weather_minutes", 30)))
        if stale and 2 < now.minute < 58:
            err = update_weather(cfg, a.out, now)
            if err:
                print(f"[warn] 天気: {err}", file=sys.stderr)
    save(a.out, *render(cfg, a.out, a.demo))
    print(f"描画しました → {a.out}")


if __name__ == "__main__":
    main()
