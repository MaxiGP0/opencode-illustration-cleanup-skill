$ErrorActionPreference = "Stop"

$SkillDir = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $SkillDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$env:U2NET_HOME = Join-Path $SkillDir "models\rembg"
$env:TORCH_HOME = Join-Path $SkillDir "models\torch"
$env:HF_HOME = Join-Path $SkillDir "models\hf"
$env:PYTHONUTF8 = "1"

& $VenvPython (Join-Path $PSScriptRoot "pipeline.py") @args
exit $LASTEXITCODE
