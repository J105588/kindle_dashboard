"""
Google Drive 同期（rclone）の PC 側の処理。kindlectl.py の gdrive-* コマンドから使う。

  python kindlectl.py gdrive-setup            … rclone の取得 → Google ログイン → Kindle に転送 → 初回同期
  python kindlectl.py gdrive-sync             … 今すぐ同期して結果を表示
  python kindlectl.py gdrive-status           … 前回の結果・ファイル数
  python kindlectl.py gdrive-ls               … Kindle 上の同期フォルダの一覧
  python kindlectl.py gdrive-log [-n 40]      … Kindle の同期ログ
  python kindlectl.py gdrive-set KEY VALUE    … Kindle の gdrive/config.sh を変える
  python kindlectl.py gdrive-get [KEY]

rclone 本体と Google のログイン情報（rclone.conf）は server/gdrive/ に置き、Git には入れない。

同期のタイミング: 電源ボタンを押して画面がついたとき（スリープからの復帰時）＋ 手動のみ
  python kindlectl.py gdrive-wake on|off      … 復帰時の同期の切り替え
"""
import io
import os
import platform
import shlex
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "gdrive")                 # .gitignore 済み
CONF = os.path.join(WORK, "rclone.conf")
CACERT = os.path.join(WORK, "cacert.pem")
REMOTE_NAME = "kgdrive"

# Kindle 第7世代のカーネルは 3.0 系。Go 1.24 以降でビルドされた rclone は Linux 3.2 以上が必要で動かないため、
# Go 1.23 でビルドされた最後の版に固定する（v1.69.0 = go1.23.4。v1.69.1 以降は go1.24）
RCLONE_VERSION = "v1.69.0"
GH = "https://github.com/rclone/rclone/releases/download"

GD = "/mnt/us/extensions/gdrive"
LOCAL_GD = os.path.normpath(os.path.join(HERE, "..", "kindle", "extensions", "gdrive"))
LOCAL_KOPLUGIN = os.path.normpath(os.path.join(HERE, "..", "kindle", "koreader", "plugins", "gdrivesync.koplugin"))
KO_PLUGIN_DIR = "/mnt/us/koreader/plugins/gdrivesync.koplugin"


class SetupError(Exception):
    pass


# ------------------------------------------------------------------ rclone の取得
def _download(url, label):
    print(f"  ダウンロード中: {label} …", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "kindle-dashboard"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    print(f" {len(data) // 1024 // 1024} MB")
    return data


def _extract(zip_bytes, member_name, dest):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.endswith("/" + member_name))
        with z.open(name) as src, open(dest + ".tmp", "wb") as out:
            shutil.copyfileobj(src, out)
    os.replace(dest + ".tmp", dest)
    os.chmod(dest, 0o755)


def pc_rclone():
    """この PC で使う rclone（Google へのログイン用）"""
    os.makedirs(WORK, exist_ok=True)
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    if system == "Windows":
        osname, exe = "windows", "rclone.exe"
    elif system == "Darwin":
        osname, exe = "osx", "rclone"
    else:
        osname, exe = "linux", "rclone"
    path = os.path.join(WORK, exe)
    if not os.path.exists(path):
        z = _download(f"{GH}/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-{osname}-{arch}.zip", f"rclone {RCLONE_VERSION}（この PC 用）")
        _extract(z, exe, path)
    return path


def kindle_rclone():
    """Kindle（ARMv7）で動かす rclone"""
    os.makedirs(WORK, exist_ok=True)
    path = os.path.join(WORK, f"rclone-{RCLONE_VERSION}-linux-arm-v7")
    if not os.path.exists(path):
        z = _download(f"{GH}/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-linux-arm-v7.zip", f"rclone {RCLONE_VERSION}（Kindle 用）")
        _extract(z, "rclone", path)
    return path


def ca_bundle():
    """Kindle の古い証明書でも Google に HTTPS でつながるよう、新しい証明書の束を同梱する"""
    os.makedirs(WORK, exist_ok=True)
    try:
        import certifi
        shutil.copyfile(certifi.where(), CACERT)
    except ImportError:
        if not os.path.exists(CACERT):
            data = _download("https://curl.se/ca/cacert.pem", "証明書（cacert.pem）")
            with open(CACERT, "wb") as f:
                f.write(data)
    return CACERT


