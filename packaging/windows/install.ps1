# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
# Installs ZPDF for the current Windows user (no administrator rights
# needed). Start it with "Install ZPDF.cmd" in the ZPDF folder.
#
#   1. Finds Python 3.10 or newer, or installs Python from python.org (with
#      winget, or the official signed installer).
#   2. Installs ZPDF in its own folder: %LOCALAPPDATA%\PDF Manager\app
#      (running it again updates it).
#   3. Adds Start menu and desktop shortcuts, ZPDF in "Open with" for
#      PDFs, ZPDF's actions in Explorer's right-click menu (a ZPDF submenu
#      on PDFs, "Make a PDF with ZPDF" on pictures), and an entry in
#      Settings > Apps to uninstall it. All for this user only.
#   4. Asks whether to add Tesseract and Ghostscript too (unless -WithExtras
#      or -NoExtras says). OCR itself is built in; these add PDF/A output,
#      turning pages upright and a second OCR engine: OCRmyPDF in ZPDF's
#      folder, and the two programs with winget. Skipped when they're
#      already there or winget is missing; it never stops the installation.
#
# The app runs with Python's own pythonw.exe, which is signed, so Windows
# Smart App Control lets it start. This script only uses plain PowerShell
# commands, so it also works where scripts are limited (Constrained
# Language mode).

param(
    [switch]$NoDesktop,   # no desktop shortcut
    [switch]$NoOpenWith,  # don't add to "Open with"
    [switch]$NoContextMenu,  # don't add ZPDF to Explorer's right-click menu
    [switch]$NoLaunch,    # don't start the app at the end
    # -WithOcr and -NoOcr, the names before 0.14, still work.
    [Alias("WithOcr")]
    [switch]$WithExtras,  # also install Tesseract and Ghostscript, without asking
    [Alias("NoOcr")]
    [switch]$NoExtras     # don't install Tesseract and Ghostscript, and don't ask
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # makes downloads much faster
$PythonVersion = "3.13.1"
# winget packages for the second OCR engine (Tesseract) and PDF/A (Ghostscript).
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

# A 64-bit Python 3.10 to 3.14: ONNX Runtime (the built-in OCR engine) has
# Windows builds for those. A newer Python, like 3.15 when it came out, is
# passed over until the engine supports it.
function Test-Python($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $ok = & $exe -c "import sys; print(int(sys.version_info >= (3, 10) and sys.version_info < (3, 15) and sys.maxsize > 2**32))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1")
    } catch { return $false }
}

function Find-Python {
    $candidates = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        # The versions ZPDF is tested with first; "py -3" alone picks the
        # newest Python, which the OCR engine may not support yet.
        foreach ($version in @("3.13", "3.12", "3.14", "3.11", "3.10")) {
            try {
                $found = & py.exe "-$version" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += "$found".Trim() }
            } catch { }
        }
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

function Install-Extras($status) {
    if (-not $status.OcrMyPdf) {
        Say "Adding OCRmyPDF to ZPDF..."
        # Just OCRmyPDF (the version pyproject.toml's "tesseract" extra asks for):
        # ZPDF itself is installed already and needn't be built again.
        & $venvPython -m pip install --disable-pip-version-check --quiet "ocrmypdf>=15"
        if ($LASTEXITCODE -ne 0) { Say "Installing OCRmyPDF didn't work (see above)." }
    }
    if ($status.Tesseract -and $status.Ghostscript) { return }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Say "winget isn't available here, so Tesseract and Ghostscript weren't installed."
        Say "Install them by hand if you need them:"
        if (-not $status.Tesseract) { Say "  Tesseract:   https://github.com/UB-Mannheim/tesseract/wiki" }
        if (-not $status.Ghostscript) { Say "  Ghostscript: https://ghostscript.com/releases/gsdnld.html" }
        return
    }
    if (-not $status.Tesseract) { Install-WithWinget $TesseractId "Tesseract" | Out-Null }
    if (-not $status.Ghostscript) { Install-WithWinget $GhostscriptId "Ghostscript (for PDF/A)" | Out-Null }
}

Write-Host "ZPDF installer" -ForegroundColor Cyan

Step "Looking for Python 3.10 to 3.14"
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
& $venvPython -m pip install --disable-pip-version-check --quiet --upgrade "$source[app,ocr]"
if ($LASTEXITCODE -ne 0) {
    # Most likely the OCR engine has no build for this Python: better ZPDF
    # without it (OCR then uses Tesseract, if installed) than no ZPDF.
    Say "Installing with the built-in OCR engine didn't work (see above); installing ZPDF without it..."
    & $venvPython -m pip install --disable-pip-version-check --quiet --upgrade "$source[app]"
}
if ($LASTEXITCODE -ne 0) { throw "Installing ZPDF's packages failed (see above)." }
Say "Done"

Step "Adding shortcuts, Open with and the right-click menu"
$setup = @("-I", "-m", "pdfmanager", "setup-windows", "--app-dir", $appDir)
if ($NoDesktop) { $setup += "--no-desktop" }
if ($NoOpenWith) { $setup += "--no-open-with" }
if ($NoContextMenu) { $setup += "--no-context-menu" }
& $venvPython @setup | ForEach-Object { Say $_ }
if ($LASTEXITCODE -ne 0) { throw "Adding the shortcuts failed (see above)." }

# Optional, and never stops the installation: ZPDF works without them.
try {
    $status = Get-OcrStatus
    $complete = $status -and $status.OcrMyPdf -and $status.Tesseract -and $status.Ghostscript
    $wanted = $false
    if ($complete -or $NoExtras) {
        $wanted = $false
    } elseif ($WithExtras) {
        $wanted = $true
    } elseif ($env:CI -or $env:GITHUB_ACTIONS -or $env:TF_BUILD) {
        $wanted = $false  # an automated run: no one to ask
    } else {
        Write-Host ""
        Write-Host "ZPDF's OCR is built in. Two free programs can add more:"
        try { $answer = Read-Host "Also install Tesseract and Ghostscript? They add PDF/A output, turning pages upright and a second OCR engine (about 150 MB) [Y/n]" } catch { $answer = "n" }
        $wanted = ("$answer".Trim() -notmatch "^(n|no)$")
    }
    if ($complete) {
        Step "Tesseract and Ghostscript"
        Say "Already installed"
    } elseif ($wanted) {
        Step "Adding Tesseract and Ghostscript"
        if (-not $status) { $status = @{ OcrMyPdf = $false; Tesseract = $false; Ghostscript = $false } }
        Install-Extras $status
        $after = Get-OcrStatus
        if ($after) {
            foreach ($part in @(@("OCRmyPDF", $after.OcrMyPdf), @("Tesseract (second OCR engine)", $after.Tesseract),
                                @("Ghostscript (PDF/A)", $after.Ghostscript))) {
                if ($part[1]) { Say "$($part[0]): ready" } else { Say "$($part[0]): not installed" }
            }
        }
    }
} catch {
    Say "Tesseract and Ghostscript weren't added ($_). ZPDF works without them; run the installer again with -WithExtras to retry."
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
