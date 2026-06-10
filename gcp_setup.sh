#!/bin/bash
# =============================================================================
# InsureVoice — Google Cloud Platform Project Setup Utility
# =============================================================================
# This script helps you link this local codebase with the 'voice-sales-agent'
# GCP project, configure the gcloud CLI, and enable the required services.

# Set colors for premium terminal UI
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
GRAY='\033[0;90m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

ProjectID="voice-sales-agent"
Region="us-central1"

echo -e ""
echo -e "${CYAN}=============================================================${NC}"
echo -e "${CYAN}         InsureVoice GCP Project Setup Utility               ${NC}"
echo -e "${CYAN}=============================================================${NC}"
echo -e "Linking local codebase to GCP Project: ${YELLOW}${ProjectID}${NC}"
echo -e ""

# 1. Check if gcloud CLI is installed and available
if ! command -v gcloud &> /dev/null; then
    echo -e "${YELLOW}WARNING: 'gcloud' CLI is not found in your system PATH.${NC}"
    echo -e "${GRAY}To fully link and deploy to Google Cloud, please install the Google Cloud SDK:${NC}"
    echo -e "  1. Download and install from: ${WHITE}https://cloud.google.com/sdk/docs/install${NC}"
    echo -e "  2. Run the initialization: ${WHITE}gcloud init${NC}"
    echo -e "  3. Re-run this script.${NC}"
    echo -e ""
    echo -e "${GRAY}Once 'gcloud' is installed, you can link the project manually by running:${NC}"
    echo -e "  ${WHITE}gcloud config set project ${ProjectID}${NC}"
    echo -e ""
    exit 1
fi

# 2. Link the project in gcloud configuration
echo -e "${CYAN}[*] Linking gcloud CLI to project: ${ProjectID}...${NC}"
if gcloud config set project "${ProjectID}"; then
    echo -e "${GREEN}[+] Successfully linked local configuration to GCP Project: ${ProjectID}${NC}"
else
    echo -e "${RED}[-] Failed to set project ID in gcloud config. Please verify you are logged in.${NC}"
    echo -e "    Run: ${YELLOW}gcloud auth login${NC}"
    exit 1
fi

echo -e ""

# 3. Check authentication status
echo -e "${CYAN}[*] Checking your GCP authentication status...${NC}"
authAccount=$(gcloud config get-value account 2>/dev/null)
if [ -z "$authAccount" ]; then
    echo -e "${YELLOW}[!] You do not appear to be logged in to gcloud CLI.${NC}"
    read -p "    Would you like to log in now? (y/n): " loginChoice
    if [[ "$loginChoice" =~ ^[Yy]$ || "$loginChoice" == "yes" ]]; then
        gcloud auth login
        gcloud auth application-default login
    else
        echo -e "${GRAY}[!] Skipping authentication. Note that deployments may fail if you're not logged in.${NC}"
    fi
else
    echo -e "${GREEN}[+] Logged in as: ${authAccount}${NC}"
fi

echo -e ""

# 4. Enable required Google Cloud APIs
echo -e "${CYAN}[*] Ensuring required APIs are enabled on '${ProjectID}'...${NC}"
echo -e "${GRAY}    This may take a few moments...${NC}"

services=(
    "aiplatform.googleapis.com"
    "dialogflow.googleapis.com"
    "speech.googleapis.com"
    "texttospeech.googleapis.com"
    "run.googleapis.com"
    "cloudfunctions.googleapis.com"
    "cloudbuild.googleapis.com"
)

for service in "${services[@]}"; do
    echo -e "${GRAY}    Enabling ${service}...${NC}"
    gcloud services enable "${service}" --quiet
done

echo -e "${GREEN}[+] All required APIs are enabled!${NC}"
echo -e ""

# 5. Provide next deployment instructions
echo -e "${CYAN}=============================================================${NC}"
echo -e "${GREEN}                 GCP Link Completed Successfully!            ${NC}"
echo -e "${CYAN}=============================================================${NC}"
echo -e "Your codebase is now linked to: ${YELLOW}${ProjectID}${NC}"
echo -e ""
echo -e "${GRAY}To deploy your Cloud Functions, run:${NC}"
echo -e "  1. Compliance Check Function:${NC}"
echo -e "     ${WHITE}cd functions/compliance_check; gcloud functions deploy compliance_check --runtime python311 --trigger-http --allow-unauthenticated${NC}"
echo -e ""
echo -e "  2. Rank Products Function:${NC}"
echo -e "     ${WHITE}cd functions/rank_products; gcloud functions deploy rank_products --runtime python311 --trigger-http --allow-unauthenticated${NC}"
echo -e ""
echo -e "  3. Product Search Function:${NC}"
echo -e "     ${WHITE}cd functions/product_search; gcloud functions deploy product_search --runtime python311 --trigger-http --allow-unauthenticated${NC}"
echo -e "${CYAN}=============================================================${NC}"
echo -e ""