# ------------------------------------------------------------------ Google へのログイン
def _rc(rclone, *args, capture=True):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if capture else 0
    return subprocess.run([rclone, *args, "--config", CONF], capture_output=capture, text=True,
                          encoding="utf-8", errors="replace", creationflags=flags)


def authorized(rclone):
    if not os.path.exists(CONF):
        return False
    p = _rc(rclone, "listremotes")
    return f"{REMOTE_NAME}:" in p.stdout.split()


def authorize(rclone, reauth=False):
    """ブラウザで Google にログインして、読み取り専用の権限を rclone.conf に保存する"""
    if authorized(rclone) and not reauth:
        print("  Google へのログインは済んでいます（やり直すときは --reauth）")
        return
    if reauth and authorized(rclone):
        _rc(rclone, "config", "delete", REMOTE_NAME)
    print()
    print("  ブラウザが開きます。Kindle で使う Google アカウントでログインし、「許可」を押してください。")
    print("  （権限は読み取り専用です。Kindle から Drive のファイルを消したり変えたりはできません）")
    print()
    p = _rc(rclone, "config", "create", REMOTE_NAME, "drive", "scope=drive.readonly", capture=False)
    if p.returncode != 0 or not authorized(rclone):
        raise SetupError("Google へのログインが完了しませんでした。もう一度 gdrive-setup を実行してください")
    print("  ログインできました")


def check_folder(rclone, folder):
    """Drive に同期するフォルダがあるか確かめ、中の対象ファイル数を返す"""
    p = _rc(rclone, "lsf", f"{REMOTE_NAME}:{folder}", "--files-only", "-R",
            "--ignore-case", "--filter", "+ *.pdf", "--filter", "- *")
    if p.returncode != 0:
        if "directory not found" in (p.stderr or ""):
            raise SetupError(f"Google Drive に「{folder}」フォルダがありません。マイドライブの直下に作ってから、もう一度実行してください\n"
                             f"  （別の名前にするときは --folder 名前）")
        raise SetupError(f"Google Drive を読めませんでした: {(p.stderr or '').strip()[-300:]}")
    return len([l for l in p.stdout.splitlines() if l.strip()])


# ------------------------------------------------------------------ Kindle への転送
def _read(path, text=False):
    with open(path, "rb") as f:
        data = f.read()
    if text and b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")   # Windows で編集されても必ず LF で送る
    return data


# 旧版（起床時・定期の自動同期）のファイル。PC に残っていたら消し、Kindle には送らない
OBSOLETE = {"bin/autosync.sh", "bin/auto_on.sh", "bin/auto_off.sh"}


def remove_obsolete_local():
    for rel in OBSOLETE:
        p = os.path.join(LOCAL_GD, *rel.split("/"))
        if os.path.exists(p):
            os.remove(p)
            print(f"  不要になったファイルを削除しました: kindle/extensions/gdrive/{rel}")


def collect_ext(with_config):
    """kindle/extensions/gdrive の中身（rclone 本体とログイン情報は含まない）"""
    remove_obsolete_local()
    files = {}
    for dirpath, _, names in os.walk(LOCAL_GD):
        for n in names:
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, LOCAL_GD).replace(os.sep, "/")
            if (rel == "config.sh" and not with_config) or rel in OBSOLETE:
                continue
            text = n.endswith((".sh", ".json", ".xml"))
            files[f"extensions/gdrive/{rel}"] = (_read(full, text), 0o755 if n.endswith(".sh") else 0o644)
    return files


def collect_koplugin():
    files = {}
    if os.path.isdir(LOCAL_KOPLUGIN):
        for n in os.listdir(LOCAL_KOPLUGIN):
            files[f"koreader/plugins/gdrivesync.koplugin/{n}"] = (_read(os.path.join(LOCAL_KOPLUGIN, n), True), 0o644)
    return files


def koreader_installed(kc, k):
    return kc.remote(k, "[ -d /mnt/us/koreader ] && echo yes || echo no").strip() == "yes"


