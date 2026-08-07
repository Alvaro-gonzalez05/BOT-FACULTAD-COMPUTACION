# Two of your own bots online at once, so you can play when nobody else is.
#
#   .\duel.ps1
#
# Needs a second bot registered on the site (My Bots -> New bot) and both tokens
# in .env:
#
#   CODECHALLENGE_TOKEN=eyJ...        the bot the panel plays as
#   CODECHALLENGE_TOKEN_2=eyJ...      the sparring one
#   CODECHALLENGE_BOT=Alvarinho       their names on the site, so the Challenge
#   CODECHALLENGE_BOT_2=Alvarinho2    button knows which side is which
#   CODECHALLENGE_SESSION=...         your sessionid cookie: the button needs it
#
# Both connect and wait; the match itself is started from the panel's Challenge
# button (or the site's own /challenge page). Ctrl+C here stops both.
#
# Three things are deliberately kept apart from real play:
#
#   * separate log directories -- both sides of a duel share one game_id, so a
#     shared directory would have them overwriting each other's transcript;
#   * --opponent-book none -- a duel says nothing about the real rivals, and
#     letting your own bot into opponents.json would make it a sparring partner
#     for `rivals`, which is the self-play drift the README warns about;
#   * a smaller time budget -- two searchers on one machine queue for the same
#     cores, and a late move is penalised.
param(
    [string]$Bot,
    [string]$Bot2,
    [int]$Port = 8720,
    [int]$Port2 = 8721,
    [string]$LogDir = "duel-a",
    [string]$LogDir2 = "duel-b",
    [double]$TimeBudget = 0.08,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# .env: KEY=value per line, '#' comments ignored.
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

$token = $env:CODECHALLENGE_TOKEN
$token2 = $env:CODECHALLENGE_TOKEN_2
if (-not $Bot) { $Bot = $env:CODECHALLENGE_BOT }
if (-not $Bot2) { $Bot2 = $env:CODECHALLENGE_BOT_2 }

if (-not $token -or -not $token2) {
    Write-Error @'
Two bot tokens are needed, one per bot.

Register a second bot on the site (My Bots -> New bot), then put both in .env:

    CODECHALLENGE_TOKEN=eyJ...
    CODECHALLENGE_TOKEN_2=eyJ...

Get each from My Bots -> Show token.
'@
}

if (-not (Test-Path '.venv')) {
    Write-Host 'creating .venv...'
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

# Invariant, not the current culture: on a Spanish Windows a double stringifies
# as "0,08", and argparse hands that to float() and dies.
$budget = $TimeBudget.ToString([System.Globalization.CultureInfo]::InvariantCulture)

# The sparring bot: no panel, its own transcripts, and it only answers the other
# bot if you named them -- otherwise a passer-by can take its attention while you
# are trying to test something.
#
# Both tokens travel in the environment rather than on a command line: run.py
# falls back to CODECHALLENGE_TOKEN, and a token in an argument list is readable
# by anything that can list processes.
$sparring = @(
    'run.py', 'play',
    '--quiet-board',
    '--log-dir', $LogDir2,
    '--opponent-book', 'none',
    '--time-budget', $budget
)
if ($Bot) { $sparring += @('--accept-from', $Bot) }

$env:CODECHALLENGE_TOKEN = $token2
$second = Start-Process -FilePath $py -ArgumentList $sparring -PassThru
$env:CODECHALLENGE_TOKEN = $token
Write-Host ""
Write-Host "  sparring bot: $(if ($Bot2) { $Bot2 } else { 'bot 2' }) (pid $($second.Id)), transcripts in $LogDir2\" -ForegroundColor DarkGray
Write-Host "  control panel: http://127.0.0.1:$Port" -ForegroundColor Green
if ($Bot2) {
    Write-Host "  pick $Bot2 in the dropdown and press Challenge"
} else {
    Write-Host "  pick your second bot in the dropdown and press Challenge"
}
Write-Host "  press Ctrl+C to stop both"
Write-Host ""

Start-Job -ScriptBlock {
    param($url)
    Start-Sleep -Seconds 2
    Start-Process $url
} -ArgumentList "http://127.0.0.1:$Port" | Out-Null

try {
    & $py run.py play `
        --quiet-board `
        --dashboard $Port `
        --log-dir $LogDir `
        --opponent-book none `
        --time-budget $budget @Rest
} finally {
    if (-not $second.HasExited) {
        Write-Host "stopping the sparring bot (pid $($second.Id))..."
        Stop-Process -Id $second.Id -Force -ErrorAction SilentlyContinue
    }
}
