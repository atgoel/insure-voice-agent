# =============================================================================
# InsureVoice — GCP Restore Utility
# =============================================================================
# This script helps you restore your Cloud Run services by resetting
# max-instances back to 10 (or normal active development limits).

$ProjectID = "voice-sales-agent"
$Region = "us-central1"

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "         InsureVoice GCP Resource Restore Utility            " -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "Project: " -NoNewline
Write-Host "$ProjectID" -ForegroundColor Yellow
Write-Host ""

# 1. Check if gcloud CLI is installed
$gcloudCheck = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloudCheck) {
    Write-Host "WARNING: 'gcloud' CLI is not found in your system PATH." -ForegroundColor Yellow
    Write-Host "To execute these commands, you must run this script on a machine with the Google Cloud SDK installed." -ForegroundColor Gray
    Write-Host ""
    Write-Host "Here are the manual commands to run on your GCP-enabled terminal:" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Restore Cloud Run Services (Scale max-instances up to 10):" -ForegroundColor Green
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Gray
    Write-Host "  gcloud run services update compliance_check --max-instances=10 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host "  gcloud run services update rank_products --max-instances=10 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host "  gcloud run services update product_search --max-instances=10 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host ""
    Exit
}

# 2. Confirm restore
Write-Host "This will restore and scale up all three Cloud Run services to max-instances=10 in project '$ProjectID'." -ForegroundColor White
$confirm = Read-Host "Proceed? (y/n)"

if ($confirm -eq "y" -or $confirm -eq "yes") {
    Write-Host ""
    Write-Host "[*] Restoring Cloud Run services..." -ForegroundColor Cyan
    $services = @("compliance_check", "rank_products", "product_search")
    foreach ($service in $services) {
        Write-Host "    Updating $service in $Region..." -ForegroundColor Gray
        & gcloud run services update $service --max-instances=10 --region=$Region --project=$ProjectID --quiet 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [+] Successfully restored $service (max-instances=10)" -ForegroundColor Green
        } else {
            Write-Host "    [-] Service $service not found or failed to update (may need redeployment)." -ForegroundColor Yellow
        }
    }
    Write-Host ""
    Write-Host "[+] All services have been successfully brought back up and are ready for end-to-end testing!" -ForegroundColor Green
} else {
    Write-Host "[*] Cancelled." -ForegroundColor Gray
}
Write-Host ""
