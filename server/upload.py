"""
スマホ・PCのブラウザから Kindle に画像を送るページ（serve.py の /upload）

  ブラウザ → このPC（変換）→ SSH → Kindle
"""
import html
import io
import os
from email.parser import BytesParser
from email.policy import default as email_default

import kindlectl
import render

MAX_BODY = 120 * 1024 * 1024  # 一度に送れる合計サイズ

PAGE = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kindle に送る</title>
<style>
  :root {{ --paper:#f3f0e8; --card:#fbfaf6; --ink:#1c1b19; --sub:#6b6860; --line:#d9d4c7; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font:16px/1.6 -apple-system,"Hiragino Sans","Yu Gothic UI","Noto Sans JP",sans-serif; }}
  main {{ max-width:520px; margin:0 auto; padding:28px 16px 48px; }}
  h1 {{ font-size:26px; letter-spacing:.02em; margin:0 0 4px; }}
  .status {{ color:var(--sub); font-size:14px; margin-bottom:22px; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:1px; background:{dot}; }}
  form {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px; }}
  .drop {{ display:block; border:2px dashed var(--line); border-radius:12px; padding:28px 12px; text-align:center;
           cursor:pointer; transition:border-color .15s; }}
  .drop:hover {{ border-color:var(--ink); }}
  .drop b {{ display:block; font-size:18px; }}
  .drop span {{ color:var(--sub); font-size:14px; }}
  input[type=file] {{ display:none; }}
  fieldset {{ border:0; padding:0; margin:20px 0 0; }}
  legend {{ font-size:13px; color:var(--sub); margin-bottom:8px; letter-spacing:.04em; }}
  .seg {{ display:flex; border:1px solid var(--ink); border-radius:10px; overflow:hidden; }}
  .seg label {{ flex:1; text-align:center; padding:10px 4px; cursor:pointer; font-size:15px; }}
  .seg label + label {{ border-left:1px solid var(--ink); }}
  .seg input {{ display:none; }}
  .seg input:checked + span {{ font-weight:700; }}
  .seg label:has(input:checked) {{ background:var(--ink); color:var(--card); }}
  .chk {{ display:flex; gap:10px; align-items:center; padding:8px 0; font-size:15px; }}
  .chk input {{ width:20px; height:20px; accent-color:var(--ink); }}
  .pin {{ width:100%; padding:10px 12px; font-size:16px; border:1px solid var(--line); border-radius:10px; background:#fff; }}
  button {{ width:100%; margin-top:22px; padding:15px; font-size:17px; font-weight:700; color:var(--card);
            background:var(--ink); border:0; border-radius:12px; cursor:pointer; }}
  button:disabled {{ opacity:.5; }}
  .msg {{ padding:14px 16px; border-radius:12px; margin-bottom:18px; background:var(--card); border:1px solid var(--line); }}
  .msg.err {{ border-color:#1c1b19; }}
  .msg ul {{ margin:6px 0 0; padding-left:20px; font-size:14px; color:var(--sub); word-break:break-all; }}
  h2 {{ font-size:14px; color:var(--sub); font-weight:600; margin:28px 0 8px; letter-spacing:.04em; }}
  .list {{ font-size:13px; color:var(--sub); word-break:break-all; margin:0; padding-left:18px; }}
</style></head>
<body><main>
  <h1>Kindle に送る</h1>
  <div class="status"><span class="dot"></span>{status}</div>
  {message}
  <form method="post" action="/upload" enctype="multipart/form-data" onsubmit="go.disabled=true;go.textContent='送信中…（変換に少しかかります）'">
    <label class="drop" for="files"><b id="cnt">写真を選ぶ</b><span>複数選べます・JPEG / PNG / HEIC など</span></label>
    <input id="files" type="file" name="files" accept="image/*" multiple required
           onchange="cnt.textContent=this.files.length+' 枚を選択中'">
    <fieldset><legend>送り先</legend>
      <div class="seg">
        <label><input type="radio" name="to" value="screensaver" checked><span>スクリーンセーバー</span></label>
        <label><input type="radio" name="to" value="slides"><span>スライドショー</span></label>
      </div></fieldset>
    <fieldset><legend>収め方</legend>
      <div class="seg">
        <label><input type="radio" name="fit" value="cover" checked><span>画面いっぱい</span></label>
        <label><input type="radio" name="fit" value="contain"><span>全体を表示</span></label>
      </div></fieldset>
    <fieldset><legend>オプション</legend>
      <label class="chk"><input type="checkbox" name="rotate" value="auto">横長の写真は回転して大きく</label>
      <label class="chk"><input type="checkbox" name="nodither" value="1">イラスト・文字（点描を使わない）</label>
      <label class="chk"><input type="checkbox" name="replace" value="1">今ある画像を消して入れ替える</label>
      <label class="chk"><input type="checkbox" name="show" value="1">送ったらすぐ Kindle に表示</label>
    </fieldset>
    {pin_field}
    <button id="go">Kindle に送る</button>
  </form>
  <h2>KINDLE にある画像</h2>
  {listing}
</main></body></html>"""


def _state_and_list(k):
    try:
        st = kindlectl.get_state(k)
        status = f"接続中・サイネージ {'動作中' if st['signage'] == 'running' else '停止'}・スクリーンセーバー {st['screensaver'].upper()}"
        parts = []
        for t, label in (("screensaver", "スクリーンセーバー"), ("slides", "スライドショー")):
            names = [n for n in kindlectl.remote(k, f"cd {kindlectl.DATA}/{t} 2>/dev/null && ls -1 *.png *.PNG 2>/dev/null; true").split() if n]
            items = "".join(f"<li>{html.escape(n)}</li>" for n in names[-8:])
            more = f"<li>…ほか {len(names) - 8} 枚</li>" if len(names) > 8 else ""
            parts.append(f"<p class='list' style='padding:0'><b>{label}</b>（{len(names)} 枚）</p><ul class='list'>{more}{items}</ul>")
        return status, "#2e7d32", "".join(parts)
    except kindlectl.KindleError as e:
        return "Kindle に接続できません（Wi-Fi・SSH を確認）", "#b23", f"<p class='list'>{html.escape(str(e))}</p>"


def page(message=""):
    cfg = render.load_config()
    try:
        _, k = kindlectl.load_cfg()
        status, dot, listing = _state_and_list(k)
    except kindlectl.KindleError as e:
        status, dot, listing = html.escape(str(e)), "#b23", ""
    pin_field = ""
    if cfg.get("upload_pin"):
        pin_field = ('<fieldset><legend>PIN</legend><input class="pin" type="password" name="pin" '
                     'inputmode="numeric" autocomplete="off" required></fieldset>')
    return PAGE.format(status=html.escape(status), dot=dot, message=message,
                       pin_field=pin_field, listing=listing).encode("utf-8")


def parse_multipart(ctype, body):
    msg = BytesParser(policy=email_default).parsebytes(
        b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
    fields, files = {}, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename:
            if data:
                files.append((filename, data))
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def handle_post(ctype, body):
    """戻り値: (HTTPステータス, HTMLバイト列)"""
    cfg = render.load_config()
    if not ctype.startswith("multipart/form-data"):
        return 400, page('<div class="msg err">送信の形式が違います</div>')
    fields, files = parse_multipart(ctype, body)
    if cfg.get("upload_pin") and fields.get("pin") != str(cfg["upload_pin"]):
        return 403, page('<div class="msg err">PIN が違います</div>')
    if not files:
        return 400, page('<div class="msg err">写真が選ばれていません</div>')

    import convert_images as ci
    from PIL import Image
    opts = kindlectl.convert_opts(fit=fields.get("fit", "cover"),
                                  rotate="auto" if fields.get("rotate") else "none",
                                  no_dither=bool(fields.get("nodither")))
    size = ci.DEVICES.get(cfg.get("device", "pw3"), ci.DEVICES["pw3"])
    import datetime as dt
    import re
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    images, failed = {}, []
    for i, (fname, data) in enumerate(files, 1):
        try:
            with Image.open(io.BytesIO(data)) as im:
                out = ci.to_eink(im, size, opts)
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(fname)[0]).strip("_")[:30] or "image"
            b = io.BytesIO()
            out.save(b, "PNG", optimize=True)
            images[f"{stamp}_{i:02d}_{stem}.png"] = b.getvalue()
        except Exception as e:
            failed.append(f"{fname}: 画像として読み込めませんでした")

    to = fields.get("to") if fields.get("to") in kindlectl.TARGETS else "screensaver"
    try:
        _, k = kindlectl.load_cfg()
        if images:
            kindlectl.push_images(k, images, to, bool(fields.get("replace")), bool(fields.get("show")))
    except kindlectl.KindleError as e:
        return 502, page(f'<div class="msg err"><b>Kindle に送れませんでした</b><ul><li>{html.escape(str(e))}</li></ul></div>')

    label = "スクリーンセーバー" if to == "screensaver" else "スライドショー"
    items = "".join(f"<li>{html.escape(n)}</li>" for n in images)
    items += "".join(f"<li>✗ {html.escape(f)}</li>" for f in failed)
    return 200, page(f'<div class="msg"><b>{len(images)} 枚を{label}に送りました</b><ul>{items}</ul></div>')
