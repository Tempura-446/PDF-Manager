# Installs ZPDF for the current Windows user (no administrator rights
# needed). Start it with "Install ZPDF.cmd" in the ZPDF folder.
#
#   1. Finds Python 3.10 or newer, or installs Python from python.org (with
#      winget, or the official signed installer).
#   2. Installs ZPDF in its own folder: %LOCALAPPDATA%\PDF Manager\app
#      (running it again updates it).
#   3. Adds Start menu and desktop shortcuts, ZPDF in "Open with" for
#      PDFs, and an entry in Settings > Apps to uninstall it.
#   4. Asks whether to add OCR and PDF/A support too (unless -WithOcr or
#      -NoOcr says): OCRmyPDF in ZPDF's folder, and the Tesseract and
#      Ghostscript programs with winget. Skipped when they're already there
#      or winget is missing; it never stops the installation.
#
# The app runs with Python's own pythonw.exe, which is signed, so Windows
# Smart App Control lets it start. This script only uses plain PowerShell
# commands, so it also works where scripts are limited (Constrained
# Language mode).

param(
    [switch]$NoDesktop,   # no desktop shortcut
    [switch]$NoOpenWith,  # don't add to "Open with"
    [switch]$NoLaunch,    # don't start the app at the end
    [switch]$WithOcr,     # also install OCR and PDF/A support, without asking
    [switch]$NoOcr        # don't install OCR and PDF/A support, and don't ask
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # makes downloads much faster
$PythonVersion = "3.13.1"
# winget packages for OCR (Tesseract) and PDF/A (Ghostscript).
$TesseractId = "UB-Mannheim.TesseractOCR"
$GhostscriptId = "ArtifexSoftware.GhostScript"

function Say($text) { Write-Host "  $text" }
function Step($text) { Write-Host ""; Write-Host "> $text" -ForegroundColor Cyan }

$source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $source "pyproject.toml"))) {
    throw "Run this from the unzipped ZPDF folder (pyproject.toml not found in $source)."
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

# What the installed ZPDF finds itself, with the same lookups the app uses:
# OCRmyPDF, Tesseract, Ghostscript (each $true or $false), or $null.
function Get-OcrStatus {
    $ErrorActionPreference = "Continue"  # a warning on stderr isn't a failure
    try {
        $out = & $venvPython -I -c "import importlib.util as u; from pdfmanager import scan, archive; print(int(u.find_spec('ocrmypdf') is not None), int(bool(scan.find_tesseract())), int(bool(archive.find_ghostscript())))" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $found = "$out".Trim() -split " "
        return @{ OcrMyPdf = ($found[0] -eq "1"); Tesseract = ($found[1] -eq "1"); Ghostscript = ($found[2] -eq "1") }
    } catch { return $null }
}

function Install-WithWinget($id, $name) {
    Say "Installing $name with winget..."
    try {
        & winget.exe install --exact --id $id --scope user --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
        if ($LASTEXITCODE -eq 0) { return $true }
        # No installer just for this user: install it for everyone. Windows
        # may ask for permission.
        Say "Installing $name for all users (Windows may ask for permission)..."
        & winget.exe install --exact --id $id --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
        return ($LASTEXITCODE -eq 0)
    } catch {
        Say "Installing $name didn't work: $_"
        return $false
    }
}

function Install-OcrSupport($status) {
    if (-not $status.OcrMyPdf) {
        Say "Adding OCRmyPDF to ZPDF..."
        # Just OCRmyPDF (the version pyproject.toml's "ocr" extra asks for): ZPDF
        # itself is installed already and needn't be built again.
        & $venvPython -m pip install --disable-pip-version-check --quiet "ocrmypdf>=15"
        if ($LASTEXITCODE -ne 0) { Say "Installing OCRmyPDF didn't work (see above)." }
    }
    if ($status.Tesseract -and $status.Ghostscript) { return }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Say "winget isn't available here, so Tesseract and Ghostscript weren't installed."
        Say "Install them by hand if you need them:"
        if (-not $status.Tesseract) { Say "  OCR:   https://github.com/UB-Mannheim/tesseract/wiki" }
        if (-not $status.Ghostscript) { Say "  PDF/A: https://ghostscript.com/releases/gsdnld.html" }
        return
    }
    if (-not $status.Tesseract) { Install-WithWinget $TesseractId "Tesseract (for OCR)" | Out-Null }
    if (-not $status.Ghostscript) { Install-WithWinget $GhostscriptId "Ghostscript (for PDF/A)" | Out-Null }
}

