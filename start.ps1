# Sets up the virtualenv if needed, then plays with the control panel open.
#
#   .\start.ps1                      reads .env, opens the panel in your browser
#   .\start.ps1 <TOKEN>              use a token directly
#   .\start.ps1 -NoPanel             console only
#
# .env is read automatically, so once your token is in there this is the whole
# command. That file is gitignored -- never put a token on the command line of a
# machine you share, it lands in the shell history.
param(
    [Parameter(Position = 0)]
    [string]$Token,

    [int]$Port = 8720,
    [switch]$NoPanel,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# .env: KEY=value per line, '#' comments ignored. Everything lands in the
# process environment, which is where run.py and the panel look for it.
if (Test-Path '.env') {
    foreach ($line in Get-Content '.env') {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $split = $trimmed.IndexOf('=')
        if ($split -lt 1) { continue }
        $name = $trimmed.Substring(0, $split).Trim()
        $value = $trimmed.Substring($split + 1).Trim()
        Set-Item -Path "env:$name" -Value $value
    }
}

if (-not $Token) {
    $Token = $env:CODECHALLENGE_TOKEN
    if (-not $Token) { $Token = $env:ALVARINHO_TOKEN }
}
if (-not $Token) {
    Write-Error @'
No bot token.

Put it in .env as   CODECHALLENGE_TOKEN=eyJ...
or pass it:         .\start.ps1 <YOUR_BOT_TOKEN>

Get it from the site: My Bots -> Show token.
'@
}

if (-not (Test-Path '.venv')) {
    Write-Host 'creating .venv...'
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}

if ($NoPanel) {
    & .\.venv\Scripts\python.exe run.py play $Token @Rest
    return
}

# Give the server a moment to bind before the browser asks for the page.
Start-Job -ScriptBlock {
    param($url)
    Start-Sleep -Seconds 2
    Start-Process $url
} -ArgumentList "http://127.0.0.1:$Port" | Out-Null

Write-Host ""
Write-Host "  control panel: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  press Ctrl+C to stop"
Write-Host ""

& .\.venv\Scripts\python.exe run.py play $Token --quiet-board --dashboard $Port @Rest
