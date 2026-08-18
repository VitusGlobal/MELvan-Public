# Configuration

Configuration is supplied through environment variables. Copy `.env.example`
to `.env` for local work and never commit the populated file. Hosted values
belong in GitHub variables, Azure managed identity, or Key Vault.

## Application

| Variable | Purpose |
| --- | --- |
| `MELVAN_DEPLOYMENT_PROFILE` | `local`, `hosted_demo`, or `production` |
| `MELVAN_ENVIRONMENT` | `development`, `test`, or `hosted` |
| `MELVAN_HOST`, `MELVAN_PORT` | Local listening address and port |
| `MELVAN_INITIAL_ADMIN_EMAIL` | Email for the sole initial Owner |
| `MELVAN_AUTH_PEPPER` | Hosted password-hash pepper; at least 32 characters |
| `MELVAN_PUBLIC_BASE_URL` | Canonical hosted HTTPS URL |

## Analysis provider

| Variable | Purpose |
| --- | --- |
| `MELVAN_MODEL_MODE` | `demo`, `foundry`, or `openai` |
| `MELVAN_MODEL` | Provider deployment or model name |
| `MELVAN_FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `MELVAN_AUTH_MODE` | `browser`, `device_code`, or `managed_identity` |
| `AZURE_TENANT_ID` | Local Azure tenant selector |
| `AZURE_CLIENT_ID` | Hosted user-assigned managed identity client ID |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | Optional OpenAI-compatible provider |

The deterministic `demo` model is for tests and interface evaluation. It must
not be mistaken for a quality assessment of a live model.

## Email, speech, and OCR

| Variable | Purpose |
| --- | --- |
| `MELVAN_EMAIL_MODE` | `console` or `azure` |
| `MELVAN_EMAIL_ENDPOINT`, `MELVAN_EMAIL_SENDER` | Azure Communication Services email |
| `MELVAN_SPEECH_MODE`, `MELVAN_SPEECH_ENDPOINT` | Voice transcription provider |
| `MELVAN_SPEECH_LANGUAGE` | Recognition language; defaults to `en-US` |
| `MELVAN_OCR_MODE`, `MELVAN_OCR_ENDPOINT` | Photo text extraction provider |
| `MELVAN_OCR_API_VERSION` | Azure Document Intelligence API version |

## Persistence

| Variable | Purpose |
| --- | --- |
| `MELVAN_DATABASE` | Local SQLite file |
| `MELVAN_PERSISTENT_DATABASE` | Hosted-demo durable SQLite snapshot |
| `MELVAN_DATABASE_URL` | Production PostgreSQL application URL |
| `MELVAN_MIGRATION_DATABASE_URL` | PostgreSQL migration-owner URL |
| `MELVAN_ATTACHMENT_BACKEND` | `local` or `azure_blob` |
| `MELVAN_ATTACHMENT_ROOT` | Local attachment directory |
| `MELVAN_BLOB_ACCOUNT_URL` | Managed-identity Blob account URL |
| `MELVAN_BLOB_CONTAINER` | Private attachment container |
| `MELVAN_BLOB_CONNECTION_STRING` | Emulator-only connection string |

## Azure deployment variables

Every fork supplies these values. The deployment must not fall back to the
original project's account or resource names.

| Variable | Required | Purpose |
| --- | --- | --- |
| `MELVAN_DEPLOYMENT_ENVIRONMENT` | Yes | Repository-level name of the protected GitHub environment |
| `AZURE_CLIENT_ID` | Yes | GitHub OIDC application/client ID |
| `AZURE_TENANT_ID` | Yes | Fork owner's tenant |
| `AZURE_SUBSCRIPTION_ID` | Yes | Target subscription |
| `MELVAN_RESOURCE_GROUP` | Yes | Target resource group |
| `MELVAN_LOCATION` | Yes | Target Azure region |
| `MELVAN_APP_NAME` | Yes | Short deployment name and resource prefix |
| `MELVAN_FOUNDRY_RESOURCE_GROUP` | Yes | Existing Foundry resource group |
| `MELVAN_FOUNDRY_ACCOUNT` | Yes | Existing Foundry account |
| `MELVAN_FOUNDRY_PROJECT_ENDPOINT` | Yes | Foundry project endpoint |
| `MELVAN_MODEL` | Yes | Foundry model deployment |
| `MELVAN_COMMUNICATION_RESOURCE_GROUP` | Yes | Communication Services resource group |
| `MELVAN_COMMUNICATION_RESOURCE` | Yes | Communication Services resource |
| `MELVAN_EMAIL_ENDPOINT` | Yes | Email service endpoint |
| `MELVAN_EMAIL_SENDER` | Yes | Verified sender |
| `MELVAN_INITIAL_ADMIN_EMAIL` | Yes | Initial Owner email |
| `MELVAN_CUSTOM_DOMAIN` | No | Existing verified custom hostname |

Run the deployment preflight before provisioning:

```bash
PYTHONPATH=src python -m melvan.preflight --profile local --azure-deployment
```

The command reports missing names without printing secret values.
