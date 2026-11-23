# 把剪貼簿裡的 GitHub token 存到 data\github_token.txt（不顯示內容），並確認可以讀寫 repo。
# 用法：在 GitHub 產生 token 後按複製，再執行：powershell -File scripts\save-token.ps1
$ErrorActionPreference = "Stop"
$file = Join-Path $PSScriptRoot "..\data\github_token.txt"
$t = (Get-Clipboard -Raw).Trim()
if ($t -notmatch '^github_pat_[A-Za-z0-9_]{40,}$') { Write-Output "剪貼簿裡不是 GitHub token（應以 github_pat_ 開頭），沒有存檔"; exit 1 }
$h = @{ Authorization = "Bearer $t"; Accept = "application/vnd.github+json" }
try {
    $r = Invoke-WebRequest "https://api.github.com/repos/fd6815/hotel-monitor" -Headers $h -UseBasicParsing
} catch { Write-Output "這個 token 讀不到 fd6815/hotel-monitor，沒有存檔：$($_.Exception.Message)"; exit 1 }
$perm = ($r.Content | ConvertFrom-Json).permissions
if (-not $perm.push) { Write-Output "這個 token 沒有寫入權限（Contents 要 Read and write），沒有存檔"; exit 1 }
if (Test-Path $file) { Copy-Item $file "$file.bak" -Force }
[IO.File]::WriteAllText((Resolve-Path (Split-Path $file)).Path + "\github_token.txt", $t)
Set-Clipboard -Value " "   # 清掉剪貼簿，避免 token 留在裡面
Write-Output "已存檔，到期日：$($r.Headers['github-authentication-token-expiration'])（舊 token 備份在 github_token.txt.bak）"
