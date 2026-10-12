#!/usr/bin/env python3
"""
kindlectl — SSH 経由で Kindle を操作するツール（PC から実行）

必要なもの: Kindle 側に USBNetwork（SSH）、PC 側に ssh コマンド（Windows 10/11 は標準で入っています）
接続先は config.json の "kindle" に書きます。

画像を送る
  python kindlectl.py push 写真フォルダ                 … 変換してスクリーンセーバー用に送る
  python kindlectl.py push a.jpg b.jpg --to slides      … スライドショー用に送る
  python kindlectl.py push 写真 --replace --show        … 今ある画像を消して入れ替え、1枚目をすぐ表示
  python kindlectl.py ls [--to slides]                  … Kindle 上の画像一覧
  python kindlectl.py rm 20261011_0301_01_cat.png       … 1枚消す
  python kindlectl.py clear --to screensaver            … フォルダを空にする

プログラムを更新する
  python kindlectl.py update                 … ../kindle/extensions/signage を送る（config.sh は残す）
  python kindlectl.py update --with-config   … config.sh も上書きする
  python kindlectl.py rollback               … 直前の更新を取り消す

操作・確認
  python kindlectl.py check                  … 接続テストと状態表示
  python kindlectl.py start | stop | ss-on | ss-off | preview
  python kindlectl.py log [-n 50]            … Kindle のログを見る
  python kindlectl.py set SS_METHOD auto     … Kindle の config.sh の設定を変える
  python kindlectl.py get [SS_METHOD]        … 設定を見る
  python kindlectl.py shell                  … Kindle にログイン

Google Drive 同期（詳しくは gdrive.py）
  python kindlectl.py gdrive-setup           … rclone・Google ログイン・Kindle への転送・初回同期
  python kindlectl.py gdrive-sync | gdrive-status | gdrive-ls | gdrive-log
  python kindlectl.py gdrive-wake on|off     … 電源ボタンで復帰したときの同期
  python kindlectl.py gdrive-set KEY VALUE   … Kindle の gdrive/config.sh を変える
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import types

import gdrive

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/mnt/us"
EXT = f"{ROOT}/extensions/signage"
DATA = f"{ROOT}/signage"
TARGETS = ("screensaver", "slides")
LOCAL_EXT = os.path.normpath(os.path.join(HERE, "..", "kindle", "extensions", "signage"))


class KindleError(Exception):
    pass


# ------------------------------------------------------------------ 設定・接続
def load_cfg():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    k = cfg.get("kindle") or {}
    if not k.get("host"):
        raise KindleError('config.json に "kindle": {"host": "KindleのIPアドレス"} を書いてください')
    return cfg, k


def ssh_base(k):
    if not shutil.which("ssh"):
        raise KindleError("ssh コマンドが見つかりません（Windows: 設定 → オプション機能 → OpenSSH クライアント）")
    cmd = ["ssh", "-p", str(k.get("port", 22)),
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=15",
           "-o", "LogLevel=ERROR"]   # 「Welcome to Kindle!」などのあいさつ文を表示しない
    if k.get("identity"):
        cmd += ["-i", os.path.expanduser(k["identity"])]
    return cmd + [f"{k.get('user', 'root')}@{k['host']}"]


# SSH で入ったときの PATH は最小限（/usr/bin:/bin）なので、eips（/usr/sbin）などが見つからない。
# KUAL から動かすときと同じ PATH にそろえてから実行する
KINDLE_PATH = "export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/mnt/us/usbnet/bin:$PATH; "


def remote(k, script, data=None, check=True):
    """Kindle 上で sh スクリプトを実行。data を渡すと標準入力に流す"""
    # serve.py をウィンドウ無しで動かしているとき、ssh のたびに黒い窓が出ないようにする
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(ssh_base(k) + [KINDLE_PATH + script], input=data, capture_output=True,
                       creationflags=flags)
    out = p.stdout.decode("utf-8", "replace")
    err = "\n".join(l for l in p.stderr.decode("utf-8", "replace").splitlines()
                    if l.strip() and "Welcome to Kindle" not in l)
    if check and p.returncode != 0:
        if p.returncode == 255:
            raise KindleError(f"Kindle に接続できません: {err.strip()}\n"
                              "  ・Kindle の Wi-Fi が ON か / USBNetwork の SSH が有効か / config.json の host を確認")
        hint = "（コマンドが見つかりません）" if p.returncode == 127 else ""
        raise KindleError(f"Kindle 上でエラー (code {p.returncode}){hint}: {err.strip() or out.strip()}")
    return out


def make_tar(files):
    """{Kindle上の相対パス: (bytes, mode)} → tar のバイト列。ssh だけで送れる（scp/sftp 不要）"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        now = int(dt.datetime.now().timestamp())
        for rel, (data, mode) in files.items():
            ti = tarfile.TarInfo(rel)
            ti.size, ti.mode, ti.mtime = len(data), mode, now
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def upload(k, files, dest=ROOT):
    remote(k, f"mkdir -p {shlex.quote(dest)} && tar -xf - -C {shlex.quote(dest)}", data=make_tar(files))


