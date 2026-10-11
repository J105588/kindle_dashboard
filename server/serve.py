#!/usr/bin/env python3
"""
ダッシュボード配信サーバー

  python serve.py           … 描画して http://<このPCのIP>:8080/ で配信。以後 refresh_minutes ごとに再描画
  python serve.py --demo    … ダミー天気で動かす（動作確認用）

Kindle 側の config.sh の SERVER_URL に、起動時に表示されるURLを設定してください。
"""
import argparse
import datetime as dt
import functools
import http.server
import os
import socket
import threading
import time
import traceback

import render


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def render_once(out_dir, demo):
    cfg = render.load_config()
    render.save(out_dir, *render.render(cfg, out_dir, demo))
    print(f"[{dt.datetime.now():%H:%M:%S}] 描画しました")


def weather_loop(out_dir, minutes):
    """天気だけを取りに行く。毎時ちょうどは天気サービスが混むので、7分・37分のように少しずらす"""
    import random
    offset = 7 * 60 + random.randint(0, 50)
    step = minutes * 60
    while True:
        now = time.time()
        wait = (offset - now % step) % step or step
        time.sleep(wait)
        cfg = render.load_config()
        err = render.update_weather(cfg, out_dir)
        if err:
            print(f"[{dt.datetime.now():%H:%M:%S}] [warn] 天気の取得に失敗（{err}）→ 前回のデータを使います")
        else:
            print(f"[{dt.datetime.now():%H:%M:%S}] 天気を更新しました")
            try:
                render_once(out_dir, False)
            except Exception:
                traceback.print_exc()


def scheduler(out_dir, demo, minutes):
    while True:
        # 毎時0分・10分…のように区切りの良い時刻に合わせる（日付の切り替わりもすぐ反映）
        now = time.time()
        step = minutes * 60
        time.sleep(step - (now % step) + 2)
        try:
            render_once(out_dir, demo)
        except Exception:
            traceback.print_exc()


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send_html(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # スマホから画像を送るページ
        if self.path.split("?")[0].rstrip("/") in ("/upload", ""):
            if self.path.split("?")[0] == "/":
                self.send_response(302)
                self.send_header("Location", "/upload")
                self.end_headers()
                return
            import upload
            return self._send_html(200, upload.page())
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/upload":
            return self.send_error(404)
        import upload
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > upload.MAX_BODY:
            return self._send_html(413, upload.page('<div class="msg err">サイズが大きすぎます（一度に送る枚数を減らしてください）</div>'))
        body = self.rfile.read(n)
        try:
            code, html_ = upload.handle_post(self.headers.get("Content-Type", ""), body)
        except Exception as e:
            traceback.print_exc()
            code, html_ = 500, upload.page(f'<div class="msg err">エラー: {e}</div>')
        print(f"[{dt.datetime.now():%H:%M:%S}] アップロード ({code})")
        self._send_html(code, html_)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    seen = set()

    def log_message(self, fmt, *args):
        # 毎回のアクセスは表示しないが、Kindle が初めて来たときだけ IP を表示する
        ip = self.client_address[0]
        if "layout.txt" in self.path and ip not in Handler.seen:
            Handler.seen.add(ip)
            print(f"[{dt.datetime.now():%H:%M:%S}] Kindle からアクセス: {ip}  ← これが Kindle の IP アドレス")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    cfg = render.load_config()
    out_dir = os.path.join(render.HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    render_once(out_dir, a.demo)
    threading.Thread(target=scheduler, args=(out_dir, a.demo, int(cfg.get("refresh_minutes", 10))), daemon=True).start()
    if not a.demo:
        threading.Thread(target=weather_loop, args=(out_dir, int(cfg.get("weather_minutes", 30))), daemon=True).start()

    port = int(cfg.get("port", 8080))
    handler = functools.partial(Handler, directory=out_dir)
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    print("=" * 56)
    print(f"  配信中: http://{lan_ip()}:{port}")
    print("  ↑ このURLを Kindle の config.sh の SERVER_URL に設定")
    print(f"  スマホから画像を送る: http://{lan_ip()}:{port}/upload")
    print("  止めるには Ctrl+C")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
