$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   AI Test Platform - Aliyun Upload Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Get Server IP
$ServerIP = Read-Host -Prompt "Enter Server IP (e.g. 47.xx.xx.xx)"
if ([string]::IsNullOrWhiteSpace($ServerIP)) {
    Write-Error "IP cannot be empty"
    exit 1
}

$User = "root"
$RemoteBase = "/root/ai_test_platform"

Write-Host ""
Write-Host "Note: You may be prompted for password 3 times." -ForegroundColor Yellow
Write-Host ""

# 2. Create Remote Directories
Write-Host "[1/3] Creating remote directories..." -ForegroundColor Green
ssh ${User}@${ServerIP} "mkdir -p ${RemoteBase}/frontend"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create directories. Please check your password or IP."
    exit 1
}

# 3. Upload Backend
Write-Host "[2/3] Uploading backend code (ai_test_platform)..." -ForegroundColor Green
scp -r .\ai_test_platform ${User}@${ServerIP}:${RemoteBase}/
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upload backend code."
    exit 1
}

# 4. Upload Frontend
Write-Host "[3/3] Uploading frontend build (frontend/dist)..." -ForegroundColor Green
if (-not (Test-Path ".\frontend\dist")) {
    Write-Error "frontend/dist not found! Please run 'npm run build' first."
    exit 1
}
scp -r .\frontend\dist ${User}@${ServerIP}:${RemoteBase}/frontend/
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upload frontend code."
    exit 1
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Upload Complete!" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps on server:"
Write-Host "1. ssh ${User}@${ServerIP}" -ForegroundColor Yellow
Write-Host "2. cd ${RemoteBase}/ai_test_platform" -ForegroundColor Yellow
Write-Host "3. chmod +x deploy/deploy_aliyun.sh" -ForegroundColor Yellow
Write-Host "4. ./deploy/deploy_aliyun.sh" -ForegroundColor Yellow