def sh_script(name, *args):
    a = " ".join(shlex.quote(x) for x in args)
    return f"sh {EXT}/bin/{name} {a} >/dev/null 2>&1 </dev/null"


# ------------------------------------------------------------------ 状態
STATE_SH = f"""
s=stopped; [ -f /var/tmp/signage_loop.pid ] && kill -0 "$(cat /var/tmp/signage_loop.pid)" 2>/dev/null && s=running
ss=off; [ -f /var/tmp/signage_ss.pid ] && kill -0 "$(cat /var/tmp/signage_ss.pid)" 2>/dev/null && ss=on
grep -q " /usr/share/blanket/screensaver " /proc/mounts 2>/dev/null && ss=on
echo "signage=$s screensaver=$ss"
"""


def get_state(k):
    out = remote(k, STATE_SH)
    return dict(kv.split("=", 1) for kv in out.split())


# ------------------------------------------------------------------ 画像
def convert(paths, a, device):
    import convert_images as ci
    size = ci.DEVICES[device]
    files = ci.collect(paths)
    if not files:
        raise KindleError("送れる画像が見つかりませんでした")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {}
    for i, p in enumerate(files, 1):
        from PIL import Image
        with Image.open(p) as im:
            img = ci.to_eink(im, size, a)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(p))[0]).strip("_")[:30] or "image"
        name = f"{stamp}_{i:02d}_{stem}.png"
        b = io.BytesIO()
        img.save(b, "PNG", optimize=True)
        out[name] = b.getvalue()
        print(f"  変換 {os.path.basename(p)} → {name}")
    return out


def convert_opts(fit="cover", bg="white", rotate="none", no_dither=False,
                 contrast=1.15, brightness=1.05, sharpen=1.0):
    return types.SimpleNamespace(fit=fit, bg=bg, rotate=rotate, no_dither=no_dither,
                                 contrast=contrast, brightness=brightness, sharpen=sharpen)


def push_images(k, images, to="screensaver", replace=False, show=False):
    """変換済み画像 {name: bytes} を Kindle に送る。サーバーのアップロード画面からも使う"""
    if to not in TARGETS:
        raise KindleError(f"--to は {TARGETS} のどれか")
    folder = f"{DATA}/{to}"
    if replace:
        remote(k, f"mkdir -p {folder} && rm -f {folder}/*.png {folder}/*.PNG")
    upload(k, {f"signage/{to}/{n}": (b, 0o644) for n, b in images.items()})
    # スクリーンセーバー native 方式が ON なら、差し替えた画像を反映し直す
    st = get_state(k)
    if to == "screensaver" and st.get("screensaver") == "on":
        remote(k, sh_script("ss_off.sh") + "; " + sh_script("ss_on.sh"))
    if show and images:
        first = sorted(images)[0]
        remote(k, f"eips -f -g {folder}/{first} >/dev/null 2>&1")
    return list(images)


# ------------------------------------------------------------------ 更新
def collect_update(with_config):
    files = {}
    for dirpath, _, names in os.walk(LOCAL_EXT):
        for n in names:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, LOCAL_EXT).replace(os.sep, "/")
            if rel == "config.sh" and not with_config:
                continue
            with open(full, "rb") as f:
                data = f.read()
            if n.endswith((".sh", ".json", ".xml")):
                if b"\r\n" in data:
                    data = data.replace(b"\r\n", b"\n")   # Windowsで編集されても必ずLFで送る
            mode = 0o755 if (n.endswith(".sh") or n == "touchgrab") else 0o644
            files[f"extensions/signage/{rel}"] = (data, mode)
    if not files:
        raise KindleError(f"更新するファイルが見つかりません: {LOCAL_EXT}")
    # Google Drive 同期の拡張（rclone 本体とログイン情報は含まない。入れていない Kindle には送らない）
    if os.path.isdir(gdrive.LOCAL_GD):
        files.update(gdrive.collect_ext(with_config))
    return files


