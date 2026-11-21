# 檢查 digiRunner 的身分驗證與權限設定（docs/digirunner_setup.md 的第 6 步）。
# 用法（在專案資料夾）：powershell -ExecutionPolicy Bypass -File deploy/digirunner/check_auth.ps1
# 需要：digiRunner 與 world-api 都開著，.env 裡有兩個 client 的帳密。密碼和 token 都不會印出來。
param([string]$BaseUrl = "http://localhost:31080")

$envFile = Join-Path $PSScriptRoot "..\..\.env"
if (-not (Test-Path $envFile)) { Write-Host "找不到 .env：$envFile"; exit 1 }

function EnvGet($name) {
    $line = Get-Content $envFile | Select-String "^$name=" | Select-Object -Last 1
    if (-not $line) { Write-Host ".env 裡沒有 $name"; exit 1 }
    $line.Line.Split('=', 2)[1].Trim()
}

# digiRunner 的 Client Credentials：Basic Base64(帳號 + ":" + Base64(密碼))
function Get-Token($id, $secret) {
    $inner = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($secret))
    $basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${id}:$inner"))
    try {
        (Invoke-RestMethod -Method Post "$BaseUrl/oauth/token" -Headers @{ Authorization = "Basic $basic" } `
            -ContentType "application/x-www-form-urlencoded" -Body "grant_type=client_credentials").access_token
    } catch {
        Write-Host "client $id 換 token 失敗（$($_.Exception.Response.StatusCode.value__)）：檢查 .env 的帳密，以及 Token 設定是否勾了 Client Credentials"
        exit 1
    }
}

# 401 / 403 是 digiRunner 擋下；其他狀態碼代表已經放行到 world-api
# （例如 run 已經跑完時 world-api 會回 409，這仍然代表身分驗證通過了）
function Test-Call($label, $url, $token, $expect) {
    $headers = @{}
    if ($token) { $headers.Authorization = "Bearer $token" }
    try {
        Invoke-WebRequest $url -Headers $headers -UseBasicParsing | Out-Null
        $code = 200
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
    }
    $result = if ($code -eq 401 -or $code -eq 403) { "擋下" } else { "放行" }
    $mark = if ($result -eq $expect) { "OK " } else { "NG!" }
    $script:failed += [int]($result -ne $expect)
    "{0}  {1,-30} {2}（{3}），預期{4}" -f $mark, $label, $result, $code, $expect
}

$gov = Get-Token (EnvGet "DIGIRUNNER_CLIENT_ID") (EnvGet "DIGIRUNNER_CLIENT_SECRET")
$fe = Get-Token (EnvGet "DIGIRUNNER_FRONTEND_CLIENT_ID") (EnvGet "DIGIRUNNER_FRONTEND_CLIENT_SECRET")
$rid = (Invoke-RestMethod "$BaseUrl/runs" -Headers @{ Authorization = "Bearer $gov" })[0].run_id
"兩個 client 都換到 token 了，目前的 run_id：$rid"
""
$script:failed = 0
Test-Call "1. 不帶 token 呼叫 /runs" "$BaseUrl/runs" $null "擋下"
Test-Call "2. 政府呼叫政府的路徑" "$BaseUrl/runs/$rid/government/observation" $gov "放行"
Test-Call "3. 政府呼叫 /world/disasters" "$BaseUrl/world/disasters" $gov "擋下"
Test-Call "4. 前端呼叫 /world/disasters" "$BaseUrl/world/disasters" $fe "放行"
Test-Call "5. 前端呼叫政府的路徑" "$BaseUrl/runs/$rid/government/observation" $fe "擋下"
""
if ($script:failed) { "有 $($script:failed) 項和預期不同，對照 docs/digirunner_setup.md 的「常見問題」"; exit 1 }
"全部符合預期"
