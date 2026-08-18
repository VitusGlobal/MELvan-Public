#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${MELVAN_RESOURCE_GROUP:-}"
LOCATION="${MELVAN_LOCATION:-}"
APP_NAME="${MELVAN_APP_NAME:-}"
IMAGE_TAG="${MELVAN_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}"
POSTGRES_SKU="${MELVAN_POSTGRES_SKU:-Standard_B1ms}"
POSTGRES_TIER="${MELVAN_POSTGRES_TIER:-Burstable}"
POSTGRES_VERSION="${MELVAN_POSTGRES_VERSION:-16}"
POSTGRES_STORAGE_GB="${MELVAN_POSTGRES_STORAGE_GB:-32}"
MIN_REPLICAS="${MELVAN_MIN_REPLICAS:-2}"
MAX_REPLICAS="${MELVAN_MAX_REPLICAS:-4}"

required=(
  MELVAN_RESOURCE_GROUP MELVAN_LOCATION MELVAN_APP_NAME
  MELVAN_FOUNDRY_RESOURCE_GROUP MELVAN_FOUNDRY_ACCOUNT
  MELVAN_FOUNDRY_PROJECT_ENDPOINT MELVAN_MODEL
  MELVAN_COMMUNICATION_RESOURCE_GROUP MELVAN_COMMUNICATION_RESOURCE
  MELVAN_EMAIL_ENDPOINT MELVAN_EMAIL_SENDER
  MELVAN_INITIAL_ADMIN_EMAIL
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Missing required deployment variable: %s\n' "$name" >&2
    exit 2
  fi
done

for command in az curl openssl python; do
  command -v "$command" >/dev/null || {
    printf '%s is required.\n' "$command" >&2
    exit 2
  }
done
for value in "$POSTGRES_STORAGE_GB" "$MIN_REPLICAS" "$MAX_REPLICAS"; do
  [[ "$value" =~ ^[0-9]+$ ]] || {
    echo 'PostgreSQL storage and replica counts must be integers.' >&2
    exit 2
  }
done
if (( MIN_REPLICAS < 2 || MAX_REPLICAS < MIN_REPLICAS )); then
  echo 'Production requires MELVAN_MIN_REPLICAS >= 2 and MELVAN_MAX_REPLICAS >= MELVAN_MIN_REPLICAS.' >&2
  exit 2
fi

az account show >/dev/null
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

ACCOUNT_TYPE="$(az account show --query user.type -o tsv)"
if [[ "$ACCOUNT_TYPE" == "servicePrincipal" ]]; then
  ACCOUNT_NAME="$(az account show --query user.name -o tsv)"
  DEPLOYMENT_PRINCIPAL_ID="$(az ad sp show --id "$ACCOUNT_NAME" --query id -o tsv)"
else
  DEPLOYMENT_PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
fi

KEY_VAULT_NAME="$(az resource list --resource-group "$RESOURCE_GROUP" --resource-type Microsoft.KeyVault/vaults --query '[0].name' -o tsv 2>/dev/null || true)"
read_secret() {
  local name="$1"
  if [[ -z "$KEY_VAULT_NAME" ]]; then
    return
  fi
  az keyvault secret show \
    --vault-name "$KEY_VAULT_NAME" \
    --name "$name" \
    --query value \
    --output tsv 2>/dev/null || true
}

AUTH_PEPPER="$(read_secret melvan-auth-pepper)"
POSTGRES_ADMIN_URL="$(read_secret melvan-postgres-migration-url)"
POSTGRES_RUNTIME_URL="$(read_secret melvan-postgres-runtime-url)"
if [[ -z "$AUTH_PEPPER" ]]; then
  AUTH_PEPPER="$(openssl rand -hex 48)"
fi
if [[ -n "$POSTGRES_ADMIN_URL" ]]; then
  POSTGRES_ADMIN_PASSWORD="$(printf '%s' "$POSTGRES_ADMIN_URL" | python -c 'from sqlalchemy.engine import make_url; import sys; print(make_url(sys.stdin.read()).password or "")')"
else
  POSTGRES_ADMIN_PASSWORD="$(openssl rand -hex 24)Aa1"
fi
if [[ -n "$POSTGRES_RUNTIME_URL" ]]; then
  POSTGRES_RUNTIME_PASSWORD="$(printf '%s' "$POSTGRES_RUNTIME_URL" | python -c 'from sqlalchemy.engine import make_url; import sys; print(make_url(sys.stdin.read()).password or "")')"
else
  POSTGRES_RUNTIME_PASSWORD="$(openssl rand -hex 24)Bb2"
fi
unset POSTGRES_ADMIN_URL POSTGRES_RUNTIME_URL

az deployment group create \
  --name melvan-production-foundation \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$ROOT/infra/production-foundation.bicep" \
  --parameters appName="$APP_NAME" location="$LOCATION" \
    authPepper="$AUTH_PEPPER" deploymentPrincipalId="$DEPLOYMENT_PRINCIPAL_ID" \
    postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
    postgresRuntimePassword="$POSTGRES_RUNTIME_PASSWORD" \
    postgresSkuName="$POSTGRES_SKU" postgresTier="$POSTGRES_TIER" \
    postgresVersion="$POSTGRES_VERSION" \
    postgresStorageSizeGB="$POSTGRES_STORAGE_GB" \
  --output none
unset AUTH_PEPPER POSTGRES_ADMIN_PASSWORD POSTGRES_RUNTIME_PASSWORD

deployment_output() {
  az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name melvan-production-foundation \
    --query "properties.outputs.$1.value" \
    --output tsv
}

REGISTRY_NAME="$(deployment_output registryName)"
REGISTRY_SERVER="$(deployment_output registryServer)"
ENVIRONMENT_NAME="$(deployment_output environmentName)"
KEY_VAULT_NAME="$(deployment_output keyVaultName)"
IDENTITY_PRINCIPAL_ID="$(deployment_output identityPrincipalId)"
MIGRATION_IDENTITY_NAME="$(deployment_output migrationIdentityName)"
BLOB_ACCOUNT_URL="$(deployment_output storageAccountUrl)"
BLOB_CONTAINER_NAME="$(deployment_output blobContainerName)"
SPEECH_SCOPE="$(deployment_output speechResourceId)"
SPEECH_ENDPOINT="$(deployment_output speechEndpoint)"
DOCUMENT_INTELLIGENCE_SCOPE="$(deployment_output documentIntelligenceResourceId)"
DOCUMENT_INTELLIGENCE_ENDPOINT="$(deployment_output documentIntelligenceEndpoint)"

FOUNDRY_SCOPE="$(az resource show --resource-group "$MELVAN_FOUNDRY_RESOURCE_GROUP" --name "$MELVAN_FOUNDRY_ACCOUNT" --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)"
COMMUNICATION_SCOPE="$(az resource show --resource-group "$MELVAN_COMMUNICATION_RESOURCE_GROUP" --name "$MELVAN_COMMUNICATION_RESOURCE" --resource-type Microsoft.Communication/communicationServices --query id -o tsv)"
ensure_role() {
  local scope="$1"
  local role="$2"
  local count
  count="$(az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$scope" --role "$role" --query 'length(@)' -o tsv)"
  if [[ "$count" == "0" ]]; then
    az role assignment create \
      --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

ensure_role "$FOUNDRY_SCOPE" "Cognitive Services User"
ensure_role "$SPEECH_SCOPE" "Cognitive Services Speech User"
ensure_role "$DOCUMENT_INTELLIGENCE_SCOPE" "Cognitive Services User"
ensure_role "$COMMUNICATION_SCOPE" Contributor

az acr build --registry "$REGISTRY_NAME" --image "melvan:$IMAGE_TAG" "$ROOT" --output none
IMAGE="$REGISTRY_SERVER/melvan:$IMAGE_TAG"

az deployment group create \
  --name melvan-production-migration-job \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$ROOT/infra/production-migration-job.bicep" \
  --parameters appName="$APP_NAME" location="$LOCATION" image="$IMAGE" \
    registryName="$REGISTRY_NAME" environmentName="$ENVIRONMENT_NAME" \
    identityName="$MIGRATION_IDENTITY_NAME" keyVaultName="$KEY_VAULT_NAME" \
  --output none

MIGRATION_JOB_NAME="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name melvan-production-migration-job \
  --query properties.outputs.jobName.value \
  --output tsv)"
printf 'Starting schema migration job %s...\n' "$MIGRATION_JOB_NAME"
EXECUTION_NAME="$(az containerapp job start \
  --name "$MIGRATION_JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query name \
  --output tsv)"
