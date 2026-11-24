$ErrorActionPreference = "Stop"
$SkillDir = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $SkillDir ".venv\Scripts\python.exe"
& $VenvPython (Join-Path $PSScriptRoot "validate_output.py") @args
exit $LASTEXITCODE
