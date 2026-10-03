# One-time HomeStream setup for Windows. Safe to re-run.
# Run it from PowerShell in the HomeStream folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor White }
function Ok($text) { Write-Host "    [ok] $text" -ForegroundColor Green }
function Ask($question) {
    $answer = Read-Host "    $question [Y/n]"
    return -not ($answer -match '^[Nn]')
}
function Find-OnPath($name) {
    # Plain file checks on each PATH folder: fast, and no module search.
    foreach ($dir in ($env:PATH -split [IO.Path]::PathSeparator)) {
        if (-not $dir) { continue }
        foreach ($ext in @(".exe", "")) {
            $candidate = Join-Path $dir "$name$ext"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    return $null
}
function Find-Python {
    # The "py" launcher first, then "python" (which on a fresh Windows may be a Store
    # shortcut that prints nothing; that counts as not installed).
    foreach ($c in @(@{ Name = "py"; Args = @("-3") }, @{ Name = "python"; Args = @() })) {
        $exe = Find-OnPath $c.Name
        if (-not $exe) { continue }
        try {
            $ok = & $exe @($c.Args + @("-c", "import sys; print(sys.version_info >= (3, 10))")) 2>$null
            if ($ok -eq "True") { return @{ Exe = $exe; Args = $c.Args } }
        } catch { }
    }
    return $null
}
function Invoke-Python($python, [string[]]$arguments) { & $python.Exe @($python.Args + $arguments) }

Write-Host "HomeStream setup" -ForegroundColor White
Write-Host "Installs what HomeStream needs, puts its Python environment in .venv and creates your settings."
Write-Host "Each step is shown as it runs; it asks before installing Python or Tailscale."

Step "Python"
$python = Find-Python
if (-not $python) {
    if (Ask "Python 3.10 or newer isn't installed. Install Python 3.13 with winget now?") {
        winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $python = Find-Python
    }
    if (-not $python) { throw "Python 3.10+ is required: https://www.python.org/downloads/" }
}
Ok "$(Invoke-Python $python @("--version"))"

Step "HomeStream"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Invoke-Python $python @("-m", "venv", ".venv")
}
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -e ".[tray]"
Ok "installed; the command is .venv\Scripts\homestream.exe"
Write-Host "    No virtual audio device needed: HomeStream records what your speakers play."

Step "Settings"
$settings = & .venv\Scripts\python.exe -m homestream config
Ok "$settings (includes a random token for devices that aren't on Tailscale)"

Step "Tailscale (private network between this computer and your phone)"
$tailscale = Find-OnPath "tailscale"
if (-not $tailscale -and (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")) {
    $tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe"
}
if (-not $tailscale) {
    if (Ask "Tailscale isn't installed. Install it with winget now?") {
        winget install --id Tailscale.Tailscale --exact --accept-package-agreements --accept-source-agreements
        Write-Host "    Sign in from the Tailscale icon by the clock, then re-run setup.ps1 to check."
    } else {
        Write-Host "    Get it from https://tailscale.com/download"
    }
} else {
    $ip = & $tailscale ip -4 2>$null | Select-Object -First 1
    if ($ip) { Ok "connected, this computer is $ip" } else { Write-Host "    Sign in from the Tailscale icon by the clock." }
}

Step "Next steps"
Write-Host "  1. Install Tailscale on your phone and sign in with the same account."
Write-Host "  2. Start HomeStream: double-click scripts\start-relay.cmd"
Write-Host "     (or, without a console window: .venv\Scripts\homestream.exe tray)"
Write-Host "     The first time, Windows asks whether it may use the network: click Allow."
Write-Host "  3. On your phone, scan the QR code it shows (or type the address), then"
Write-Host "     Share -> Add to Home Screen. Play something here and tap Listen."
