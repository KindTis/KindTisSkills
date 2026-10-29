$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $OutputEncoding
[Console]::OutputEncoding = $OutputEncoding
try {
    $reportSettingsPath = $env:CODEX_REPORT_CONFIG
    if (-not $reportSettingsPath) { $reportSettingsPath = Join-Path $env:USERPROFILE '.codex/goal-report/settings.json' }
    $reportSettings = Get-Content -Raw -LiteralPath $reportSettingsPath | ConvertFrom-Json
    $reportEvent = [Console]::In.ReadToEnd()
    $env:CODEX_REPORT_CONFIG = $reportSettingsPath
    $reportEvent | & $reportSettings.python -X utf8 (Join-Path $PSScriptRoot 'lifecycle.py')
} catch {
    '{"systemMessage":"Goal report hook could not run. Check prepare-completion output directly."}'
}
