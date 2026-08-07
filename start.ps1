# Sets up the virtualenv if needed, then plays.
#   .\start.ps1 <YOUR_BOT_TOKEN>
param(
    [Parameter(Position = 0)]
    [string]$Token = $env:CODECHALLENGE_TOKEN,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not $Token) {
    Write-Error 'Usage: .\start.ps1 <YOUR_BOT_TOKEN>   (or set $env:CODECHALLENGE_TOKEN)'
}

if (-not (Test-Path '.venv')) {
    Write-Host 'creating .venv...'
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}

& .\.venv\Scripts\python.exe run.py play $Token @Rest
