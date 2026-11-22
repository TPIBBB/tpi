# 同時啟動後端 world-api、前端、政府（各開一個視窗）
# 用法（在 TPIB 資料夾）：
#   powershell -ExecutionPolicy Bypass -File .\start-all.ps1                       # 政府不行動，測連線
#   powershell -ExecutionPolicy Bypass -File .\start-all.ps1 -Controller llm -Config configs/group_c.yaml
param(
    [string]$Config = "configs/baseline.yaml",
    [ValidateSet("none", "llm")] [string]$Controller = "none",
    [int]$Port = 8000
)

$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$api = "http://127.0.0.1:$Port"  # 不用 localhost：Windows 會先試 IPv6，連不上要等約 2 秒

foreach ($dir in $backend, $frontend) {
    if (-not (Test-Path $dir)) { throw "找不到 $dir，請把兩個 repo 都 clone 到 $root" }
}

# 1. 後端 world-api
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle='world-api'; uv run python -m civsim serve --config $Config --port $Port"

# 2. 前端（第一次會先 npm install）
$envFile = Join-Path $frontend ".env"
if (-not (Test-Path $envFile)) { "VITE_API_BASE_URL=$api" | Out-File $envFile -Encoding utf8 }
Start-Process powershell -WorkingDirectory $frontend -ArgumentList "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle='frontend'; if (-not (Test-Path node_modules)) { npm install }; npm run dev"

# 3. 等 world-api 起來，再讓政府開始跑
Write-Host "等待 world-api 啟動..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest "$api/world/state" -UseBasicParsing -TimeoutSec 5 | Out-Null; $ready = $true; break }
    catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw "world-api 60 秒內沒有啟動，請看 world-api 視窗的錯誤訊息" }

# --base-url 明確指定本機，避免 .env 裡的 DIGIRUNNER_BASE_URL 把政府導到 digiRunner
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle='government'; uv run python -m civsim govern --base-url $api --controller $Controller"

Start-Process "http://localhost:5173"
Write-Host "已啟動：world-api $api、前端 http://localhost:5173、政府 = $Controller"
