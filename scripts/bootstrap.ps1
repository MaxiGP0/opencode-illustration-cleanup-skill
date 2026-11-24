$ErrorActionPreference = "Stop"

$SkillDir = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $SkillDir "tools"
$UvRoot = Join-Path $ToolsDir "uv"
$UvExe = Join-Path $UvRoot "uv.exe"
$Venv = Join-Path $SkillDir ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

if (-not (Test-Path $UvExe)) {
    Write-Host "Descargando uv portátil..."
    $zip = Join-Path $ToolsDir "uv.zip"
    Invoke-WebRequest `
        -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" `
        -OutFile $zip
    if (Test-Path $UvRoot) {
        Remove-Item $UvRoot -Recurse -Force
    }
    Expand-Archive -Path $zip -DestinationPath $UvRoot -Force
    $found = Get-ChildItem $UvRoot -Recurse -Filter uv.exe | Select-Object -First 1
    if (-not $found) {
        throw "No encontré uv.exe dentro del archivo descargado."
    }
    $UvExe = $found.FullName
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $ToolsDir "python"
$env:UV_CACHE_DIR = Join-Path $ToolsDir "uv-cache"
$env:UV_PYTHON_PREFERENCE = "only-managed"
New-Item -ItemType Directory -Force -Path $env:UV_PYTHON_INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null

Write-Host "Instalando Python 3.12 administrado por uv..."
& $UvExe python install 3.12
if ($LASTEXITCODE -ne 0) {
    throw "Falló la instalación de Python 3.12."
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creando entorno virtual..."
    & $UvExe venv --python 3.12 $Venv
    if ($LASTEXITCODE -ne 0) {
        throw "Falló la creación del entorno virtual."
    }
}

Write-Host "Instalando dependencias (puede tardar varios minutos)..."
& $UvExe pip install --python $VenvPython -r (Join-Path $SkillDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Falló la instalación de dependencias."
}

# These two packages pin old Pillow/NumPy versions in their metadata but their
# code works with the modern stack. Install them without letting pip downgrade.
Write-Host "Instalando RapidOCR y LaMa sin sus pins obsoletos..."
& $UvExe pip install --python $VenvPython --no-deps `
    "rapidocr_onnxruntime==1.4.4" `
    "simple-lama-inpainting==0.1.2"
if ($LASTEXITCODE -ne 0) {
    throw "Falló la instalación de RapidOCR/LaMa."
}

Write-Host ""
Write-Host "Entorno listo."
Write-Host "Python: $VenvPython"
Write-Host "Modelos: $SkillDir\models"