def do_update(k, with_config=False, dry=False):
    files = collect_update(with_config)
    if not dry:
        has_gd = remote(k, f"[ -f {gdrive.GD}/config.sh ] && echo yes || echo no").strip() == "yes"
        if not has_gd:   # まだ gdrive-setup していない Kindle には Google Drive の拡張を送らない
            files = {n: v for n, v in files.items() if not n.startswith("extensions/gdrive/")}
        elif gdrive.koreader_installed(sys.modules[__name__], k):
            files.update(gdrive.collect_koplugin())
    print(f"送るファイル {len(files)} 個:")
    for f in sorted(files):
        print("  ", f)
    if dry:
        return
    st = get_state(k)
    print(f"Kindle の状態: サイネージ={st['signage']} / スクリーンセーバー={st['screensaver']}")
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    # 1. 動いているものを止める（画面の一時停止やタッチの独占も解除される）
    if st["signage"] == "running":
        remote(k, sh_script("stop.sh"))
    if st["screensaver"] == "on":
        remote(k, sh_script("ss_off.sh"))
    gdrive.stop_wake(sys.modules[__name__], k)
    # 2. 今のプログラムをバックアップ（rollback 用。config.sh も含む。rclone 本体は大きいので含めない）
    remote(k, f"mkdir -p {DATA}/.backup && cd {ROOT}/extensions && "
              f"tar -cf {DATA}/.backup/signage_{ts}.tar signage $(ls -d gdrive/*.sh gdrive/*.json gdrive/*.xml gdrive/bin/*.sh 2>/dev/null) "
              f"&& ls -1t {DATA}/.backup/signage_*.tar | sed -n '6,$p' | xargs rm -f")
    # 3. 送る
    upload(k, files)
    remote(k, f"chmod +x {EXT}/bin/*.sh {EXT}/bin/touchgrab 2>/dev/null; sed -i 's/\\r$//' {EXT}/config.sh")
    # 4. 元の状態に戻す
    if st["screensaver"] == "on":
        remote(k, sh_script("ss_on.sh"))
    if st["signage"] == "running":
        remote(k, sh_script("start.sh"))
    remote(k, f"chmod +x {gdrive.GD}/bin/*.sh 2>/dev/null; true")
    gdrive.ensure_wake(sys.modules[__name__], k)
    remote(k, f"echo \"$(date '+%Y-%m-%d %H:%M:%S') updated via ssh (backup signage_{ts}.tar)\" >> {DATA}/signage.log")
    print(f"更新しました（バックアップ: signage/.backup/signage_{ts}.tar）")


def do_rollback(k):
    out = remote(k, f"ls -1t {DATA}/.backup/signage_*.tar 2>/dev/null | head -1").strip()
    if not out:
        raise KindleError("バックアップがありません")
    st = get_state(k)
    if st["signage"] == "running":
        remote(k, sh_script("stop.sh"))
    if st["screensaver"] == "on":
        remote(k, sh_script("ss_off.sh"))
    remote(k, f"cd {ROOT}/extensions && rm -rf signage && tar -xf {shlex.quote(out)} && rm -f {shlex.quote(out)}")
    if st["screensaver"] == "on":
        remote(k, sh_script("ss_on.sh"))
    if st["signage"] == "running":
        remote(k, sh_script("start.sh"))
    print(f"元に戻しました（{os.path.basename(out)}）")


