# =============================================================================
# InsureVoice — Google Cloud Platform Project Setup Utility
# =============================================================================
# This script helps you link this local codebase with the 'voice-sales-agent'
# GCP project, configure the gcloud CLI, and enable the required services.

$ProjectID = "voice-sales-agent"
$Region = "us-central1"

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "         InsureVoice GCP Project Setup Utility               " -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "Linking local codebase to GCP Project: " -NoNewline
Write-Host "$ProjectID" -ForegroundColor Yellow
Write-Host ""

# 1. Check if gcloud CLI is installed and available
$gcloudCheck = Get-Command gcloud -ErrorAction SilentlyContinue

if (-not $gcloudCheck) {
    Write-Host "WARNING: 'gcloud' CLI is not found in your system PATH." -ForegroundColor Yellow
    Write-Host "To fully link and deploy to Google Cloud, please install the Google Cloud SDK:" -ForegroundColor Gray
    Write-Host "  1. Download the installer from: https://cloud.google.com/sdk/docs/install#windows" -ForegroundColor White
    Write-Host "  2. Run the installer and complete the setup wizard." -ForegroundColor White
    Write-Host "  3. Open a new PowerShell window and re-run this script." -ForegroundColor White
    Write-Host ""
    Write-Host "Alternatively, if it is already installed, make sure its path is added to your environment variables." -ForegroundColor Gray
    Write-Host ""
    Write-Host "Once 'gcloud' is installed, you can link the project manually by running:" -ForegroundColor Gray
    Write-Host "  gcloud config set project $ProjectID" -ForegroundColor White
    Write-Host ""
    Exit
}

# 2. Link the project in gcloud configuration
Write-Host "[*] Linking gcloud CLI to project: $ProjectID..." -ForegroundColor Cyan
try {
    & gcloud config set project $ProjectID
    Write-Host "[+] Successfully linked local configuration to GCP Project: $ProjectID" -ForegroundColor Green
} catch {
    Write-Host "[-] Failed to set project ID in gcloud config. Please verify you are logged in." -ForegroundColor Red
    Write-Host "    Run: gcloud auth login" -ForegroundColor Yellow
    Exit
}

Write-Host ""

# 3. Check authentication status
Write-Host "[*] Checking your GCP authentication status..." -ForegroundColor Cyan
$authAccount = & gcloud config get-value account 2>$null
if (-not $authAccount) {
    Write-Host "[!] You do not appear to be logged in to gcloud CLI." -ForegroundColor Yellow
    Write-Host "    Would you like to log in now? (y/n): " -NoNewline
    $loginChoice = Read-Host
    if ($loginChoice -eq 'y' -or $loginChoice -eq 'yes') {
        & gcloud auth login
        & gcloud auth application-default login
    } else {
        Write-Host "[!] Skipping authentication. Note that deployments may fail if you're not logged in." -ForegroundColor Gray
    }
} else {
    Write-Host "[+] Logged in as: $authAccount" -ForegroundColor Green
}

Write-Host ""

# 4. Enable required Google Cloud APIs
Write-Host "[*] Ensuring required APIs are enabled on '$ProjectID'..." -ForegroundColor Cyan
Write-Host "    This may take a few moments..." -ForegroundColor Gray

$services = @(
    "aiplatform.googleapis.com",
    "dialogflow.googleapis.com",
    "speech.googleapis.com",
    "texttospeech.googleapis.com",
    "run.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com"
)

foreach ($service in $services) {
    Write-Host "    Enabling $service..." -ForegroundColor Gray
    & gcloud services enable $service --quiet
}

Write-Host "[+] All required APIs are enabled!" -ForegroundColor Green
Write-Host ""

# 5. Provide next deployment instructions
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "                 GCP Link Completed Successfully!            " -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "Your codebase is now linked to: $ProjectID" -ForegroundColor Yellow
Write-Host ""
Write-Host "To deploy your Cloud Functions, run:" -ForegroundColor Gray
Write-Host "  1. Compliance Check Function:" -ForegroundColor Gray
Write-Host "     cd functions/compliance_check; gcloud functions deploy compliance_check --runtime python311 --trigger-http --allow-unauthenticated" -ForegroundColor White
Write-Host ""
Write-Host "  2. Rank Products Function:" -ForegroundColor Gray
Write-Host "     cd functions/rank_products; gcloud functions deploy rank_products --runtime python311 --trigger-http --allow-unauthenticated" -ForegroundColor White
Write-Host ""
Write-Host "  3. Product Search Function:" -ForegroundColor Gray
Write-Host "     cd functions/product_search; gcloud functions deploy product_search --runtime python311 --trigger-http --allow-unauthenticated" -ForegroundColor White
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host ""
