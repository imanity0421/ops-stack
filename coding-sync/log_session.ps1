#Requires -Version 5.1
<#
.SYNOPSIS
  封装 log_session.py：会话摘要或命令运行记录。
.EXAMPLE
  .\log_session.ps1 session -Title "改 ops-agent" -Body "完成 skill_id / manifest 目录改造"
  .\log_session.ps1 run -Cmd "pytest -q" -ExitCode 0
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("session", "run")]
    [string]$Action = "session",

    [string]$Title = "",
    [string]$Body = "",
    [string]$Cmd = "",
    [int]$ExitCode = 0,
    [string]$Cwd = "",
    [string]$Note = ""
)

$py = Join-Path $PSScriptRoot "log_session.py"
if (-not (Test-Path $py)) {
    Write-Error "找不到 log_session.py: $py"
    exit 1
}

if ($Action -eq "session") {
    if (-not $Title) {
        Write-Error "session 模式需要 -Title"
        exit 1
    }
    $args = @("session", "--title", $Title)
    if ($Body) { $args += @("--body", $Body) }
    & python $py @args
    exit $LASTEXITCODE
}

if ($Action -eq "run") {
    if (-not $Cmd) {
        Write-Error "run 模式需要 -Cmd"
        exit 1
    }
    $ra = @("run", "--cmd", $Cmd, "--exit-code", $ExitCode)
    if ($Cwd) { $ra += @("--cwd", $Cwd) }
    if ($Note) { $ra += @("--note", $Note) }
    & python $py @ra
    exit $LASTEXITCODE
}