def install(kc, k, with_config=False, binaries=True):
    """拡張・プラグイン・rclone・ログイン情報を Kindle に送る"""
    has_conf = kc.remote(k, f"[ -f {GD}/config.sh ] && echo yes || echo no").strip() == "yes"
    files = collect_ext(with_config or not has_conf)   # 初回は config.sh も送る
    ko = koreader_installed(kc, k)
    if ko:
        files.update(collect_koplugin())
    if binaries:
        files["extensions/gdrive/bin/rclone"] = (_read(kindle_rclone()), 0o755)
        files["extensions/gdrive/cacert.pem"] = (_read(ca_bundle()), 0o644)
        files["extensions/gdrive/rclone.conf"] = (_read(CONF), 0o600)
    size = sum(len(d) for d, _ in files.values()) // 1024 // 1024
    print(f"  Kindle に送っています（{len(files)} ファイル・約 {size} MB）…")
    stop_wake(kc, k)
    kc.upload(k, files)
    kc.remote(k, f"chmod +x {GD}/bin/*.sh {GD}/bin/rclone 2>/dev/null; sed -i 's/\\r$//' {GD}/config.sh; true")
    ensure_wake(kc, k)
    return ko


# ------------------------------------------------------------------ 状態・操作
def stop_wake(kc, k):
    """更新の前に、復帰時の待ち受けを止める。旧版の自動同期が残っていれば止めて消す"""
    kc.remote(k, f"[ -f {GD}/bin/auto_off.sh ] && sh {GD}/bin/auto_off.sh --silent >/dev/null 2>&1 </dev/null; "
                 f"[ -f {GD}/bin/wake.sh ] && sh {GD}/bin/wake.sh stop >/dev/null 2>&1 </dev/null; "
                 f"rm -f {GD}/bin/auto_on.sh {GD}/bin/auto_off.sh {GD}/bin/autosync.sh; true")


def ensure_wake(kc, k):
    """設定（SYNC_ON_WAKE=1）どおりに復帰時の待ち受けを動かす"""
    kc.remote(k, f"[ -f {GD}/bin/wake.sh ] && sh {GD}/bin/wake.sh ensure --silent >/dev/null 2>&1 </dev/null; true")


def wake_status(kc, k):
    return kc.remote(k, f"sh {GD}/bin/wake.sh status 2>/dev/null; true").strip() or "off"


def read_state(kc, k):
    out = kc.remote(k, f"cat {GD}/state.txt 2>/dev/null; true")
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


def show_state(kc, k):
    st = read_state(kc, k)
    if not st:
        print("まだ同期していません")
        return st
    label = {"ok": "成功", "running": "同期中", "error": "失敗"}.get(st.get("status"), st.get("status"))
    print(f"前回の同期: {label}（{st.get('time', '')}）")
    print(f"  追加 {st.get('added', 0)} ・ 削除 {st.get('deleted', 0)} ・ 更新 {st.get('updated', 0)}"
          f"   Kindle のファイル: {st.get('files', '?')} 件")
    if st.get("status") != "ok" and st.get("message"):
        print(f"  {st['message']}")
    return st


def sync(kc, k):
    print("同期しています…")
    kc.remote(k, f"sh {GD}/bin/sync.sh >/dev/null 2>&1 </dev/null; true")
    return show_state(kc, k)


def set_conf(kc, k, key, value):
    import re
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
        raise SetupError("設定名は英大文字（例: SYNC_INTERVAL_MIN）")
    if re.search(r"[\"'`$\\\n#]", value):
        raise SetupError("値に使えない文字が含まれています")
    v = value if re.fullmatch(r"[A-Za-z0-9_./-]+", value) else f'"{value}"'
    cf = f"{GD}/config.sh"
    kc.remote(k, f"grep -q '^{key}=' {cf} && sed -i 's#^{key}=.*#{key}={v}#' {cf} || echo '{key}={v}' >> {cf}")
    return kc.remote(k, f"grep '^{key}=' {cf}").strip()