# ------------------------------------------------------------------ コマンド
def main():
    ap = argparse.ArgumentParser(description="SSH 経由で Kindle を操作")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("push", help="画像を変換して送る")
    p.add_argument("paths", nargs="+")
    p.add_argument("--to", default="screensaver", choices=TARGETS)
    p.add_argument("--replace", action="store_true", help="今ある画像を消してから送る")
    p.add_argument("--show", action="store_true", help="送った1枚目をすぐ表示")
    p.add_argument("--fit", default="cover", choices=["cover", "contain"])
    p.add_argument("--bg", default="white", choices=["white", "black"])
    p.add_argument("--rotate", default="none", choices=["none", "auto"])
    p.add_argument("--no-dither", action="store_true")
    p.add_argument("--device")

    p = sub.add_parser("ls", help="画像一覧")
    p.add_argument("--to", default="screensaver", choices=TARGETS)
    p = sub.add_parser("rm", help="画像を消す")
    p.add_argument("names", nargs="+")
    p.add_argument("--to", default="screensaver", choices=TARGETS)
    p = sub.add_parser("clear", help="フォルダを空にする")
    p.add_argument("--to", required=True, choices=TARGETS)

    p = sub.add_parser("update", help="プログラムを更新")
    p.add_argument("--with-config", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("rollback", help="直前の更新を取り消す")

    p = sub.add_parser("set", help="Kindle の config.sh の設定を1つ変える（例: set SS_METHOD auto）")
    p.add_argument("key")
    p.add_argument("value")
    p = sub.add_parser("get", help="Kindle の config.sh の設定を表示")
    p.add_argument("key", nargs="?")

    sub.add_parser("check", help="接続テスト")
    for c in ("start", "stop", "ss-on", "ss-off", "preview"):
        sub.add_parser(c)
    p = sub.add_parser("log")
    p.add_argument("-n", type=int, default=40)
    sub.add_parser("shell")
    gdrive.add_commands(sub)
    a = ap.parse_args()

    try:
        cfg, k = load_cfg()
        if gdrive.run(sys.modules[__name__], k, a):
            return
        if a.cmd == "push":
            opts = convert_opts(a.fit, a.bg, a.rotate, a.no_dither)
            imgs = convert(a.paths, opts, a.device or cfg.get("device", "pw3"))
            push_images(k, imgs, a.to, a.replace, a.show)
            print(f"{len(imgs)} 枚を Kindle の signage/{a.to} に送りました")
        elif a.cmd == "ls":
            print(remote(k, f"cd {DATA}/{a.to} 2>/dev/null && ls -1 *.png *.PNG 2>/dev/null; true") or "(空)")
        elif a.cmd == "rm":
            names = " ".join(shlex.quote(os.path.basename(n)) for n in a.names)
            remote(k, f"cd {DATA}/{a.to} && rm -f {names}")
            print("消しました")
        elif a.cmd == "clear":
            remote(k, f"rm -f {DATA}/{a.to}/*.png {DATA}/{a.to}/*.PNG")
            print(f"signage/{a.to} を空にしました")
        elif a.cmd == "update":
            do_update(k, a.with_config, a.dry_run)
        elif a.cmd == "rollback":
            do_rollback(k)
        elif a.cmd == "set":
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", a.key):
                raise KindleError("設定名は英大文字（例: SS_METHOD）")
            if re.search(r"[\"'`$\\\n]", a.value):
                raise KindleError("値に使えない文字が含まれています")
            v = a.value if re.fullmatch(r"[A-Za-z0-9_.:/-]+", a.value) and ":" not in a.value else f'"{a.value}"'
            cf = f"{EXT}/config.sh"
            remote(k, f"grep -q '^{a.key}=' {cf} && sed -i 's#^{a.key}=[^ ]*#{a.key}={v}#' {cf} "
                      f"|| echo '{a.key}={v}' >> {cf}")
            print(remote(k, f"grep '^{a.key}=' {cf}").strip())
            if a.key.startswith("SS_") and get_state(k).get("screensaver") == "on":
                remote(k, sh_script("ss_off.sh") + "; " + sh_script("ss_on.sh"))
                print("スクリーンセーバーを再起動して反映しました")
        elif a.cmd == "get":
            pat = f"^{a.key}=" if a.key else "^[A-Z_]*="
            print(remote(k, f"grep '{pat}' {EXT}/config.sh; true").strip())
        elif a.cmd == "check":
            info = remote(k, "echo host=$(hostname); df -h /mnt/us | tail -1 | awk '{print \"free=\"$4}'")
            print(info.strip())
            print(" ".join(f"{a_}={b}" for a_, b in get_state(k).items()))
            print("接続OK")
        elif a.cmd in ("start", "stop", "ss-on", "ss-off", "preview"):
            script = {"start": "start.sh", "stop": "stop.sh", "ss-on": "ss_on.sh",
                      "ss-off": "ss_off.sh", "preview": "ss_test.sh"}[a.cmd]
            remote(k, sh_script(script) if a.cmd != "preview" else f"(setsid sh {EXT}/bin/ss_test.sh >/dev/null 2>&1 </dev/null &)")
            import time
            time.sleep(2)   # 起動・停止が反映されるのを待ってから状態を表示
            print(f"{a.cmd}: OK  →  " + " ".join(f"{x}={y}" for x, y in get_state(k).items()))
        elif a.cmd == "log":
            print(remote(k, f"tail -n {int(a.n)} {DATA}/signage.log 2>/dev/null; true"))
        elif a.cmd == "shell":
            args = ["ssh", "-p", str(k.get("port", 22))]
            if k.get("identity"):
                args += ["-i", os.path.expanduser(k["identity"])]
            sys.exit(subprocess.call(args + [f"{k.get('user', 'root')}@{k['host']}"]))
    except KindleError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
