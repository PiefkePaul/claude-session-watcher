# PostToolUse hook: runs ruff after editing .py files
$json = [Console]::In.ReadToEnd()
try { $d = $json | ConvertFrom-Json } catch { exit 0 }

$fp = if ($d.file_path) { $d.file_path } elseif ($d.tool_input) { $d.tool_input.file_path } else { $null }
if (-not $fp -or $fp -notmatch '\.py$') { exit 0 }

Set-Location "D:\Development\claude-session-watcher"
$result = & .venv-cli\Scripts\ruff.exe check $fp 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Output "ruff found issues in $([System.IO.Path]::GetFileName($fp)):"
    Write-Output ($result | Out-String)
}
exit 0
