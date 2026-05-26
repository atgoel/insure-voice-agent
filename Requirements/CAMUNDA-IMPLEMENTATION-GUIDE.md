# Camunda Platform 8 - Implementation Guide for Infinity
## Step-by-Step Setup & Integration with .NET Microservices

**Document Version:** 1.0  
**Date:** 2026-02-28  
**Target Platform:** Infinity Life Insurance Platform  
**Camunda Version:** Platform 8 (Zeebe)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local Development Setup](#2-local-development-setup)
3. [Architecture Overview](#3-architecture-overview)
4. [Setup Options Comparison](#4-setup-options-comparison)
5. [Option A: Quick Start with Camunda Cloud SaaS](#5-option-a-quick-start-with-camunda-cloud-saas)
6. [Option B: Self-Hosted on Azure (Docker Compose)](#6-option-b-self-hosted-on-azure-docker-compose)
7. [Option C: Production-Ready Azure Kubernetes (AKS)](#7-option-c-production-ready-azure-kubernetes-aks)
8. [.NET Worker Implementation](#8-net-worker-implementation)
9. [Sample BPMN Workflows for Infinity](#9-sample-bpmn-workflows-for-infinity)
10. [Integration with Existing Microservices](#10-integration-with-existing-microservices)
11. [Testing & Debugging](#11-testing--debugging)
12. [Monitoring & Operations](#12-monitoring--operations)
13. [Migration Strategy](#13-migration-strategy)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

### Software Requirements

```powershell
# Check prerequisites
node --version        # Should be v16+ (for Camunda Modeler)
dotnet --version      # Should be .NET 7.0 or 8.0
docker --version      # Should be 20.10+
docker-compose --version  # Should be 1.29+
kubectl version       # For AKS deployment
az --version          # Azure CLI for cloud deployment
```

### Downloads Needed

| Tool | Purpose | Download URL |
|------|---------|--------------|
| **Camunda Modeler** | Visual BPMN editor | https://camunda.com/download/modeler/ |
| **Camunda Desktop** | Local test environment | https://camunda.com/download/camunda-platform-8/ |
| **Zeebe .NET Client** | .NET integration library | `dotnet add package Zeebe.Client` |

### Azure Resources (for self-hosted)

- Azure Kubernetes Service (AKS) cluster
- Azure SQL Database (for Camunda Operate/Tasklist)
- Azure Redis Cache (optional, for session management)
- Azure Container Registry (ACR) for custom worker images
- Azure Application Gateway (for ingress)

---

## 2. Local Development Setup

### Step 1: Install Camunda Desktop (Fastest Way to Start)

```powershell
# Download from: https://camunda.com/download/camunda-platform-8/
# Extract and run:
cd ~/Downloads/camunda-desktop
./camunda-desktop.exe

# This includes:
# - Zeebe broker (port 26500)
# - Zeebe Gateway (gRPC port 26500, REST port 8080)
# - Operate (UI at http://localhost:8081)
# - Tasklist (UI at http://localhost:8082)
# - Elasticsearch (for Operate/Tasklist storage)
```

**Benefits:**
- ✅ No Docker needed
- ✅ All components pre-configured
- ✅ Perfect for learning/prototyping
- ✅ Self-contained (stops when you close it)

---

### Step 2: Alternative - Docker Compose (More Control)

Create `docker-compose.yml` in your project root:

```yaml
version: "3.8"

services:
  # Core workflow engine
  zeebe:
    image: camunda/zeebe:8.4.3
    container_name: zeebe_broker
    ports:
      - "26500:26500"  # gRPC gateway
      - "9600:9600"    # Monitoring
      - "8080:8080"    # REST API
    environment:
      - ZEEBE_BROKER_GATEWAY_ENABLE=true
      - ZEEBE_BROKER_GATEWAY_NETWORK_HOST=0.0.0.0
      - ZEEBE_BROKER_GATEWAY_NETWORK_PORT=26500
      - ZEEBE_BROKER_EXPORTERS_ELASTICSEARCH_CLASSNAME=io.camunda.zeebe.exporter.ElasticsearchExporter
      - ZEEBE_BROKER_EXPORTERS_ELASTICSEARCH_ARGS_URL=http://elasticsearch:9200
      - ZEEBE_BROKER_EXPORTERS_ELASTICSEARCH_ARGS_BULK_SIZE=1
    volumes:
      - zeebe_data:/usr/local/zeebe/data
    depends_on:
      - elasticsearch
    networks:
      - camunda-platform

  # Elasticsearch for Operate/Tasklist data
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.9.0
    container_name: elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
    ports:
      - "9200:9200"
    volumes:
      - elastic_data:/usr/share/elasticsearch/data
    networks:
      - camunda-platform

  # Operate - Process monitoring UI
  operate:
    image: camunda/operate:8.4.3
    container_name: operate
    ports:
      - "8081:8080"
    environment:
      - CAMUNDA_OPERATE_ZEEBE_GATEWAYADDRESS=zeebe:26500
      - CAMUNDA_OPERATE_ELASTICSEARCH_URL=http://elasticsearch:9200
      - CAMUNDA_OPERATE_ZEEBEELASTICSEARCH_URL=http://elasticsearch:9200
      - SERVER_PORT=8080
    depends_on:
      - zeebe
      - elasticsearch
    networks:
      - camunda-platform

  # Tasklist - Human task management UI
  tasklist:
    image: camunda/tasklist:8.4.3
    container_name: tasklist
    ports:
      - "8082:8080"
    environment:
      - CAMUNDA_TASKLIST_ZEEBE_GATEWAYADDRESS=zeebe:26500
      - CAMUNDA_TASKLIST_ELASTICSEARCH_URL=http://elasticsearch:9200
      - CAMUNDA_TASKLIST_ZEEBEELASTICSEARCH_URL=http://elasticsearch:9200
      - SERVER_PORT=8080
    depends_on:
      - zeebe
      - elasticsearch
    networks:
      - camunda-platform

  # Connectors (optional) - For external integrations (REST, Email, etc.)
  connectors:
    image: camunda/connectors:8.4.3
    container_name: connectors
    ports:
      - "8085:8080"
    environment:
      - ZEEBE_CLIENT_BROKER_GATEWAY-ADDRESS=zeebe:26500
      - ZEEBE_CLIENT_SECURITY_PLAINTEXT=true
    depends_on:
      - zeebe
    networks:
      - camunda-platform

volumes:
  zeebe_data:
  elastic_data:

networks:
  camunda-platform:
    driver: bridge
```

**Start the stack:**

```powershell
# Start all services
docker-compose up -d

# Verify all containers are running
docker-compose ps

# View Zeebe broker logs
docker-compose logs -f zeebe

# Access the UIs:
# - Operate: http://localhost:8081 (default: demo/demo)
# - Tasklist: http://localhost:8082 (default: demo/demo)
# - Zeebe gRPC: localhost:26500
```

---

## 3. Architecture Overview

### Camunda Platform 8 Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CAMUNDA PLATFORM 8 ARCHITECTURE                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐                                                   │
│  │   Business   │  BPMN Models                                      │
│  │   Analysts   ├──────────────┐                                    │
│  └──────────────┘              │                                    │
│                                ▼                                    │
│  ┌─────────────────────────────────────────┐                        │
│  │      Camunda Modeler (Desktop)          │                        │
│  │  - Visual BPMN 2.0 Editor               │                        │
│  │  - Model validation                     │                        │
│  │  - Deploy to Zeebe                      │                        │
│  └────────────────┬────────────────────────┘                        │
│                   │ Deploy BPMN                                     │
│                   ▼                                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Zeebe Broker (Workflow Engine)                 │   │
│  │  - Process orchestration                                   │   │
│  │  - State management (millions of instances)                │   │
│  │  - Event streaming (Kafka protocol)                        │   │
│  │  - Horizontal scaling                                      │   │
│  └──────┬────────────────────────┬─────────────────────────────┘   │
│         │                        │                                 │
│         │ Jobs                   │ Events                          │
│         ▼                        ▼                                 │
│  ┌──────────────┐         ┌─────────────────┐                      │
│  │ .NET Workers │         │ Elasticsearch   │                      │
│  │              │         │ (Event Storage) │                      │
│  │ - BIService  │         └────────┬────────┘                      │
│  │ - ImageQC    │                  │                               │
│  │ - PrintQC    │                  │ Query                         │
│  │ - Payment    │                  ▼                               │
│  └──────────────┘         ┌─────────────────────────────────────┐  │
│                           │  Camunda Operate (Monitoring)       │  │
│  ┌──────────────┐         │  - Process instance view            │  │
│  │   QC Team    ├────────▶│  - Incident management              │  │
│  │   Approvers  │         │  - Process history                  │  │
│  └──────────────┘         └─────────────────────────────────────┘  │
│         │                                                           │
│         │ Approve/Reject                                            │
│         ▼                                                           │
│  ┌─────────────────────────────────────────┐                        │
│  │  Camunda Tasklist (Human Tasks)         │                        │
│  │  - Task inbox for users                 │                        │
│  │  - Forms rendering                      │                        │
│  │  - Task assignment/delegation           │                        │
│  └─────────────────────────────────────────┘                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### How It Integrates with Infinity

```
┌──────────────────────────────────────────────────────────────────────┐
│                    INFINITY + CAMUNDA INTEGRATION                    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Frontend (React)                                                    │
│  ┌────────────────┐                                                  │
│  │ ProposalForm   │──┐                                               │
│  │    Submit      │  │                                               │
│  └────────────────┘  │ 1. POST /api/applications                    │
│                      ▼                                               │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │         ProposalForm Service (Existing .NET API)             │   │
│  │  - Save application to SQL                                   │   │
│  │  - NEW: Start Camunda workflow                               │   │
│  └───────────────┬──────────────────────────────────────────────┘   │
│                  │                                                   │
│                  │ 2. StartProcessInstance("sales-journey")          │
│                  │    with variables: { applicationId: 12345 }       │
│                  ▼                                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Zeebe Broker                              │   │
│  │  [Start] → [DeDupe Check] → [ImageQC] → [PrintQC] → [End]   │   │
│  └───────┬────────────────┬────────────────┬──────────────────┘    │
│          │                │                │                        │
│          │ 3. Job         │ 4. Job         │ 5. Job                 │
│          ▼                ▼                ▼                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ DeDupe      │  │ ImageQC      │  │ PrintQC      │               │
│  │ Worker      │  │ Worker       │  │ Worker       │               │
│  │ (.NET)      │  │ (.NET)       │  │ (.NET)       │               │
│  └─────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│        │                 │                 │                        │
│        │ 6. Call         │ 7. Call         │ 8. Call                │
│        ▼                 ▼                 ▼                        │
│  ┌────────────────┐ ┌───────────────┐ ┌───────────────┐            │
│  │ MasterService  │ │ Processing    │ │ PrintQC       │            │
│  │ (DeDupe API)   │ │ Hub Service   │ │ Service       │            │
│  └────────────────┘ └───────────────┘ └───────────────┘            │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Key Points:**
1. **Existing APIs remain unchanged** - They become "workers" invoked by Camunda
2. **Process logic moves to BPMN** - No more hardcoded stepper logic
3. **State managed by Zeebe** - Your services become stateless
4. **Human tasks** - QC approvals handled by Camunda Tasklist

---

## 4. Setup Options Comparison

| Option | Best For | Time to Setup | Monthly Cost | Complexity |
|--------|----------|---------------|--------------|------------|
| **Camunda Desktop** | Local development, learning | 5 minutes | $0 | ⭐ |
| **Docker Compose** | Team development, testing | 15 minutes | $0 | ⭐⭐ |
| **Camunda Cloud SaaS** | Quick production, no ops | 10 minutes | $500-$2000 | ⭐ |
| **Azure AKS (Self-hosted)** | Full control, production | 2-3 days | $650 | ⭐⭐⭐⭐ |

**Recommended Path:**
1. **Week 1-2:** Start with **Docker Compose** for POC
2. **Week 3-4:** If successful, move to **Camunda Cloud SaaS** for pilot
3. **Month 2-3:** Evaluate self-hosted AKS if cost/control becomes priority

---

## 5. Option A: Quick Start with Camunda Cloud SaaS

### Step 1: Sign Up (Free Trial)

1. Go to https://camunda.com/get-started/
2. Click "Try Camunda Cloud Free"
3. Create account (no credit card required for 30-day trial)
4. Create a cluster (takes ~2 minutes to provision)

### Step 2: Get Connection Credentials

```powershell
# In Camunda Cloud Console:
# 1. Go to your cluster
# 2. Click "API" tab
# 3. Create a new client
# 4. Save these values:

# ZEEBE_ADDRESS=xxx-yyy-zzz.bru-2.zeebe.camunda.io:443
# ZEEBE_CLIENT_ID=xxx
# ZEEBE_CLIENT_SECRET=xxx
# ZEEBE_AUTHORIZATION_SERVER_URL=https://login.cloud.camunda.io/oauth/token
```

### Step 3: Configure .NET Worker

```bash
# Add to your appsettings.json or Azure Key Vault
{
  "Camunda": {
    "Cloud": {
      "ClusterId": "xxx-yyy-zzz",
      "ClientId": "xxxx",
      "ClientSecret": "xxxxx",
      "Region": "bru-2"
    }
  }
}
```

**Benefits:**
- ✅ No infrastructure management
- ✅ Auto-scaling
- ✅ Built-in monitoring
- ✅ Automatic backups
- ✅ 99.9% SLA

**Drawbacks:**
- ⚠️ Recurring subscription ($500-$2000/month depending on volume)
- ⚠️ Data stored in Camunda's cloud (check compliance requirements)

---

## 6. Option B: Self-Hosted on Azure (Docker Compose)

This option runs Camunda on an Azure VM using Docker Compose - good for pilot/staging environments.

### Step 1: Create Azure VM

```powershell
# Azure CLI commands
az login

# Create resource group
az group create --name rg-infinity-camunda-dev --location eastus

# Create VM (Ubuntu 22.04 with Docker pre-installed)
az vm create \
  --resource-group rg-infinity-camunda-dev \
  --name vm-camunda-dev \
  --image Ubuntu2204 \
  --size Standard_D4s_v3 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard

# Install Docker and Docker Compose
az vm run-command invoke \
  --resource-group rg-infinity-camunda-dev \
  --name vm-camunda-dev \
  --command-id RunShellScript \
  --scripts @install-docker.sh

# Open ports for Camunda components
az vm open-port --port 26500 --resource-group rg-infinity-camunda-dev --name vm-camunda-dev --priority 900  # Zeebe gRPC
az vm open-port --port 8081 --resource-group rg-infinity-camunda-dev --name vm-camunda-dev --priority 901   # Operate
az vm open-port --port 8082 --resource-group rg-infinity-camunda-dev --name vm-camunda-dev --priority 902   # Tasklist
```

**install-docker.sh:**
```bash
#!/bin/bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Add current user to docker group
sudo usermod -aG docker $USER
```

### Step 2: Deploy Docker Compose

```powershell
# SSH into the VM
az ssh vm --resource-group rg-infinity-camunda-dev --name vm-camunda-dev

# Upload docker-compose.yml (from section 2) to the VM
# scp docker-compose.yml azureuser@<VM_IP>:~/

# Start Camunda
docker-compose up -d

# Verify all services are running
docker-compose ps

# Check logs
docker-compose logs -f zeebe
```

### Step 3: Access UIs

```
# Get VM public IP
az vm show --resource-group rg-infinity-camunda-dev --name vm-camunda-dev --show-details --query publicIps -o tsv

# Access:
# - Operate: http://<VM_IP>:8081
# - Tasklist: http://<VM_IP>:8082
# - Zeebe gRPC: <VM_IP>:26500
```

**Estimated Monthly Cost:**
- VM (Standard_D4s_v3): ~$120/month
- Storage (128GB SSD): ~$20/month
- **Total: ~$140/month**

---

## 7. Option C: Production-Ready Azure Kubernetes (AKS)

For production workloads requiring high availability and scalability.

### Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    AZURE KUBERNETES SERVICE                    │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         Application Gateway (Ingress)                    │  │
│  │  - SSL termination                                       │  │
│  │  - /operate → Operate service                            │  │
│  │  - /tasklist → Tasklist service                          │  │
│  └────────────────┬─────────────────────────────────────────┘  │
│                   │                                            │
│   ┌───────────────┴────────────────────┐                       │
│   │                                    │                       │
│   ▼                                    ▼                       │
│  ┌──────────────┐              ┌──────────────┐               │
│  │   Operate    │              │  Tasklist    │               │
│  │   (3 pods)   │              │  (3 pods)    │               │
│  └──────┬───────┘              └──────┬───────┘               │
│         │                             │                       │
│         └──────────────┬──────────────┘                       │
│                        │ Query                                │
│                        ▼                                      │
│              ┌──────────────────┐                             │
│              │  Elasticsearch   │                             │
│              │   (3 nodes)      │                             │
│              └──────────────────┘                             │
│                        ▲                                      │
│                        │ Export events                        │
│              ┌─────────┴──────────┐                           │
│              │                    │                           │
│       ┌──────▼──────┐      ┌──────▼──────┐                   │
│       │   Zeebe     │◀────▶│   Zeebe     │                   │
│       │ (Broker 1)  │      │ (Broker 2)  │                   │
│       └─────────────┘      └─────────────┘                   │
│              ▲                                                │
│              │ gRPC (Job polling)                             │
│       ┌──────┴──────────────────────┐                         │
│       │                             │                         │
│  ┌────▼────────┐              ┌─────▼───────┐                │
│  │ DeDupe      │              │  ImageQC    │                │
│  │ Worker Pod  │              │  Worker Pod │                │
│  └─────────────┘              └─────────────┘                │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### Step 1: Create AKS Cluster

```powershell
# Create AKS cluster
az aks create \
  --resource-group rg-infinity-camunda-prod \
  --name aks-camunda-prod \
  --node-count 3 \
  --node-vm-size Standard_D4s_v3 \
  --enable-managed-identity \
  --generate-ssh-keys \
  --network-plugin azure \
  --enable-addons monitoring

# Get kubectl credentials
az aks get-credentials --resource-group rg-infinity-camunda-prod --name aks-camunda-prod

# Verify connection
kubectl get nodes
```

### Step 2: Install Camunda using Helm

```powershell
# Add Camunda Helm repo
helm repo add camunda https://helm.camunda.io
helm repo update

# Create namespace
kubectl create namespace camunda

# Install Camunda Platform 8
helm install camunda-platform camunda/camunda-platform \
  --namespace camunda \
  --set global.image.tag=8.4.3 \
  --set zeebe.clusterSize=2 \
  --set zeebe.partitionCount=2 \
  --set zeebe.replicationFactor=2 \
  --set elasticsearch.replicas=3 \
  --set operate.replicas=2 \
  --set tasklist.replicas=2

# Wait for all pods to be ready
kubectl -n camunda get pods -w

# Check status
kubectl -n camunda get all
```

### Step 3: Configure Ingress

```yaml
# camunda-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: camunda-ingress
  namespace: camunda
  annotations:
    kubernetes.io/ingress.class: azure/application-gateway
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - camunda.yourdomain.com
    secretName: camunda-tls
  rules:
  - host: camunda.yourdomain.com
    http:
      paths:
      - path: /operate
        pathType: Prefix
        backend:
          service:
            name: camunda-platform-operate
            port:
              number: 80
      - path: /tasklist
        pathType: Prefix
        backend:
          service:
            name: camunda-platform-tasklist
            port:
              number: 80
```

```powershell
# Apply ingress
kubectl apply -f camunda-ingress.yaml

# Get ingress IP
kubectl -n camunda get ingress camunda-ingress
```

### Step 4: Configure High Availability

```yaml
# camunda-values.yaml (Custom Helm values for production)
global:
  image:
    tag: 8.4.3

zeebe:
  clusterSize: 3  # 3 Zeebe brokers for HA
  partitionCount: 6  # More partitions = better parallelism
  replicationFactor: 3  # Each partition replicated 3 times
  resources:
    requests:
      cpu: 1000m
      memory: 2Gi
    limits:
      cpu: 2000m
      memory: 4Gi
  
  # Persistent storage
  persistence:
    enabled: true
    size: 128Gi
    storageClassName: managed-premium

elasticsearch:
  replicas: 3  # 3-node ES cluster
  resources:
    requests:
      cpu: 500m
      memory: 2Gi
    limits:
      cpu: 1000m
      memory: 4Gi
  persistence:
    enabled: true
    size: 100Gi

operate:
  replicas: 2  # Load-balanced Operate instances
  resources:
    requests:
      cpu: 500m
      memory: 1Gi

tasklist:
  replicas: 2
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
```

```powershell
# Upgrade with custom values
helm upgrade camunda-platform camunda/camunda-platform \
  --namespace camunda \
  --values camunda-values.yaml
```

**Estimated Monthly Cost (Production):**
- AKS cluster (3 x D4s_v3 nodes): $360/month
- Premium SSD (128GB x 3): $100/month
- Application Gateway: $125/month
- Monitoring: $50/month
- **Total: ~$635/month**

---

## 8. .NET Worker Implementation

Workers are .NET services that execute tasks defined in BPMN workflows. They poll Zeebe for jobs and execute business logic.

### Step 1: Install NuGet Package

```powershell
# In your microservice project (e.g., Processing Hub)
cd Backend/FGLI-MS-ProcessingHubService-main
dotnet add package Zeebe.Client --version 8.4.3
```

### Step 2: Create Worker Base Class

**Infrastructure/Camunda/ZeebeWorkerBase.cs:**

```csharp
using Zeebe.Client;
using Zeebe.Client.Api.Responses;
using Zeebe.Client.Api.Worker;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace FGLI.ProcessingHub.Infrastructure.Camunda;

public abstract class ZeebeWorkerBase
{
    private readonly IZeebeClient _client;
    private readonly ILogger _logger;
    private IJobWorker? _worker;

    protected ZeebeWorkerBase(IZeebeClient client, ILogger logger)
    {
        _client = client ?? throw new ArgumentNullException(nameof(client));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    /// <summary>
    /// Job type name - must match BPMN task's "Type" attribute
    /// Example: "dedupe-check", "image-qc-approval", etc.
    /// </summary>
    protected abstract string JobType { get; }

    /// <summary>
    /// Business logic executed for each job
    /// </summary>
    protected abstract Task<Dictionary<string, object>> ExecuteAsync(
        IJob job, 
        Dictionary<string, object> variables, 
        CancellationToken cancellationToken);

    /// <summary>
    /// Starts the worker (called from Hosted Service)
    /// </summary>
    public void StartWorker()
    {
        _worker = _client.NewWorker()
            .JobType(JobType)
            .Handler(HandleJobAsync)
            .MaxJobsActive(10)  // Process up to 10 jobs concurrently
            .Timeout(TimeSpan.FromMinutes(5))  // Job timeout
            .PollInterval(TimeSpan.FromSeconds(1))
            .Name($"{JobType}-worker")
            .Open();

        _logger.LogInformation("Zeebe worker '{JobType}' started", JobType);
    }

    /// <summary>
    /// Stops the worker gracefully
    /// </summary>
    public void StopWorker()
    {
        _worker?.Dispose();
        _logger.LogInformation("Zeebe worker '{JobType}' stopped", JobType);
    }

    private async Task HandleJobAsync(IJobClient jobClient, IJob job)
    {
        var correlationId = Guid.NewGuid().ToString();
        _logger.LogInformation(
            "[{CorrelationId}] Received job {JobKey} for process instance {ProcessInstanceKey}",
            correlationId, job.Key, job.ProcessInstanceKey);

        try
        {
            // Deserialize variables
            var variables = JsonSerializer.Deserialize<Dictionary<string, object>>(job.Variables) 
                ?? new Dictionary<string, object>();

            _logger.LogDebug("[{CorrelationId}] Job variables: {Variables}", 
                correlationId, JsonSerializer.Serialize(variables));

            // Execute business logic
            var outputVariables = await ExecuteAsync(job, variables, CancellationToken.None);

            // Complete the job
            await jobClient.NewCompleteJobCommand(job.Key)
                .Variables(outputVariables)
                .Send();

            _logger.LogInformation(
                "[{CorrelationId}] Job {JobKey} completed successfully",
                correlationId, job.Key);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, 
                "[{CorrelationId}] Job {JobKey} failed: {ErrorMessage}",
                correlationId, job.Key, ex.Message);

            // Fail the job (Zeebe will retry based on BPMN config)
            await jobClient.NewFailCommand(job.Key)
                .Retries(job.Retries - 1)
                .ErrorMessage(ex.Message)
                .Send();
        }
    }
}
```

### Step 3: Implement Specific Workers

**Workers/DedupeCheckWorker.cs:**

```csharp
using FGLI.ProcessingHub.Application.Services;
using FGLI.ProcessingHub.Infrastructure.Camunda;
using Microsoft.Extensions.Logging;
using Zeebe.Client;
using Zeebe.Client.Api.Responses;

namespace FGLI.ProcessingHub.Workers;

/// <summary>
/// Worker that checks for duplicate applications
/// Invoked by Camunda when service task "dedupe-check" is activated
/// </summary>
public class DedupeCheckWorker : ZeebeWorkerBase
{
    private readonly IDedupeService _dedupeService;
    private readonly ILogger<DedupeCheckWorker> _logger;

    public DedupeCheckWorker(
        IZeebeClient client, 
        IDedupeService dedupeService, 
        ILogger<DedupeCheckWorker> logger)
        : base(client, logger)
    {
        _dedupeService = dedupeService ?? throw new ArgumentNullException(nameof(dedupeService));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    protected override string JobType => "dedupe-check";

    protected override async Task<Dictionary<string, object>> ExecuteAsync(
        IJob job, 
        Dictionary<string, object> variables, 
        CancellationToken cancellationToken)
    {
        // Extract application ID from process variables
        if (!variables.TryGetValue("applicationId", out var appIdObj) || appIdObj == null)
        {
            throw new InvalidOperationException("Missing required variable: applicationId");
        }

        var applicationId = Convert.ToInt32(appIdObj.ToString());
        _logger.LogInformation("Running dedupe check for application {ApplicationId}", applicationId);

        // Call existing DeDupe API
        var dedupeResult = await _dedupeService.CheckForDuplicatesAsync(applicationId, cancellationToken);

        // Return output variables (will be merged into process instance)
        return new Dictionary<string, object>
        {
            { "isDuplicate", dedupeResult.IsDuplicate },
            { "duplicateApplicationIds", dedupeResult.DuplicateIds },
            { "dedupeCheckCompletedAt", DateTime.UtcNow.ToString("O") },
            { "dedupeStatus", dedupeResult.IsDuplicate ? "DUPLICATE_FOUND" : "NO_DUPLICATE" }
        };
    }
}
```

**Workers/ImageQCWorker.cs:**

```csharp
using FGLI.ProcessingHub.Application.Services;
using FGLI.ProcessingHub.Infrastructure.Camunda;
using Microsoft.Extensions.Logging;
using Zeebe.Client;
using Zeebe.Client.Api.Responses;

namespace FGLI.ProcessingHub.Workers;

/// <summary>
/// Worker that processes ImageQC approval results
/// This is invoked AFTER a QC user approves/rejects in Camunda Tasklist
/// </summary>
public class ImageQCWorker : ZeebeWorkerBase
{
    private readonly IImageQCService _imageQCService;
    private readonly ILogger<ImageQCWorker> _logger;

    public ImageQCWorker(
        IZeebeClient client, 
        IImageQCService imageQCService, 
        ILogger<ImageQCWorker> logger)
        : base(client, logger)
    {
        _imageQCService = imageQCService ?? throw new ArgumentNullException(nameof(imageQCService));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    protected override string JobType => "image-qc-process";

    protected override async Task<Dictionary<string, object>> ExecuteAsync(
        IJob job, 
        Dictionary<string, object> variables, 
        CancellationToken cancellationToken)
    {
        var applicationId = Convert.ToInt32(variables["applicationId"].ToString());
        
        // These variables are set by the QC user in Tasklist
        var qcApproved = Convert.ToBoolean(variables["imageQCApproved"]);
        var qcComments = variables.TryGetValue("imageQCComments", out var comments) 
            ? comments?.ToString() 
            : null;
        var qcUserId = variables["imageQCUserId"]?.ToString();

        _logger.LogInformation(
            "Processing ImageQC result for application {ApplicationId}: Approved={Approved}, User={User}",
            applicationId, qcApproved, qcUserId);

        // Update application status in your database
        await _imageQCService.SaveQCResultAsync(
            applicationId, 
            qcApproved, 
            qcComments, 
            qcUserId, 
            cancellationToken);

        // Return variables (used for routing in BPMN - e.g., exclusive gateway)
        return new Dictionary<string, object>
        {
            { "imageQCApproved", qcApproved },
            { "imageQCCompletedAt", DateTime.UtcNow.ToString("O") },
            { "imageQCComments", qcComments ?? string.Empty }
        };
    }
}
```

### Step 4: Register Workers with DI

**Program.cs (or Startup.cs):**

```csharp
using Zeebe.Client;
using Zeebe.Client.Impl.Builder;
using FGLI.ProcessingHub.Workers;
using FGLI.ProcessingHub.Infrastructure.Camunda;

var builder = WebApplication.CreateBuilder(args);

// Register Zeebe client
builder.Services.AddSingleton<IZeebeClient>(sp =>
{
    var config = builder.Configuration.GetSection("Camunda");
    
    // Option 1: Local/Docker Compose
    if (config.GetValue<bool>("UseLocalBroker"))
    {
        return CamundaCloudClientBuilder
            .Builder()
            .UseGatewayAddress("localhost:26500")
            .UsePlainText()  // No TLS for local dev
            .Build();
    }
    
    // Option 2: Camunda Cloud SaaS
    var clusterId = config["Cloud:ClusterId"];
    var clientId = config["Cloud:ClientId"];
    var clientSecret = config["Cloud:ClientSecret"];
    var region = config["Cloud:Region"];
    
    return CamundaCloudClientBuilder
        .Builder()
        .UseClientId(clientId)
        .UseClientSecret(clientSecret)
        .UseContactPoint($"{clusterId}.{region}.zeebe.camunda.io:443")
        .Build();
});

// Register workers as singletons
builder.Services.AddSingleton<DedupeCheckWorker>();
builder.Services.AddSingleton<ImageQCWorker>();
// ... register other workers

// Register hosted service to start workers
builder.Services.AddHostedService<CamundaWorkerHostedService>();

var app = builder.Build();
app.Run();
```

**Infrastructure/Camunda/CamundaWorkerHostedService.cs:**

```csharp
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using FGLI.ProcessingHub.Workers;

namespace FGLI.ProcessingHub.Infrastructure.Camunda;

/// <summary>
/// Background service that starts/stops Camunda workers with application lifecycle
/// </summary>
public class CamundaWorkerHostedService : IHostedService
{
    private readonly DedupeCheckWorker _dedupeWorker;
    private readonly ImageQCWorker _imageQCWorker;
    // ... inject other workers
    private readonly ILogger<CamundaWorkerHostedService> _logger;

    public CamundaWorkerHostedService(
        DedupeCheckWorker dedupeWorker,
        ImageQCWorker imageQCWorker,
        ILogger<CamundaWorkerHostedService> logger)
    {
        _dedupeWorker = dedupeWorker;
        _imageQCWorker = imageQCWorker;
        _logger = logger;
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _logger.LogInformation("Starting Camunda workers...");
        
        _dedupeWorker.StartWorker();
        _imageQCWorker.StartWorker();
        // ... start other workers
        
        _logger.LogInformation("All Camunda workers started successfully");
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        _logger.LogInformation("Stopping Camunda workers...");
        
        _dedupeWorker.StopWorker();
        _imageQCWorker.StopWorker();
        // ... stop other workers
        
        _logger.LogInformation("All Camunda workers stopped");
        return Task.CompletedTask;
    }
}
```

### Step 5: Configuration

**appsettings.json:**

```json
{
  "Camunda": {
    "UseLocalBroker": true,  // Set to false for Camunda Cloud
    "Cloud": {
      "ClusterId": "xxx-yyy-zzz",
      "ClientId": "your-client-id",
      "ClientSecret": "your-client-secret",
      "Region": "bru-2"
    }
  }
}
```

**appsettings.Production.json (use Azure Key Vault):**

```json
{
  "Camunda": {
    "UseLocalBroker": false,
    "Cloud": {
      "ClusterId": "@Microsoft.KeyVault(SecretUri=https://your-vault.vault.azure.net/secrets/CamundaClusterId/)",
      "ClientId": "@Microsoft.KeyVault(SecretUri=https://your-vault.vault.azure.net/secrets/CamundaClientId/)",
      "ClientSecret": "@Microsoft.KeyVault(SecretUri=https://your-vault.vault.azure.net/secrets/CamundaClientSecret/)",
      "Region": "bru-2"
    }
  }
}
```

---

## 9. Sample BPMN Workflows for Infinity

### Workflow 1: Sales Journey (High-Level)

Create this in **Camunda Modeler** (download from https://camunda.com/download/modeler/)

**infinity-sales-journey.bpmn:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" 
                  xmlns:zeebe="http://camunda.org/schema/zeebe/1.0"
                  id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  
  <bpmn:process id="infinity-sales-journey" name="Infinity Sales Journey" isExecutable="true">
    
    <!-- Start Event -->
    <bpmn:startEvent id="StartEvent_1" name="Application Submitted">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    
    <!-- Service Task: DeDupe Check -->
    <bpmn:serviceTask id="Task_DedupeCheck" name="DeDupe Check">
      <bpmn:extensionElements>
        <zeebe:taskDefinition type="dedupe-check" />
        <zeebe:ioMapping>
          <zeebe:input source="= applicationId" target="applicationId" />
        </zeebe:ioMapping>
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:serviceTask>
    
    <!-- Exclusive Gateway: Check if duplicate -->
    <bpmn:exclusiveGateway id="Gateway_IsDuplicate" name="Is Duplicate?">
      <bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_Duplicate</bpmn:outgoing>
      <bpmn:outgoing>Flow_NotDuplicate</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    
    <!-- Condition: If duplicate, end process -->
    <bpmn:sequenceFlow id="Flow_Duplicate" name="Yes" sourceRef="Gateway_IsDuplicate" targetRef="EndEvent_Duplicate">
      <bpmn:conditionExpression>= isDuplicate = true</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    
    <bpmn:endEvent id="EndEvent_Duplicate" name="Application Rejected (Duplicate)">
      <bpmn:incoming>Flow_Duplicate</bpmn:incoming>
    </bpmn:endEvent>
    
    <!-- Continue if not duplicate -->
    <bpmn:sequenceFlow id="Flow_NotDuplicate" name="No" sourceRef="Gateway_IsDuplicate" targetRef="Task_ImageQC">
      <bpmn:conditionExpression>= isDuplicate = false</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    
    <!-- User Task: ImageQC Approval -->
    <bpmn:userTask id="Task_ImageQC" name="ImageQC Approval">
      <bpmn:extensionElements>
        <zeebe:assignmentDefinition assignee="= imageQCAssignee" candidateGroups="QC_TEAM" />
        <zeebe:formDefinition formKey="camunda-forms:bpmn:ImageQCForm" />
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_NotDuplicate</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing>
    </bpmn:userTask>
    
    <!-- Service Task: Process ImageQC Result -->
    <bpmn:serviceTask id="Task_ProcessImageQC" name="Process ImageQC Result">
      <bpmn:extensionElements>
        <zeebe:taskDefinition type="image-qc-process" />
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_3</bpmn:incoming>
      <bpmn:outgoing>Flow_4</bpmn:outgoing>
    </bpmn:serviceTask>
    
    <!-- Exclusive Gateway: ImageQC Approved? -->
    <bpmn:exclusiveGateway id="Gateway_ImageQCApproved" name="Approved?">
      <bpmn:incoming>Flow_4</bpmn:incoming>
      <bpmn:outgoing>Flow_QC_Approved</bpmn:outgoing>
      <bpmn:outgoing>Flow_QC_Rejected</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    
    <bpmn:sequenceFlow id="Flow_QC_Approved" name="Yes" sourceRef="Gateway_ImageQCApproved" targetRef="Task_PrintQC">
      <bpmn:conditionExpression>= imageQCApproved = true</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    
    <bpmn:sequenceFlow id="Flow_QC_Rejected" name="No" sourceRef="Gateway_ImageQCApproved" targetRef="EndEvent_Rejected">
      <bpmn:conditionExpression>= imageQCApproved = false</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    
    <bpmn:endEvent id="EndEvent_Rejected" name="Application Rejected (QC Failed)">
      <bpmn:incoming>Flow_QC_Rejected</bpmn:incoming>
    </bpmn:endEvent>
    
    <!-- User Task: PrintQC Approval -->
    <bpmn:userTask id="Task_PrintQC" name="PrintQC Approval">
      <bpmn:extensionElements>
        <zeebe:assignmentDefinition candidateGroups="PRINT_QC_TEAM" />
        <zeebe:formDefinition formKey="camunda-forms:bpmn:PrintQCForm" />
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_QC_Approved</bpmn:incoming>
      <bpmn:outgoing>Flow_5</bpmn:outgoing>
    </bpmn:userTask>
    
    <!-- Service Task: Underwriting -->
    <bpmn:serviceTask id="Task_Underwriting" name="Submit to LifeAsia Underwriting">
      <bpmn:extensionElements>
        <zeebe:taskDefinition type="underwriting-submit" />
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_5</bpmn:incoming>
      <bpmn:outgoing>Flow_6</bpmn:outgoing>
    </bpmn:serviceTask>
    
    <!-- End Event -->
    <bpmn:endEvent id="EndEvent_Success" name="Policy Issued">
      <bpmn:incoming>Flow_6</bpmn:incoming>
    </bpmn:endEvent>
    
    <!-- Sequence Flows -->
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_DedupeCheck" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_DedupeCheck" targetRef="Gateway_IsDuplicate" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_ImageQC" targetRef="Task_ProcessImageQC" />
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Task_ProcessImageQC" targetRef="Gateway_ImageQCApproved" />
    <bpmn:sequenceFlow id="Flow_5" sourceRef="Task_PrintQC" targetRef="Task_Underwriting" />
    <bpmn:sequenceFlow id="Flow_6" sourceRef="Task_Underwriting" targetRef="EndEvent_Success" />
    
  </bpmn:process>
</bpmn:definitions>
```

**Visual representation (as you'll see in Camunda Modeler):**

```
[Start] → [DeDupe Check] → <Is Duplicate?>
                                   ├─ Yes → [End: Rejected - Duplicate]
                                   └─ No  → [ImageQC Approval] → [Process ImageQC]
                                                                         ↓
                                                       <ImageQC Approved?>
                                                          ├─ Yes → [PrintQC Approval] → [Underwriting] → [End: Policy Issued]
                                                          └─ No  → [End: Rejected - QC Failed]
```

### Workflow 2: Processing Hub (Detailed)

**infinity-processing-hub.bpmn** - Models the complex approval flow

```
[Start: PDF Generated]
   ↓
[Parallel Gateway] → Split into 3 parallel tasks
   ├─ [DeDupe Check] ──────┐
   ├─ [CKYC Verification] ──┼─→ [Join Gateway]
   └─ [Bank Verification] ──┘         ↓
                          [All Validations Passed?]
                               ├─ Yes → [ImageQC Assignment]
                               └─ No  → [Send Rejection Email] → [End]
                                              ↓
                                    [User Task: ImageQC]
                                              ↓
                                    [Timer: 2 days SLA]
                                              ↓
                                    <ImageQC Approved?>
                                       ├─ Yes → [PrintQC Assignment]
                                       └─ No  → [Rejection Workflow]
                                                      ↓
                                            [User Task: PrintQC]
                                                      ↓
                                            <PrintQC Approved?>
                                               ├─ Yes → [Generate XML] → [Submit to LifeAsia]
                                               └─ No  → [Rejection Workflow]
                                                              ↓
                                                    [LifeAsia Response]
                                                              ↓
                                                    <Policy Issued?>
                                                       ├─ Yes → [Send Welcome Email] → [End: Success]
                                                       └─ No  → [Underwriting Review] → [Manual Review Task]
```

---

## 10. Integration with Existing Microservices

### Option 1: Start Workflow from ProposalForm Service

**Controllers/ApplicationsController.cs:**

```csharp
using Microsoft.AspNetCore.Mvc;
using Zeebe.Client;
using FGLI.ProposalForm.Application.Commands;

namespace FGLI.ProposalForm.API.Controllers;

[ApiController]
[Route("api/[controller]")]
public class ApplicationsController : ControllerBase
{
    private readonly IZeebeClient _zeebeClient;
    private readonly IMediator _mediator;

    public ApplicationsController(IZeebeClient zeebeClient, IMediator mediator)
    {
        _zeebeClient = zeebeClient;
        _mediator = mediator;
    }

    [HttpPost]
    public async Task<IActionResult> SubmitApplication([FromBody] CreateApplicationCommand command)
    {
        // 1. Save application to database (existing logic)
        var result = await _mediator.Send(command);
        
        if (!result.IsSuccess)
            return BadRequest(result.Errors);
        
        var applicationId = result.Value.ApplicationId;
        
        // 2. NEW: Start Camunda workflow
        try
        {
            var processInstance = await _zeebeClient
                .NewCreateProcessInstanceCommand()
                .BpmnProcessId("infinity-sales-journey")  // Matches BPMN process ID
                .LatestVersion()
                .Variables(new
                {
                    applicationId = applicationId,
                    applicantName = command.ApplicantName,
                    productCode = command.ProductCode,
                    sumAssured = command.SumAssured,
                    // ... other relevant data
                })
                .Send();

            _logger.LogInformation(
                "Camunda process instance {ProcessInstanceKey} started for application {ApplicationId}",
                processInstance.ProcessInstanceKey, applicationId);

            return Ok(new
            {
                applicationId = applicationId,
                processInstanceId = processInstance.ProcessInstanceKey,
                message = "Application submitted successfully"
            });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to start Camunda workflow for application {ApplicationId}", applicationId);
            
            // IMPORTANT: Application is already saved, so return success
            // Create a compensating task or retry mechanism
            return Ok(new
            {
                applicationId = applicationId,
                warning = "Application saved but workflow start failed. Will retry automatically."
            });
        }
    }
}
```

### Option 2: Trigger Workflow via Azure Service Bus (Decoupled)

**Better approach for microservices architecture:**

```csharp
// ProposalForm Service publishes event
public class ApplicationSubmittedEventHandler : INotificationHandler<ApplicationSubmittedEvent>
{
    private readonly IServiceBusClient _serviceBus;

    public async Task Handle(ApplicationSubmittedEvent notification, CancellationToken cancellationToken)
    {
        var message = new
        {
            EventType = "ApplicationSubmitted",
            ApplicationId = notification.ApplicationId,
            Timestamp = DateTime.UtcNow,
            Data = notification
        };

        await _serviceBus.PublishAsync("application-events", message, cancellationToken);
    }
}

// Separate "Workflow Orchestrator" service listens and starts workflows
public class WorkflowOrchestratorService : IHostedService
{
    private readonly IZeebeClient _zeebeClient;
    private readonly IServiceBusConsumer _serviceBus;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        await _serviceBus.SubscribeAsync<ApplicationSubmittedEvent>(
            "application-events",
            async (evt) =>
            {
                await _zeebeClient
                    .NewCreateProcessInstanceCommand()
                    .BpmnProcessId("infinity-sales-journey")
                    .LatestVersion()
                    .Variables(evt)
                    .Send();
            },
            cancellationToken);
    }
}
```

**Benefits:**
- ✅ No coupling between ProposalForm and Camunda
- ✅ Can replay events if workflow start fails
- ✅ Easy to add new workflows without changing ProposalForm

---

## 11. Testing & Debugging

### Unit Testing Workers

**Tests/Workers/DedupeCheckWorkerTests.cs:**

```csharp
using Xunit;
using Moq;
using Zeebe.Client.Api.Responses;
using FGLI.ProcessingHub.Workers;

public class DedupeCheckWorkerTests
{
    [Fact]
    public async Task ExecuteAsync_WhenDuplicateFound_ReturnsIsDuplicateTrue()
    {
        // Arrange
        var mockZeebeClient = new Mock<IZeebeClient>();
        var mockDedupeService = new Mock<IDedupeService>();
        var mockLogger = new Mock<ILogger<DedupeCheckWorker>>();

        mockDedupeService
            .Setup(s => s.CheckForDuplicatesAsync(123, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new DedupeResult { IsDuplicate = true, DuplicateIds = new[] { 456 } });

        var worker = new DedupeCheckWorker(mockZeebeClient.Object, mockDedupeService.Object, mockLogger.Object);

        var job = Mock.Of<IJob>();
        var variables = new Dictionary<string, object> { { "applicationId", 123 } };

        // Act
        var result = await worker.ExecuteAsync(job, variables, CancellationToken.None);

        // Assert
        Assert.True((bool)result["isDuplicate"]);
        Assert.Equal("DUPLICATE_FOUND", result["dedupeStatus"]);
    }
}
```

### Integration Testing with Testcontainers

```powershell
dotnet add package Testcontainers.Zeebe
```

```csharp
using Testcontainers.Zeebe;
using Xunit;

public class CamundaIntegrationTests : IAsyncLifetime
{
    private ZeebeContainer _zeebeContainer;
    private IZeebeClient _client;

    public async Task InitializeAsync()
    {
        // Start Zeebe container
        _zeebeContainer = new ZeebeBuilder()
            .WithImage("camunda/zeebe:8.4.3")
            .Build();

        await _zeebeContainer.StartAsync();

        // Create client
        _client = CamundaCloudClientBuilder.Builder()
            .UseGatewayAddress(_zeebeContainer.GetContactPoint())
            .UsePlainText()
            .Build();
    }

    [Fact]
    public async Task SalesJourney_WhenValidApplication_CompletesSuccessfully()
    {
        // Deploy BPMN
        await _client.NewDeployCommand()
            .AddResourceFile("bpmn/infinity-sales-journey.bpmn")
            .Send();

        // Start process instance
        var processInstance = await _client
            .NewCreateProcessInstanceCommand()
            .BpmnProcessId("infinity-sales-journey")
            .LatestVersion()
            .Variables(new { applicationId = 12345 })
            .WithResult()  // Wait for completion
            .Send();

        // Assert
        Assert.NotNull(processInstance);
        // ... more assertions
    }

    public async Task DisposeAsync()
    {
        await _zeebeContainer.DisposeAsync();
    }
}
```

### Debugging in Camunda Operate

1. **View Running Instances:**
   - Go to http://localhost:8081
   - See all active process instances
   - Click on an instance to see current state

2. **Inspect Variables:**
   - Click on any process instance
   - View all variables at any point in time

3. **Handle Incidents:**
   - If a worker fails, an "incident" is created
   - View error message, stack trace
   - Retry or cancel the job

4. **Process History:**
   - See audit trail of all completed tasks
   - View duration of each task (identify bottlenecks)

---

## 12. Monitoring & Operations

### Metrics & Observability

**Option 1: Prometheus + Grafana (Self-Hosted)**

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'zeebe'
    static_configs:
      - targets: ['zeebe:9600']  # Zeebe metrics endpoint
```

**Key Metrics to Monitor:**
- `zeebe_stream_processor_records_total` - Total records processed
- `zeebe_pending_jobs` - Jobs waiting for workers
- `zeebe_processing_duration` - Task execution time
- `zeebe_backpressure` - System overload indicator

**Grafana Dashboard:**
- Import Camunda community dashboard: https://grafana.com/grafana/dashboards/15682

---

**Option 2: Azure Application Insights (Cloud-Native)**

```csharp
// In your worker, log to App Insights
builder.Services.AddApplicationInsightsTelemetry();

// Custom metrics
_telemetry.TrackMetric("WorkflowDuration", duration.TotalSeconds, new Dictionary<string, string>
{
    { "ProcessId", "infinity-sales-journey" },
    { "ApplicationId", applicationId.ToString() }
});
```

---

### Alerting

**Critical Alerts:**

1. **High Incident Rate**
   - Alert if > 5% of jobs fail
   - `rate(zeebe_incidents_total[5m]) > 0.05`

2. **Worker Inactivity**
   - Alert if no jobs processed in 10 minutes
   - `rate(zeebe_executed_jobs_total[10m]) == 0`

3. **SLA Breach**
   - Alert if ImageQC tasks pending > 2 days
   - Query Camunda Operate API:
     ```csharp
     var pendingTasks = await operateClient.GetTasksAsync(new TaskQuery
     {
         State = TaskState.CREATED,
         TaskDefinitionId = "Task_ImageQC",
         CreatedBefore = DateTime.UtcNow.AddDays(-2)
     });
     ```

---

### Backup & Disaster Recovery

**What to Backup:**
1. **Zeebe data directory** - Process state (persistent volume)
2. **Elasticsearch indices** - Process history (Operate/Tasklist data)
3. **BPMN models** - Store in Git (version control)

**Azure Backup Strategy:**

```powershell
# Automated snapshots of AKS persistent volumes
az aks backup enable \
  --resource-group rg-infinity-camunda-prod \
  --name aks-camunda-prod \
  --backup-vault-id /subscriptions/.../vaults/camunda-backup-vault \
  --snapshot-frequency daily \
  --retention-days 30
```

**Elasticsearch Backups:**

```bash
# Snapshot to Azure Blob Storage
PUT _snapshot/azure_backup
{
  "type": "azure",
  "settings": {
    "account": "infinitycamundabackup",
    "container": "elasticsearch-snapshots",
    "base_path": "zeebe-data"
  }
}

# Create snapshot
POST _snapshot/azure_backup/snapshot_1
```

---

## 13. Migration Strategy

### Phase 1: Proof of Concept (Week 1-2)

**Goal:** Demonstrate value with minimal changes

**Scope:**
- Deploy Camunda locally (Docker Compose)
- Model "ImageQC Approval" workflow (simplest flow)
- Implement 1 worker: ImageQCWorker
- Run 10 test applications through Camunda
- Demo to stakeholders

**Success Criteria:**
- ✅ QC team can approve/reject in Tasklist
- ✅ Ops team can see process state in Operate
- ✅ All 10 test apps complete successfully

---

### Phase 2: Pilot in Staging (Week 3-6)

**Goal:** Run Camunda alongside existing system (dark launch)

**Implementation:**
- Deploy Camunda to Azure (Docker Compose on VM or Camunda Cloud)
- Model "Processing Hub" workflow (DeDupe → ImageQC → PrintQC → Underwriting)
- Implement all required workers
- **Dual-write**: Save to both existing DB tables AND start Camunda workflow
- Monitor for discrepancies

**Testing:**
- Run 100 real staging applications through Camunda
- Compare results with existing system
- Performance testing (latency, throughput)

---

### Phase 3: Production Rollout (Week 7-12)

**Goal:** Replace existing stepper logic with Camunda

**Approach: Canary Deployment**

```csharp
// Feature flag to control rollout
public async Task<IActionResult> SubmitApplication([FromBody] CreateApplicationCommand command)
{
    var result = await _mediator.Send(command);
    var applicationId = result.Value.ApplicationId;

    // Use feature flag to gradually enable Camunda
    if (_featureFlags.IsEnabled("UseCamundaWorkflow", applicationId))
    {
        // NEW PATH: Start Camunda workflow
        await _zeebeClient.NewCreateProcessInstanceCommand()
            .BpmnProcessId("infinity-sales-journey")
            .LatestVersion()
            .Variables(new { applicationId })
            .Send();
    }
    else
    {
        // OLD PATH: Use existing stepper logic
        await _legacyStepperService.InitializeAsync(applicationId);
    }

    return Ok(new { applicationId });
}
```

**Rollout Schedule:**
- Week 7: 5% of applications
- Week 8: 10%
- Week 9: 25%
- Week 10: 50%
- Week 11: 75%
- Week 12: 100% (remove old code)

---

### Phase 4: Optimization (Week 13-16)

**Goal:** Add advanced features

**Enhancements:**
1. **Process Analytics** - Enable Camunda Optimize
2. **Auto-Assignment** - Use Camunda's DMN (Decision Model Notation) for task routing
3. **SLA Monitoring** - Add timer boundary events for escalations
4. **Compensation** - Add refund/cancellation workflows
5. **A/B Testing** - Deploy multiple process versions, compare performance

---

## 14. Troubleshooting

### Common Issues

#### Issue 1: Worker Not Receiving Jobs

**Symptom:** Process instance stuck, worker logs show no activity

**Diagnosis:**
```bash
# Check if Zeebe is reachable
zbctl --address localhost:26500 status

# List active workers
zbctl --address localhost:26500 inspect workers
```

**Solutions:**
1. Verify job type matches BPMN: `<zeebe:taskDefinition type="dedupe-check" />`
2. Check worker is running: `docker-compose ps`
3. Check network connectivity: `telnet zeebe-host 26500`

---

#### Issue 2: High Incident Rate

**Symptom:** Many red triangles in Camunda Operate

**Diagnosis:**
```bash
# View incidents
zbctl --address localhost:26500 inspect incidents

# Check worker logs
docker-compose logs worker-dedupe
```

**Common Causes:**
1. **Worker throwing exceptions** - Check logs, fix code
2. **Job timeout too short** - Increase timeout in worker registration:
   ```csharp
   .Timeout(TimeSpan.FromMinutes(10))  // Increase if needed
   ```
3. **Variables missing** - Ensure BPMN `<zeebe:input>` mappings are correct

---

#### Issue 3: Process Instance Not Progressing

**Symptom:** Instance stuck at a task, no incident

**Diagnosis:**
1. Check Operate: Is it waiting for a worker?
2. Check if worker is registered:
   ```bash
   zbctl inspect workers | grep dedupe-check
   ```
3. Check if job has retries left:
   - If retries = 0 → Create incident
   - If retries > 0 → Still retrying

**Solution:**
- Restart worker
- Or manually retry in Operate: Click instance → "Retry"

---

#### Issue 4: Camunda Cloud Connection Errors

**Symptom:** `UNAUTHENTICATED` or `UNAVAILABLE` errors

**Diagnosis:**
```csharp
try
{
    var topology = await _zeebeClient.TopologyRequest().Send();
    _logger.LogInformation("Connected to {Brokers} brokers", topology.Brokers.Count);
}
catch (Exception ex)
{
    _logger.LogError(ex, "Failed to connect to Camunda Cloud");
}
```

**Solutions:**
1. Verify credentials (Client ID/Secret)
2. Check cluster is running (Camunda Cloud Console)
3. Check firewall allows port 443 outbound
4. Regenerate client credentials if expired

---

### Performance Optimization

#### Slow Worker Throughput

**Problem:** Only processing 10 jobs/second, need 100+

**Solutions:**

1. **Increase worker concurrency:**
   ```csharp
   .MaxJobsActive(50)  // Default is 10, increase to 50-100
   ```

2. **Scale horizontally:**
   ```bash
   # Run multiple worker instances
   docker-compose up --scale worker-dedupe=5
   ```

3. **Optimize worker logic:**
   - Cache frequently accessed data (Redis)
   - Use bulk API calls instead of single requests
   - Add async/await properly (avoid blocking calls)

---

#### Large Process Instances (>1MB variables)

**Problem:** Zeebe slows down with huge variable payloads

**Solution:** Store large data externally, pass references:

```csharp
// BAD: Storing large base64 PDF in Camunda
var variables = new
{
    applicationId = 123,
    pdfContent = Convert.ToBase64String(pdfBytes)  // 5MB!
};

// GOOD: Store PDF in Azure Blob, pass URL
var blobUrl = await _blobStorage.UploadAsync(pdfBytes);
var variables = new
{
    applicationId = 123,
    pdfBlobUrl = blobUrl  // Small string
};
```

---

### Support Resources

| Resource | URL |
|----------|-----|
| **Camunda Docs** | https://docs.camunda.io |
| **Community Forum** | https://forum.camunda.io |
| **.NET Client Docs** | https://github.com/camunda-community-hub/zeebe-client-csharp |
| **BPMN Tutorial** | https://camunda.com/bpmn/ |
| **GitHub Issues** | https://github.com/camunda/zeebe/issues |
| **Slack Community** | https://camunda.com/slack |

---

## Quick Start Checklist

- [ ] Install Camunda Desktop or Docker Compose
- [ ] Download Camunda Modeler
- [ ] Create first BPMN diagram (ImageQC approval)
- [ ] Deploy BPMN to Zeebe
- [ ] Install `Zeebe.Client` NuGet package
- [ ] Implement one worker (DedupeCheckWorker)
- [ ] Register worker in DI
- [ ] Start worker as Hosted Service
- [ ] Test: Submit application → Worker executes → Check Operate
- [ ] Expand to full workflow
- [ ] Deploy to Azure (Camunda Cloud or AKS)
- [ ] Configure monitoring (App Insights)
- [ ] Train QC team on Tasklist
- [ ] Rollout to production (feature flag)

---

## Next Steps

1. **Today:** Deploy Camunda Desktop, model ImageQC workflow
2. **This Week:** Implement 1-2 workers, run POC with 10 test apps
3. **Next Week:** Present to stakeholders, get buy-in
4. **Month 1:** Pilot in staging with full Processing Hub workflow
5. **Month 2:** Production rollout with canary deployment
6. **Month 3:** Add advanced features (Optimize, DMN, compensation)

---

**Questions?** Open an issue in your project repo or contact the Camunda community:
- Forum: https://forum.camunda.io
- Slack: https://camunda.com/slack

**Happy Workflow Automation! 🚀**