MIGRATION_SUCCEEDED=false
for _ in {1..60}; do
  EXECUTION_STATUS="$(az containerapp job execution show \
    --name "$MIGRATION_JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --job-execution-name "$EXECUTION_NAME" \
    --query properties.status \
    --output tsv 2>/dev/null || true)"
  case "$EXECUTION_STATUS" in
    Succeeded)
      MIGRATION_SUCCEEDED=true
      break
      ;;
    Failed|Stopped)
      break
      ;;
  esac
  sleep 10
done
if [[ "$MIGRATION_SUCCEEDED" != true ]]; then
  az containerapp job logs show \
    --name "$MIGRATION_JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --execution "$EXECUTION_NAME" \
    --container migrate \
    --tail 100 || true
  printf 'Migration execution %s ended with status %s. The web application was not deployed.\n' \
    "$EXECUTION_NAME" "${EXECUTION_STATUS:-unknown}" >&2
  exit 1
fi

CUSTOM_DOMAIN_NAME="${MELVAN_CUSTOM_DOMAIN:-}"
CUSTOM_DOMAIN_CERTIFICATE_ID=""
if [[ -n "$CUSTOM_DOMAIN_NAME" ]]; then
  CUSTOM_DOMAIN_CERTIFICATE_ID="$(
    az containerapp env certificate list \
      --resource-group "$RESOURCE_GROUP" \
      --name "$ENVIRONMENT_NAME" \
      --query "[?properties.subjectName=='${CUSTOM_DOMAIN_NAME}'].id | [0]" \
      --output tsv
  )"
  if [[ -z "$CUSTOM_DOMAIN_CERTIFICATE_ID" ]]; then
    printf 'No managed certificate exists for MELVAN_CUSTOM_DOMAIN=%s in Container Apps environment %s.\n' \
      "$CUSTOM_DOMAIN_NAME" "$ENVIRONMENT_NAME" >&2
    exit 2
  fi