Write-Host "ZPDF installer" -ForegroundColor Cyan

Step "Looking for Python 3.10 or newer"
$python = Find-Python
if ($python) { Say "Found $python" } else { $python = Install-Python; Say "Installed $python" }

Step "Installing ZPDF in $appDir"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Python $venvPython)) {
    if (Test-Path $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the app's Python environment." }
}
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade "$source[app]"
if ($LASTEXITCODE -ne 0) { throw "Installing ZPDF's packages failed (see above)." }
Say "Done"

Step "Adding shortcuts and Open with"
$setup = @("-I", "-m", "pdfmanager", "setup-windows", "--app-dir", $appDir)
if ($NoDesktop) { $setup += "--no-desktop" }
if ($NoOpenWith) { $setup += "--no-open-with" }
& $venvPython @setup | ForEach-Object { Say $_ }
if ($LASTEXITCODE -ne 0) { throw "Adding the shortcuts failed (see above)." }

# Optional, and never stops the installation: ZPDF works without them.
try {
    $status = Get-OcrStatus
    $complete = $status -and $status.OcrMyPdf -and $status.Tesseract -and $status.Ghostscript
    $wanted = $false
    if ($complete -or $NoOcr) {
        $wanted = $false
    } elseif ($WithOcr) {
        $wanted = $true
    } elseif ($env:CI -or $env:GITHUB_ACTIONS -or $env:TF_BUILD) {
        $wanted = $false  # an automated run: no one to ask
    } else {
        Write-Host ""
        Write-Host "OCR makes scanned PDFs searchable; PDF/A is for long-term archiving."
        try { $answer = Read-Host "Also install OCR and PDF/A support? (about 150 MB) [Y/n]" } catch { $answer = "n" }
        $wanted = ("$answer".Trim() -notmatch "^(n|no)$")
    }
    if ($complete) {
        Step "OCR and PDF/A support"
        Say "Already installed"
    } elseif ($wanted) {
        Step "Adding OCR and PDF/A support"
        if (-not $status) { $status = @{ OcrMyPdf = $false; Tesseract = $false; Ghostscript = $false } }
        Install-OcrSupport $status
        $after = Get-OcrStatus
        if ($after) {
            foreach ($part in @(@("OCRmyPDF", $after.OcrMyPdf), @("Tesseract (OCR)", $after.Tesseract),
                                @("Ghostscript (PDF/A)", $after.Ghostscript))) {
                if ($part[1]) { Say "$($part[0]): ready" } else { Say "$($part[0]): not installed" }
            }
        }
    }
} catch {
    Say "OCR and PDF/A support wasn't added ($_). ZPDF works without it; run the installer again with -WithOcr to retry."
}

Write-Host ""
Write-Host "ZPDF is installed. Find it in the Start menu" -NoNewline -ForegroundColor Green
if (-not $NoDesktop) { Write-Host " or on your desktop" -NoNewline -ForegroundColor Green }
Write-Host "." -ForegroundColor Green
Write-Host "To remove it: Settings > Apps > Installed apps > ZPDF > Uninstall."
if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $venv "Scripts\pythonw.exe") -ArgumentList "-I", "-m", "pdfmanager", "gui" `
        -WorkingDirectory $env:USERPROFILE
    Start-Sleep -Seconds 4  # time to read the message before the window closes
}
