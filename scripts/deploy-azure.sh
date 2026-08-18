#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCE_GROUP="${MELVAN_RESOURCE_GROUP:-}"
LOCATION="${MELVAN_LOCATION:-}"
APP_NAME="${MELVAN_APP_NAME:-}"
IMAGE_TAG="${MELVAN_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}"

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

command -v az >/dev/null || { echo 'Azure CLI is required.' >&2; exit 2; }
az account show >/dev/null
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

ACCOUNT_TYPE="$(az account show --query user.type -o tsv)"
if [[ "$ACCOUNT_TYPE" == "servicePrincipal" ]]; then
  ACCOUNT_NAME="$(az account show --query user.name -o tsv)"
  DEPLOYMENT_PRINCIPAL_ID="$(az ad sp show --id "$ACCOUNT_NAME" --query id -o tsv)"
else
  DEPLOYMENT_PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
fi

# Reuse the stable Key Vault secret after the first deployment. On the first
# run, generate it locally and pass it only as a secure deployment parameter.
KEY_VAULT_NAME="$(az resource list --resource-group "$RESOURCE_GROUP" --resource-type Microsoft.KeyVault/vaults --query '[0].name' -o tsv 2>/dev/null || true)"
AUTH_PEPPER=""
if [[ -n "$KEY_VAULT_NAME" ]]; then
  AUTH_PEPPER="$(az keyvault secret show --vault-name "$KEY_VAULT_NAME" --name melvan-auth-pepper --query value -o tsv 2>/dev/null || true)"
fi
if [[ -z "$AUTH_PEPPER" ]]; then
  AUTH_PEPPER="$(openssl rand -hex 48)"
fi

az deployment group create \
  --name melvan-foundation \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$ROOT/infra/foundation.bicep" \
  --parameters appName="$APP_NAME" location="$LOCATION" authPepper="$AUTH_PEPPER" deploymentPrincipalId="$DEPLOYMENT_PRINCIPAL_ID" \
  --output none
unset AUTH_PEPPER

REGISTRY_NAME="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.registryName.value -o tsv)"
REGISTRY_SERVER="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.registryServer.value -o tsv)"
ENVIRONMENT_NAME="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.environmentName.value -o tsv)"
KEY_VAULT_NAME="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.keyVaultName.value -o tsv)"
IDENTITY_PRINCIPAL_ID="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.identityPrincipalId.value -o tsv)"
SPEECH_SCOPE="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.speechResourceId.value -o tsv)"
SPEECH_ENDPOINT="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.speechEndpoint.value -o tsv)"
DOCUMENT_INTELLIGENCE_SCOPE="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.documentIntelligenceResourceId.value -o tsv)"
DOCUMENT_INTELLIGENCE_ENDPOINT="$(az deployment group show --resource-group "$RESOURCE_GROUP" --name melvan-foundation --query properties.outputs.documentIntelligenceEndpoint.value -o tsv)"

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

FOUNDRY_SCOPE="$(az resource show --resource-group "$MELVAN_FOUNDRY_RESOURCE_GROUP" --name "$MELVAN_FOUNDRY_ACCOUNT" --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)"
COMMUNICATION_SCOPE="$(az resource show --resource-group "$MELVAN_COMMUNICATION_RESOURCE_GROUP" --name "$MELVAN_COMMUNICATION_RESOURCE" --resource-type Microsoft.Communication/communicationServices --query id -o tsv)"
ensure_role() {
  local scope="$1"
  local role="$2"
  local count
  count="$(az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$scope" --role "$role" --query 'length(@)' -o tsv)"
  if [[ "$count" == "0" ]]; then
    az role assignment create --assignee-object-id "$IDENTITY_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "$role" --scope "$scope" --output none
  fi
}

ensure_role "$FOUNDRY_SCOPE" "Cognitive Services User"
ensure_role "$SPEECH_SCOPE" "Cognitive Services Speech User"
ensure_role "$DOCUMENT_INTELLIGENCE_SCOPE" "Cognitive Services User"
# Communication Services documents Contributor, or an equivalent custom role
# with CommunicationServices Read/Write, for managed-identity email delivery.
ensure_role "$COMMUNICATION_SCOPE" Contributor

az acr build --registry "$REGISTRY_NAME" --image "melvan:$IMAGE_TAG" "$ROOT" --output none

az deployment group create \
  --name melvan-app \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$ROOT/infra/app.bicep" \
  --parameters appName="$APP_NAME" location="$LOCATION" \
    image="$REGISTRY_SERVER/melvan:$IMAGE_TAG" registryName="$REGISTRY_NAME" \
    environmentName="$ENVIRONMENT_NAME" keyVaultName="$KEY_VAULT_NAME" \
    foundryProjectEndpoint="$MELVAN_FOUNDRY_PROJECT_ENDPOINT" modelDeployment="$MELVAN_MODEL" \
    emailEndpoint="$MELVAN_EMAIL_ENDPOINT" emailSender="$MELVAN_EMAIL_SENDER" \
    speechEndpoint="$SPEECH_ENDPOINT" \
    documentIntelligenceEndpoint="$DOCUMENT_INTELLIGENCE_ENDPOINT" \
    initialAdminEmail="$MELVAN_INITIAL_ADMIN_EMAIL" \
    customDomainName="$CUSTOM_DOMAIN_NAME" customDomainCertificateId="$CUSTOM_DOMAIN_CERTIFICATE_ID" \
  --output none

PUBLIC_URL="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name melvan-app \
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
      printf 'Revision %s failed with state %s.\n' "$LATEST_REVISION" "$RUNNING_STATE" >&2
      break
      ;;
  esac
  sleep 15
done

if [[ "$REVISION_READY" != true ]]; then
  az containerapp logs show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --revision "$LATEST_REVISION" \
    --type system \
    --tail 50 || true
  az containerapp logs show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --revision "$LATEST_REVISION" \
    --type console \
    --tail 50 || true
  printf 'Revision %s did not become healthy.\n' "$LATEST_REVISION" >&2
  exit 1
fi

printf 'Verifying MELvan %s at %s...\n' "$EXPECTED_VERSION" "$PUBLIC_URL"
VERSION_READY=false
for _ in {1..20}; do
  HEALTH_PAYLOAD="$(curl --silent --show-error --fail \
    --connect-timeout 10 --max-time 20 \
    "$PUBLIC_URL/api/health" 2>/dev/null || true)"
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

printf 'MELvan %s is live on revision %s at %s\n' \
  "$EXPECTED_VERSION" "$LATEST_REVISION" "$PUBLIC_URL"
