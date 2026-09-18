$ErrorActionPreference = "Stop"

$projectParent = Split-Path $PSScriptRoot -Parent
$sourceFolder = Join-Path $projectParent "wuma_ai_v1_3_15"
$sourceEnv = Join-Path $sourceFolder ".env"
$targetEnv = Join-Path $PSScriptRoot ".env"
$sourceDatabase = Join-Path $sourceFolder "data\wuma_ai.db"
$targetDataFolder = Join-Path $PSScriptRoot "data"
$targetDatabase = Join-Path $targetDataFolder "wuma_ai.db"

if (-not (Test-Path $sourceFolder)) {
    Write-Host "Cannot find wuma_ai_v1_3_15 beside this folder." -ForegroundColor Red
    exit 1
}

if (Test-Path $targetEnv) {
    Write-Host "The v1.3.16 .env already exists. It was not overwritten." -ForegroundColor Yellow
} elseif (Test-Path $sourceEnv) {
    Copy-Item $sourceEnv $targetEnv
    Write-Host "Copied .env from v1.3.15." -ForegroundColor Green
} else {
    Write-Host "No .env found in v1.3.15. Create it from .env.example." -ForegroundColor Yellow
}

if (Test-Path $targetDatabase) {
    Write-Host "The v1.3.16 database already exists. It was not overwritten." -ForegroundColor Yellow
} elseif (Test-Path $sourceDatabase) {
    New-Item -ItemType Directory -Path $targetDataFolder -Force | Out-Null
    Copy-Item $sourceDatabase $targetDatabase
    Write-Host "Copied the database from v1.3.15." -ForegroundColor Green
} else {
    Write-Host "No v1.3.15 database found. v1.3.16 will create a new one." -ForegroundColor Yellow
}

Write-Host "Upgrade preparation completed. Current folder:" -ForegroundColor Cyan
Write-Host $PSScriptRoot -ForegroundColor Cyan
Write-Host "Update LearnBuddy mcp.json paths from v1_3_15 to v1_3_16, then restart LearnBuddy." -ForegroundColor Cyan
