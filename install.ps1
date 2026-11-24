[CmdletBinding()]
param(
    [string]$SkillDir = $(if ($env:OPENCODE_SKILLS_DIR) { $env:OPENCODE_SKILLS_DIR } else { Join-Path $HOME ".config\opencode\skills" }),
    [string]$Ref = "main",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$SkillName = "illustration-cleanup"
$Target = Join-Path $SkillDir $SkillName
$Temp = $null

if (Test-Path $Target) {
    if (-not $Force) {
        throw "Ya existe $Target. Usá -Force para reemplazarlo."
    }
    Remove-Item $Target -Recurse -Force
}

if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "SKILL.md"))) {
    $Source = $PSScriptRoot
} else {
    $Temp = Join-Path ([System.IO.Path]::GetTempPath()) ("illustration-cleanup-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $Temp | Out-Null
    $Zip = Join-Path $Temp "repo.zip"
    $Url = "https://codeload.github.com/MaxiGP0/opencode-illustration-cleanup-skill/zip/$Ref"
    Write-Host "Descargando $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Zip
    Expand-Archive -Path $Zip -DestinationPath $Temp -Force
    $Source = Get-ChildItem $Temp -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "SKILL.md") } |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $Source) {
        throw "No encontré SKILL.md en el archivo descargado."
    }
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item (Join-Path $Source "SKILL.md") $Target
Copy-Item (Join-Path $Source "requirements.txt") $Target
Copy-Item (Join-Path $Source "LICENSE") $Target
Copy-Item (Join-Path $Source "NOTICE") $Target
Copy-Item (Join-Path $Source "scripts") $Target -Recurse
Remove-Item (Join-Path $Target "scripts\__pycache__") -Recurse -Force -ErrorAction SilentlyContinue

if ($Temp) {
    Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Instalado en: $Target"
Write-Host "Primera ejecución:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File `"$Target\scripts\run.ps1`" --help"
Write-Host ""
Write-Host "La primera corrida descarga Python, dependencias y modelos (~3 GB)."
