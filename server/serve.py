#!/usr/bin/env python3
"""
ダッシュボード配信サーバー

  python serve.py           … 描画して http://<このPCのIP>:8080/ で配信。以後 refresh_minutes ごとに再描画
  python serve.py --demo    … ダミー天気で動かす（動作確認用）

  ウィンドウを開いたままにしたくないとき（裏で動かす）:
  python serve.py start          … 裏で起動（PowerShell を閉じても動き続ける）
  python serve.py stop           … 止める
  python serve.py restart        … 再起動（config.json を変えたとき）
  python serve.py status         … 動いているか・URL を表示
  python serve.py log            … ログの最後の部分を表示（out/serve.log）
  python serve.py autostart on   … Windows にサインインしたら自動で裏起動（off で解除）

Kindle 側の config.sh の SERVER_URL に、起動時に表示されるURLを設定してください。
"""
import argparse
import atexit
import datetime as dt
import functools
import http.server
import os
import signal
import socket
import subprocess
import sys
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


OUT_DIR = os.path.join(render.HERE, "out")
PID_FILE = os.path.join(OUT_DIR, "serve.pid")
LOG_FILE = os.path.join(OUT_DIR, "serve.log")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "KindleDashboardServer"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pythonw():
    if os.name != "nt":
        return sys.executable
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def child_cmd(demo):
    return [pythonw(), os.path.abspath(__file__), "--child"] + (["--demo"] if demo else [])


def is_alive(pid):
    if os.name == "nt":
        # Windows の os.kill(pid, 0) は生存確認ではなくプロセスを終了させてしまうので tasklist で調べる
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout
        return "python" in out.lower()
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if is_alive(pid) else None


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def tail_log(n=20):
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "（ログはまだありません）"


def cmd_start(demo, port):
    pid = running_pid()
    if pid:
        print(f"すでに裏で起動しています (PID {pid})  http://{lan_ip()}:{port}")
        return 0
    if port_open(port):
        print(f"ポート {port} は別のプログラムが使っています（別の PowerShell で serve.py が動いていませんか）")
        return 1
    if os.name == "nt":
        kw = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        kw = {"start_new_session": True}
    subprocess.Popen(child_cmd(demo), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, cwd=render.HERE, **kw)
    # 最初の描画で天気を取りに行くので、配信が始まるまで少し待つ
    for _ in range(60):
        time.sleep(0.5)
        if running_pid() and port_open(port):
            print("裏で起動しました。この PowerShell は閉じても大丈夫です")
            print(f"  配信中: http://{lan_ip()}:{port}")
            print(f"  スマホから画像を送る: http://{lan_ip()}:{port}/upload")
            print("  止めるには: python serve.py stop")
            return 0
    print("起動を確認できませんでした。ログの最後:")
    print(tail_log())
    return 1


def cmd_stop():
    pid = running_pid()
    if not pid:
        print("裏では起動していません")
    else:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not is_alive(pid):
                break
            time.sleep(0.25)
        print(f"停止しました (PID {pid})")
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    return 0


def autostart_enabled():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False


def cmd_autostart(value, demo):
    if os.name != "nt":
        print("自動起動の設定は Windows 用です（Raspberry Pi などでは cron の @reboot や systemd を使ってください）")
        return 1
    if value not in ("on", "off"):
        print(f"現在: {'on' if autostart_enabled() else 'off'}   （設定: python serve.py autostart on / off）")
        return 0
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if value == "on":
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(child_cmd(demo)))
            print("サインイン時に自動で裏起動するように設定しました")
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
            print("自動起動を解除しました")
    return 0


def cmd_status(port):
    pid = running_pid()
    if pid:
        print(f"裏で起動中 (PID {pid})  http://{lan_ip()}:{port}")
    else:
        print("裏では起動していません")
    if os.name == "nt":
        print(f"自動起動: {'on' if autostart_enabled() else 'off'}")
    print(f"ログ: {LOG_FILE}")
    return 0


def become_child():
    """裏で動くとき: 画面の代わりにログへ書き、PID を残す"""
    big = os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000
    f = open(LOG_FILE, "w" if big else "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    with open(PID_FILE, "w") as p:
        p.write(str(os.getpid()))

    def cleanup():
        if running_pid() == os.getpid():
            os.remove(PID_FILE)
    atexit.register(cleanup)
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] 裏で起動しました (PID {os.getpid()})")


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
    ap.add_argument("cmd", nargs="?", choices=["start", "stop", "restart", "status", "log", "autostart"])
    ap.add_argument("value", nargs="?", help="autostart のとき on / off")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    cfg = render.load_config()
    port = int(cfg.get("port", 8080))
    out_dir = OUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    if a.cmd == "start":
        return cmd_start(a.demo, port)
    if a.cmd == "stop":
        return cmd_stop()
    if a.cmd == "restart":
        cmd_stop()
        return cmd_start(a.demo, port)
    if a.cmd == "status":
        return cmd_status(port)
    if a.cmd == "log":
        print(tail_log(40))
        return 0
    if a.cmd == "autostart":
        return cmd_autostart(a.value, a.demo)

    if a.child:
        if running_pid():
            return 0
        become_child()
    elif running_pid():
        print("すでに裏で起動しています（python serve.py status で確認 / stop で停止）")
        return 1
    render_once(out_dir, a.demo)
    # タイマー画面は天気や時刻で変わらないので起動時に1回だけ描く
    render.save_timer(out_dir, cfg)
    threading.Thread(target=scheduler, args=(out_dir, a.demo, int(cfg.get("refresh_minutes", 10))), daemon=True).start()
    if not a.demo:
        threading.Thread(target=weather_loop, args=(out_dir, int(cfg.get("weather_minutes", 30))), daemon=True).start()

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
    sys.exit(main())
