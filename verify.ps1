[CmdletBinding()]
param(
    [string]$SkillDir = $(if ($env:OPENCODE_SKILLS_DIR) { $env:OPENCODE_SKILLS_DIR } else { Join-Path $HOME ".config\opencode\skills" })
)

$ErrorActionPreference = "Stop"
$Target = Join-Path $SkillDir "illustration-cleanup"
$Errors = @()

$Required = @(
    "SKILL.md",
    "requirements.txt",
    "LICENSE",
    "NOTICE",
    "scripts\pipeline.py",
    "scripts\run.ps1",
    "scripts\run.sh",
    "scripts\bootstrap.ps1",
    "scripts\bootstrap.sh",
    "scripts\validate_output.py"
)

foreach ($Relative in $Required) {
    if (-not (Test-Path (Join-Path $Target $Relative))) {
        $Errors += "Falta: $Relative"
    }
}

$SkillFile = Join-Path $Target "SKILL.md"
if (Test-Path $SkillFile) {
    $Content = Get-Content $SkillFile -Raw
    if ($Content -notmatch "(?m)^name:\s*illustration-cleanup\s*$") {
        $Errors += "SKILL.md no declara name: illustration-cleanup"
    }
    if ($Content -notmatch "(?m)^description:\s*\S+") {
        $Errors += "SKILL.md no tiene description"
    }
}

if ($Errors.Count -gt 0) {
    Write-Host "Verificación fallida en $Target" -ForegroundColor Red
    $Errors | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host "OK: $Target"
Write-Host "Archivos requeridos presentes; SKILL.md válido."