# ------------------------------------------------------------------ コマンド
def add_commands(sub):
    p = sub.add_parser("gdrive-setup", help="Google Drive 同期の準備（rclone・ログイン・転送・初回同期）")
    p.add_argument("--folder", default="Kindle", help="同期する Google Drive のフォルダ（既定: Kindle）")
    p.add_argument("--reauth", action="store_true", help="Google へのログインをやり直す")
    p.add_argument("--with-config", action="store_true", help="Kindle の gdrive/config.sh も上書きする")
    p.add_argument("--no-sync", action="store_true", help="転送だけして同期しない")
    sub.add_parser("gdrive-sync", help="今すぐ同期")
    sub.add_parser("gdrive-status", help="同期の状態")
    sub.add_parser("gdrive-ls", help="Kindle 上の同期フォルダの一覧")
    p = sub.add_parser("gdrive-wake", help="電源ボタンで復帰したときの同期の切り替え")
    p.add_argument("value", choices=["on", "off"])
    p = sub.add_parser("gdrive-log", help="Kindle の同期ログ")
    p.add_argument("-n", type=int, default=40)
    p = sub.add_parser("gdrive-set", help="Kindle の gdrive/config.sh を変える")
    p.add_argument("key")
    p.add_argument("value")
    p = sub.add_parser("gdrive-get", help="Kindle の gdrive/config.sh を表示")
    p.add_argument("key", nargs="?")


def run(kc, k, a):
    """kindlectl の main から呼ばれる。gdrive-* 以外なら False を返す"""
    if not a.cmd.startswith("gdrive-"):
        return False
    try:
        if a.cmd == "gdrive-setup":
            print("1/5 rclone を用意します")
            rclone = pc_rclone()
            kindle_rclone()
            ca_bundle()
            print("2/5 Google にログインします")
            authorize(rclone, a.reauth)
            print(f"3/5 Google Drive の「{a.folder}」フォルダを確認します")
            n = check_folder(rclone, a.folder)
            print(f"  PDF {n} 件")
            print("4/5 Kindle に送ります")
            ko = install(kc, k, with_config=a.with_config)
            print("  " + set_conf(kc, k, "GDRIVE_REMOTE", f"{REMOTE_NAME}:{a.folder}"))
            if not ko:
                print("  ※ KOReader が見つからないため、KOReader のメニューは入れていません（入れた後に gdrive-setup を再実行）")
            if a.no_sync:
                print("5/5 同期は省略しました")
            else:
                print("5/5 初回の同期をします（PDF が多いと時間がかかります）")
                sync(kc, k)
            print("完了しました。KOReader で /mnt/us/gdrive を開くと読めます")
            print(f"以後の同期: 電源ボタンで復帰したとき（{wake_status(kc, k)}）＋ 手動（KOReader のメニュー / KUAL / gdrive-sync）")
        elif a.cmd == "gdrive-sync":
            sync(kc, k)
        elif a.cmd == "gdrive-status":
            show_state(kc, k)
            print(f"電源ボタンで復帰したときの同期: {wake_status(kc, k)}")
            print(kc.remote(k, f"grep '^GDRIVE_REMOTE=' {GD}/config.sh 2>/dev/null; "
                               f"[ -f {GD}/bin/rclone ] || echo 'rclone が未インストール（gdrive-setup を実行）'; true").strip())
        elif a.cmd == "gdrive-ls":
            out = kc.remote(k, "cd /mnt/us/gdrive 2>/dev/null && find . -type f ! -path '*.sdr/*' | sed 's#^\\./##' | sort; true")
            print(out.strip() or "(空)")
        elif a.cmd == "gdrive-wake":
            kc.remote(k, f"sh {GD}/bin/wake.sh {a.value} --silent >/dev/null 2>&1 </dev/null; true")
            print(f"電源ボタンで復帰したときの同期: {wake_status(kc, k)}")
        elif a.cmd == "gdrive-log":
            print(kc.remote(k, f"tail -n {int(a.n)} {GD}/gdrive.log 2>/dev/null; true"))
        elif a.cmd == "gdrive-set":
            if a.key == "SYNC_ON_WAKE":   # 待ち受けの開始・停止も合わせて行う
                kc.remote(k, f"sh {GD}/bin/wake.sh {'on' if a.value == '1' else 'off'} --silent >/dev/null 2>&1 </dev/null; true")
            print(set_conf(kc, k, a.key, a.value))
        elif a.cmd == "gdrive-get":
            pat = f"^{a.key}=" if a.key else "^[A-Z_]*="
            print(kc.remote(k, f"grep '{pat}' {GD}/config.sh 2>/dev/null; true").strip())
    except SetupError as e:
        raise kc.KindleError(str(e))
    return True
