#!/bin/bash
# =============================================================================
# InsureVoice — GCP Weekend Cost Saver Utility
# =============================================================================
# This script helps you temporarily stop or scale down your GCP resources
# (specifically Cloud Run services / Cloud Functions) to save costs over the weekend.

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
echo -e "${CYAN}         InsureVoice GCP Weekend Cost Saver                  ${NC}"
echo -e "${CYAN}=============================================================${NC}"
echo -e "Project: ${YELLOW}${ProjectID}${NC}"
echo -e ""

# 1. Check if gcloud CLI is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${YELLOW}WARNING: 'gcloud' CLI is not found in your system PATH.${NC}"
    echo -e "${GRAY}To execute these commands, you must run this script on a machine with the Google Cloud SDK installed.${NC}"
    echo -e ""
    echo -e "${GRAY}Here are the manual commands to run on your GCP-enabled terminal:${NC}"
    echo -e ""
    echo -e "${GREEN}Option A: Scale Down Cloud Run Services to 0 (Saves all active instance costs, non-destructive)${NC}"
    echo -e "${GRAY}--------------------------------------------------------------------------------${NC}"
    echo -e "  gcloud run services update compliance_check --max-instances=0 --region=${Region} --project=${ProjectID}"
    echo -e "  gcloud run services update rank_products --max-instances=0 --region=${Region} --project=${ProjectID}"
    echo -e "  gcloud run services update product_search --max-instances=0 --region=${Region} --project=${ProjectID}"
    echo -e ""
    echo -e "${RED}Option B: Delete the Cloud Run Services (Fully destructive, easily redeployed via Cloud Build)${NC}"
    echo -e "${GRAY}--------------------------------------------------------------------------------${NC}"
    echo -e "  gcloud run services delete compliance_check --region=${Region} --project=${ProjectID} --quiet"
    echo -e "  gcloud run services delete rank_products --region=${Region} --project=${ProjectID} --quiet"
    echo -e "  gcloud run services delete product_search --region=${Region} --project=${ProjectID} --quiet"
    echo -e ""
    echo -e "${YELLOW}Option C: Temporarily Disable Cloud Run & Cloud Functions APIs (Global stop)${NC}"
    echo -e "${GRAY}--------------------------------------------------------------------------------${NC}"
    echo -e "  gcloud services disable run.googleapis.com cloudfunctions.googleapis.com --project=${ProjectID}"
    echo -e ""
    exit 1
fi

# 2. Select action
echo -e "${WHITE}Please select a cost-saving action for project '${ProjectID}':${NC}"
echo -e "  [1] Scale Down to 0 (Recommended): Scale all Cloud Run services to max-instances=0 (stops instances, non-destructive)."
echo -e "  [2] Delete Services: Fully remove all three deployed Cloud Run services (redeployable later)."
echo -e "  [3] Disable APIs: Disable Cloud Run and Cloud Functions APIs globally (stops all usage and costs)."
echo -e "  [4] Exit"
echo -e ""
read -p "Enter option (1-4): " choice

if [ "$choice" == "1" ]; then
    echo -e ""
    echo -e "${CYAN}[*] Scaling down Cloud Run services to 0 max-instances...${NC}"
    services=("compliance_check" "rank_products" "product_search")
    for service in "${services[@]}"; do
        echo -e "${GRAY}    Updating ${service} in ${Region}...${NC}"
        gcloud run services update "${service}" --max-instances=0 --region="${Region}" --project="${ProjectID}" --quiet &>/dev/null
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}    [+] Successfully scaled down ${service}${NC}"
        else
            echo -e "${YELLOW}    [-] Service ${service} not found or failed to update (may not be deployed yet).${NC}"
        fi
    done
    echo -e ""
    echo -e "${GREEN}[+] All active Cloud Run costs have been paused. To restore them on Monday, deploy them again or set max-instances back to normal (e.g. 10).${NC}"
elif [ "$choice" == "2" ]; then
    echo -e ""
    echo -e "${RED}[!] WARNING: This will delete all three Cloud Run services. Are you sure? (y/n): ${NC}"
    read -p "" confirm
    if [[ "$confirm" =~ ^[Yy]$ || "$confirm" == "yes" ]]; then
        services=("compliance_check" "rank_products" "product_search")
        for service in "${services[@]}"; do
            echo -e "${GRAY}    Deleting ${service} in ${Region}...${NC}"
            gcloud run services delete "${service}" --region="${Region}" --project="${ProjectID}" --quiet &>/dev/null
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}    [+] Successfully deleted ${service}${NC}"
            else
                echo -e "${YELLOW}    [-] Service ${service} not found or already deleted.${NC}"
            fi
        done
        echo -e ""
        echo -e "${GREEN}[+] All services deleted. You can redeploy them on Monday using your Cloud Build pipeline or the gcp_setup script.${NC}"
    else
        echo -e "${GRAY}[*] Cancelled.${NC}"
    fi
elif [ "$choice" == "3" ]; then
    echo -e ""
    echo -e "${RED}[!] WARNING: Disabling APIs will stop all related operations and might affect configuration. Are you sure? (y/n): ${NC}"
    read -p "" confirm
    if [[ "$confirm" =~ ^[Yy]$ || "$confirm" == "yes" ]]; then
        echo -e "${CYAN}[*] Disabling Cloud Run API...${NC}"
        gcloud services disable run.googleapis.com --project="${ProjectID}" --quiet
        echo -e "${CYAN}[*] Disabling Cloud Functions API...${NC}"
        gcloud services disable cloudfunctions.googleapis.com --project="${ProjectID}" --quiet
        echo -e "${GREEN}[+] APIs disabled successfully! Remember to enable them on Monday.${NC}"
    else
        echo -e "${GRAY}[*] Cancelled.${NC}"
    fi
else
    echo -e "${GRAY}[*] Exiting script.${NC}"
fi
echo -e ""
