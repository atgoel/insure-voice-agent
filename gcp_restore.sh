#!/bin/bash
# =============================================================================
# InsureVoice — GCP Restore Utility
# =============================================================================
# This script helps you restore your Cloud Run services by resetting
# max-instances back to 10 (or normal active development limits).

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
echo -e "${CYAN}         InsureVoice GCP Resource Restore Utility            ${NC}"
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
    echo -e "${GREEN}Restore Cloud Run Services (Scale max-instances up to 10):${NC}"
    echo -e "${GRAY}--------------------------------------------------------------------------------${NC}"
    echo -e "  gcloud run services update compliance_check --max-instances=10 --region=${Region} --project=${ProjectID}"
    echo -e "  gcloud run services update rank_products --max-instances=10 --region=${Region} --project=${ProjectID}"
    echo -e "  gcloud run services update product_search --max-instances=10 --region=${Region} --project=${ProjectID}"
    echo -e ""
    exit 1
fi

# 2. Confirm restore
echo -e "This will restore and scale up all three Cloud Run services to max-instances=10 in project '${ProjectID}'."
read -p "Proceed? (y/n): " confirm

if [[ "$confirm" =~ ^[Yy]$ || "$confirm" == "yes" ]]; then
    echo -e ""
    echo -e "${CYAN}[*] Restoring Cloud Run services...${NC}"
    services=("compliance_check" "rank_products" "product_search")
    for service in "${services[@]}"; do
        echo -e "${GRAY}    Updating ${service} in ${Region}...${NC}"
        gcloud run services update "${service}" --max-instances=10 --region="${Region}" --project="${ProjectID}" --quiet &>/dev/null
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}    [+] Successfully restored ${service} (max-instances=10)${NC}"
        else
            echo -e "${YELLOW}    [-] Service ${service} not found or failed to update (may need redeployment).${NC}"
        fi
    done
    echo -e ""
    echo -e "${GREEN}[+] All services have been successfully brought back up and are ready for end-to-end testing!${NC}"
else
    echo -e "${GRAY}[*] Cancelled.${NC}"
fi
echo -e ""
