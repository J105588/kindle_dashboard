# ============================================================
#  Kindle SSH 鍵セットアップ（Windows PowerShell 用）
#
#  使い方（Kindle を USB でつなぎ、ドライブとして見えている状態で）:
#    cd C:\dev\tmp\kindle_dashboard\server
#    powershell -ExecutionPolicy Bypass -File .\setup_ssh.ps1 -Drive E:
#
#  やること:
#    1. PC に鍵（~/.ssh/kindle と kindle.pub）が無ければ作る
#    2. Kindle の usbnet\etc\authorized_keys に公開鍵を書き込む
#       （拡張子なし・LF・BOMなし。既にある鍵は消さずに追加）
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Drive
)
$ErrorActionPreference = "Stop"
$Drive = $Drive.TrimEnd('\').TrimEnd(':') + ':'

# --- 0. 確認 ---
if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
    Write-Host "ssh-keygen が見つかりません。設定 → システム → オプション機能 → 'OpenSSH クライアント' を追加してください" -ForegroundColor Red
    exit 1
}
$etc = Join-Path $Drive "usbnet\etc"
if (-not (Test-Path (Join-Path $Drive "documents"))) {
    Write-Host "$Drive は Kindle のドライブではないようです（documents フォルダがありません）" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $etc)) {
    Write-Host "$etc がありません。先に USBNetwork をインストールしてください（mrpackages に .bin → ;log mrpi）" -ForegroundColor Red
    exit 1
}

# --- 1. 鍵を作る ---
$sshDir = Join-Path $HOME ".ssh"
$key = Join-Path $sshDir "kindle"
if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }
if (Test-Path $key) {
    Write-Host "[1/2] 鍵は作成済み: $key"
} else {
    Write-Host "[1/2] 鍵を作成します: $key"
    # 空パスフレーズ（PowerShell 5 / 7 の両方で動く書き方）
    & ssh-keygen -q -t ed25519 -f $key -N '""' -C "kindle-signage" 2>$null
    if (-not (Test-Path $key)) { & ssh-keygen -q -t ed25519 -f $key -N "" -C "kindle-signage" }
    if (-not (Test-Path $key)) { Write-Host "鍵の作成に失敗しました" -ForegroundColor Red; exit 1 }
}
$pub = (Get-Content "$key.pub" -Raw).Trim()

# --- 2. Kindle に登録 ---
$ak = Join-Path $etc "authorized_keys"
if (Test-Path $ak -PathType Container) {
    Write-Host "$ak がフォルダになっています。削除してからもう一度実行してください" -ForegroundColor Red
    exit 1
}
$lines = @()
if (Test-Path $ak) {
    $lines = ((Get-Content $ak -Raw) -replace "`r", "").Split("`n") | Where-Object { $_.Trim() -ne "" }
}
if ($lines -contains $pub) {
    Write-Host "[2/2] この鍵は登録済みです: $ak"
} else {
    $lines += $pub
    Write-Host "[2/2] 登録しました: $ak"
}
$text = ($lines -join "`n") + "`n"
[IO.File]::WriteAllText($ak, $text, (New-Object System.Text.UTF8Encoding($false)))   # LF・BOMなし

# --- 確認表示 ---
$bytes = [IO.File]::ReadAllBytes($ak)
$hasCR = $bytes -contains 13
Write-Host ""
Write-Host "  authorized_keys: $($lines.Count) 個の鍵 / 改行 $(if ($hasCR) {'CRLF（NG）'} else {'LF（OK）'})"
Write-Host ""
Write-Host "次にやること:" -ForegroundColor Cyan
Write-Host "  1. Kindle を安全に取り外す"
Write-Host "  2. KUAL → USBNetwork で Wi-Fi 経由の SSH を有効にし、USBNetwork を ON"
Write-Host "  3. 接続テスト:  ssh -i `"$key`" root@<KindleのIP>"
Write-Host "  4. config.json の kindle.host に IP を書いて  python kindlectl.py check"
