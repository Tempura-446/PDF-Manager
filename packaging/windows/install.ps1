# Installs PDF Manager for the current Windows user (no administrator rights
# needed). Start it with "Install PDF Manager.cmd" in the PDF Manager folder.
#
#   1. Finds Python 3.10 or newer, or installs Python from python.org (with
#      winget, or the official signed installer).
#   2. Installs PDF Manager in its own folder: %LOCALAPPDATA%\PDF Manager\app
#      (running it again updates it).
#   3. Adds Start menu and desktop shortcuts, PDF Manager in "Open with" for
#      PDFs, and an entry in Settings > Apps to uninstall it.
#
# The app runs with Python's own pythonw.exe, which is signed, so Windows
# Smart App Control lets it start. This script only uses plain PowerShell
# commands, so it also works where scripts are limited (Constrained
# Language mode).

param(
    [switch]$NoDesktop,   # no desktop shortcut
    [switch]$NoOpenWith,  # don't add to "Open with"
    [switch]$NoLaunch     # don't start the app at the end
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # makes downloads much faster
$PythonVersion = "3.13.1"

function Say($text) { Write-Host "  $text" }
function Step($text) { Write-Host ""; Write-Host "> $text" -ForegroundColor Cyan }

$source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $source "pyproject.toml"))) {
    throw "Run this from the unzipped PDF Manager folder (pyproject.toml not found in $source)."
}
$appDir = Join-Path $env:LOCALAPPDATA "PDF Manager\app"
$venv = Join-Path $appDir "venv"

function Test-Python($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $ok = & $exe -c "import sys; print(int(sys.version_info >= (3, 10) and sys.maxsize > 2**32))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1")
    } catch { return $false }
}

function Find-Python {
    $candidates = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        try {
            $found = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += "$found".Trim() }
        } catch { }
    }
    foreach ($cmd in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        # Skip the Microsoft Store shortcut that only opens the Store.
        if ($cmd.Source -notlike "*\WindowsApps\*") { $candidates += $cmd.Source }
    }
    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")) {
        $candidates += @(Get-ChildItem -Path "$root\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object -Property FullName -Descending | ForEach-Object { $_.FullName })
    }
    foreach ($exe in $candidates) {
        if (Test-Python $exe) { return $exe }
    }
    return $null
}

function Install-Python {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        Say "Installing Python with winget (from python.org)..."
        & winget.exe install --exact --id Python.Python.3.13 --scope user --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
        $found = Find-Python
        if ($found) { return $found }
    }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-$arch.exe"
    $file = Join-Path $env:TEMP "python-$PythonVersion-$arch.exe"
    Say "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    $signature = Get-AuthenticodeSignature -FilePath $file
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
        Remove-Item $file -Force
        throw "The downloaded Python installer isn't signed by the Python Software Foundation; not running it."
    }
    Say "Installing Python $PythonVersion (just for you)..."
    $p = Start-Process -FilePath $file -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
        "InstallLauncherAllUsers=0", "Include_test=0", "Include_doc=0")
    Remove-Item $file -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { throw "The Python installer stopped with code $($p.ExitCode)." }
    $found = Find-Python
    if (-not $found) { throw "Python was installed but can't be found. Restart Windows and run the installer again." }
    return $found
}

Write-Host "PDF Manager installer" -ForegroundColor Cyan

Step "Looking for Python 3.10 or newer"
$python = Find-Python
if ($python) { Say "Found $python" } else { $python = Install-Python; Say "Installed $python" }

Step "Installing PDF Manager in $appDir"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Python $venvPython)) {
    if (Test-Path $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the app's Python environment." }
}
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade "$source[app]"
if ($LASTEXITCODE -ne 0) { throw "Installing PDF Manager's packages failed (see above)." }
Say "Done"

Step "Adding shortcuts and Open with"
$setup = @("-I", "-m", "pdfmanager", "setup-windows", "--app-dir", $appDir)
if ($NoDesktop) { $setup += "--no-desktop" }
if ($NoOpenWith) { $setup += "--no-open-with" }
& $venvPython @setup | ForEach-Object { Say $_ }
if ($LASTEXITCODE -ne 0) { throw "Adding the shortcuts failed (see above)." }

Write-Host ""
Write-Host "PDF Manager is installed. Find it in the Start menu" -NoNewline -ForegroundColor Green
if (-not $NoDesktop) { Write-Host " or on your desktop" -NoNewline -ForegroundColor Green }
Write-Host "." -ForegroundColor Green
Write-Host "To remove it: Settings > Apps > Installed apps > PDF Manager > Uninstall."
if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $venv "Scripts\pythonw.exe") -ArgumentList "-I", "-m", "pdfmanager", "gui" `
        -WorkingDirectory $env:USERPROFILE
    Start-Sleep -Seconds 4  # time to read the message before the window closes
}