fi

az deployment group create \
  --name melvan-production-app \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$ROOT/infra/production-app.bicep" \
  --parameters appName="$APP_NAME" location="$LOCATION" image="$IMAGE" \
    registryName="$REGISTRY_NAME" environmentName="$ENVIRONMENT_NAME" \
    keyVaultName="$KEY_VAULT_NAME" blobAccountUrl="$BLOB_ACCOUNT_URL" \
    blobContainerName="$BLOB_CONTAINER_NAME" \
    foundryProjectEndpoint="$MELVAN_FOUNDRY_PROJECT_ENDPOINT" \
    modelDeployment="$MELVAN_MODEL" emailEndpoint="$MELVAN_EMAIL_ENDPOINT" \
    emailSender="$MELVAN_EMAIL_SENDER" speechEndpoint="$SPEECH_ENDPOINT" \
    documentIntelligenceEndpoint="$DOCUMENT_INTELLIGENCE_ENDPOINT" \
    initialAdminEmail="$MELVAN_INITIAL_ADMIN_EMAIL" \
    minReplicas="$MIN_REPLICAS" maxReplicas="$MAX_REPLICAS" \
    customDomainName="$CUSTOM_DOMAIN_NAME" \
    customDomainCertificateId="$CUSTOM_DOMAIN_CERTIFICATE_ID" \
  --output none

PUBLIC_URL="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name melvan-production-app \
  --query properties.outputs.url.value \
  --output tsv)"
LATEST_REVISION="$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.latestRevisionName \
  --output tsv)"
EXPECTED_VERSION="$(PYTHONPATH="$ROOT/src" python -c 'from melvan import __version__; print(__version__)')"

printf 'Waiting for revision %s to become healthy...\n' "$LATEST_REVISION"
REVISION_READY=false
for _ in {1..40}; do
  RUNNING_STATE="$(az containerapp revision show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --revision "$LATEST_REVISION" \
    --query properties.runningState \
    --output tsv 2>/dev/null || true)"
  case "$RUNNING_STATE" in
    Running|RunningAtMaxScale)
      REVISION_READY=true
      break
      ;;
    ActivationFailed|Degraded|Failed)
      break
      ;;
  esac
  sleep 15
done
if [[ "$REVISION_READY" != true ]]; then
  az containerapp logs show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --revision "$LATEST_REVISION" --type system --tail 50 || true
  az containerapp logs show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --revision "$LATEST_REVISION" --type console --tail 50 || true
  printf 'Revision %s did not become healthy; state=%s.\n' "$LATEST_REVISION" "${RUNNING_STATE:-unknown}" >&2
  exit 1
fi

printf 'Verifying MELvan %s at %s...\n' "$EXPECTED_VERSION" "$PUBLIC_URL"
VERSION_READY=false
for _ in {1..20}; do
  HEALTH_PAYLOAD="$(curl --silent --show-error --fail --connect-timeout 10 --max-time 20 "$PUBLIC_URL/api/health" 2>/dev/null || true)"
  if python -c \
    'import json, sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("app_version") == sys.argv[2] else 1)' \
    "$HEALTH_PAYLOAD" "$EXPECTED_VERSION" 2>/dev/null; then
    VERSION_READY=true
    break
  fi
  sleep 10
done
if [[ "$VERSION_READY" != true ]]; then
  printf 'Public health endpoint did not report MELvan %s. Last response: %s\n' \
    "$EXPECTED_VERSION" "$HEALTH_PAYLOAD" >&2
  exit 1
fi

printf 'MELvan %s production profile is live on revision %s at %s\n' \
  "$EXPECTED_VERSION" "$LATEST_REVISION" "$PUBLIC_URL"
