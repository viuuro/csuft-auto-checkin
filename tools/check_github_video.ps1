# 验证 GitHub 仓库上视频交付物的可见性。
# 用法: pwsh -File tools/check_github_video.ps1
$ErrorActionPreference = "Continue"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$repo = "viuuro/csuft-auto-checkin"

Write-Host "===== 远端 main 最新提交 ====="
$sha = & $gh api "repos/$repo/commits/main" --jq ".sha"
$msg = & $gh api "repos/$repo/commits/main" --jq ".commit.message"
Write-Host "  $($sha.Substring(0,7))"
Write-Host "  $($msg -split "`n" | Select-Object -First 1)"

Write-Host "`n===== video-intro/renders 内容 ====="
& $gh api "repos/$repo/contents/video-intro/renders" --jq ".[] | .name"
if ($LASTEXITCODE -ne 0) { Write-Host "  （空或不可访问）" }

Write-Host "`n===== video-intro 顶层文件 ====="
& $gh api "repos/$repo/contents/video-intro" --jq ".[] | .name"

Write-Host "`n===== 成片文件大小与下载地址 ====="
$size = & $gh api "repos/$repo/contents/video-intro/renders/video-intro-final.mp4" --jq ".size"
$url = & $gh api "repos/$repo/contents/video-intro/renders/video-intro-final.mp4" --jq ".download_url"
Write-Host "  大小: $size B"
Write-Host "  地址: $url"

Write-Host "`n===== 仓库可见性 ====="
$vis = & $gh api "repos/$repo" --jq ".visibility + \"  |  \" + (.size|tostring) + \" KB\""
Write-Host "  $vis"
