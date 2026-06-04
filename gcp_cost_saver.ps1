# =============================================================================
# InsureVoice — GCP Weekend Cost Saver Utility
# =============================================================================
# This script helps you temporarily stop or scale down your GCP resources
# (specifically Cloud Run services / Cloud Functions) to save costs over the weekend.

$ProjectID = "voice-sales-agent"
$Region = "us-central1"

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "         InsureVoice GCP Weekend Cost Saver                  " -ForegroundColor Cyan
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
    Write-Host "Option A: Scale Down Cloud Run Services to 0 (Saves all active instance costs, non-destructive)" -ForegroundColor Green
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Gray
    Write-Host "  gcloud run services update compliance_check --max-instances=0 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host "  gcloud run services update rank_products --max-instances=0 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host "  gcloud run services update product_search --max-instances=0 --region=$Region --project=$ProjectID" -ForegroundColor White
    Write-Host ""
    Write-Host "Option B: Delete the Cloud Run Services (Fully destructive, easily redeployed via Cloud Build)" -ForegroundColor Red
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Gray
    Write-Host "  gcloud run services delete compliance_check --region=$Region --project=$ProjectID --quiet" -ForegroundColor White
    Write-Host "  gcloud run services delete rank_products --region=$Region --project=$ProjectID --quiet" -ForegroundColor White
    Write-Host "  gcloud run services delete product_search --region=$Region --project=$ProjectID --quiet" -ForegroundColor White
    Write-Host ""
    Write-Host "Option C: Temporarily Disable Cloud Run & Cloud Functions APIs (Global stop)" -ForegroundColor Yellow
    Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Gray
    Write-Host "  gcloud services disable run.googleapis.com cloudfunctions.googleapis.com --project=$ProjectID" -ForegroundColor White
    Write-Host ""
    Exit
}

# 2. Select action
Write-Host "Please select a cost-saving action for project '$ProjectID':" -ForegroundColor White
Write-Host "  [1] Scale Down to 0 (Recommended): Scale all Cloud Run services to max-instances=0 (stops instances, non-destructive)." -ForegroundColor Gray
Write-Host "  [2] Delete Services: Fully remove all three deployed Cloud Run services (redeployable later)." -ForegroundColor Gray
Write-Host "  [3] Disable APIs: Disable Cloud Run and Cloud Functions APIs globally (stops all usage and costs)." -ForegroundColor Gray
Write-Host "  [4] Exit" -ForegroundColor Gray
Write-Host ""
$choice = Read-Host "Enter option (1-4)"

if ($choice -eq "1") {
    Write-Host ""
    Write-Host "[*] Scaling down Cloud Run services to 0 max-instances..." -ForegroundColor Cyan
    $services = @("compliance_check", "rank_products", "product_search")
    foreach ($service in $services) {
        Write-Host "    Updating $service in $Region..." -ForegroundColor Gray
        & gcloud run services update $service --max-instances=0 --region=$Region --project=$ProjectID --quiet 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [+] Successfully scaled down $service" -ForegroundColor Green
        } else {
            Write-Host "    [-] Service $service not found or failed to update (may not be deployed yet)." -ForegroundColor Yellow
        }
    }
    Write-Host ""
    Write-Host "[+] All active Cloud Run costs have been paused. To restore them on Monday, deploy them again or set max-instances back to normal (e.g. 10)." -ForegroundColor Green
}
elseif ($choice -eq "2") {
    Write-Host ""
    Write-Host "[!] WARNING: This will delete all three Cloud Run services. Are you sure? (y/n): " -NoNewline -ForegroundColor Red
    $confirm = Read-Host
    if ($confirm -eq "y" -or $confirm -eq "yes") {
        $services = @("compliance_check", "rank_products", "product_search")
        foreach ($service in $services) {
            Write-Host "    Deleting $service in $Region..." -ForegroundColor Gray
            & gcloud run services delete $service --region=$Region --project=$ProjectID --quiet 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    [+] Successfully deleted $service" -ForegroundColor Green
            } else {
                Write-Host "    [-] Service $service not found or already deleted." -ForegroundColor Yellow
            }
        }
        Write-Host ""
        Write-Host "[+] All services deleted. You can redeploy them on Monday using your Cloud Build pipeline or the gcp_setup script." -ForegroundColor Green
    } else {
        Write-Host "[*] Cancelled." -ForegroundColor Gray
    }
}
elseif ($choice -eq "3") {
    Write-Host ""
    Write-Host "[!] WARNING: Disabling APIs will stop all related operations and might affect configuration. Are you sure? (y/n): " -NoNewline -ForegroundColor Red
    $confirm = Read-Host
    if ($confirm -eq "y" -or $confirm -eq "yes") {
        Write-Host "[*] Disabling Cloud Run API..." -ForegroundColor Cyan
        & gcloud services disable run.googleapis.com --project=$ProjectID --quiet
        Write-Host "[*] Disabling Cloud Functions API..." -ForegroundColor Cyan
        & gcloud services disable cloudfunctions.googleapis.com --project=$ProjectID --quiet
        Write-Host "[+] APIs disabled successfully! Remember to enable them on Monday." -ForegroundColor Green
    } else {
        Write-Host "[*] Cancelled." -ForegroundColor Gray
    }
}
else {
    Write-Host "[*] Exiting script." -ForegroundColor Gray
}
Write-Host ""
