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

Persistence profiles are mutually exclusive. An installation selects exactly
one profile; MELvan does not run SQLite alongside PostgreSQL.

| Profile | Relational store | Attachment store | Active variables |
| --- | --- | --- | --- |
| `local` | One SQLite file | Local directory | `MELVAN_DATABASE`, `MELVAN_ATTACHMENT_BACKEND=local`, `MELVAN_ATTACHMENT_ROOT` |
| `hosted_demo` | Runtime SQLite file plus durable SQLite snapshot | Azure Files-compatible local directory | `MELVAN_DATABASE`, `MELVAN_PERSISTENT_DATABASE`, `MELVAN_ATTACHMENT_BACKEND=local`, `MELVAN_ATTACHMENT_ROOT` |
| `production` | PostgreSQL only | Private Azure Blob Storage | `MELVAN_DATABASE_URL`, `MELVAN_MIGRATION_DATABASE_URL`, `MELVAN_ATTACHMENT_BACKEND=azure_blob`, `MELVAN_BLOB_ACCOUNT_URL`, `MELVAN_BLOB_CONTAINER` |

In the `production` profile, MELvan constructs the PostgreSQL adapter and does
not create or open a SQLite database. `MELVAN_DATABASE` and
`MELVAN_ATTACHMENT_ROOT` are not used, and `MELVAN_PERSISTENT_DATABASE` is
rejected. `MELVAN_BLOB_CONNECTION_STRING` is reserved for local Azurite testing;
hosted Azure deployments should use managed identity and
`MELVAN_BLOB_ACCOUNT_URL`.

### PostgreSQL-only configuration

An installation that wants only the production-ready persistence path uses:

```text
MELVAN_ENVIRONMENT=hosted
MELVAN_DEPLOYMENT_PROFILE=production
MELVAN_DATABASE_URL=postgresql+psycopg://melvan_app:...@postgres:5432/melvan
MELVAN_MIGRATION_DATABASE_URL=postgresql+psycopg://melvan_migrator:...@postgres:5432/melvan
MELVAN_ATTACHMENT_BACKEND=azure_blob
MELVAN_BLOB_ACCOUNT_URL=https://your-storage-account.blob.core.windows.net
MELVAN_BLOB_CONTAINER=melvan-attachments
```

The migration URL belongs in the trusted migration environment, not the
ordinary application runtime. Run the database migrations before starting or
upgrading application replicas. The hosted authentication, provider, email,
speech, OCR, and Owner variables remain required independently of the selected
persistence profile.

The supplied production Azure deployment provisions PostgreSQL and Blob
Storage, runs migrations before web rollout, and injects only the runtime
database URL into the application. Operators can also supply equivalent
infrastructure while preserving those credential and migration boundaries. See
[Azure Deployment](azure-deployment.md#postgresql-only-deployment).

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
| `MELVAN_POSTGRES_SKU` | No | Production PostgreSQL SKU; defaults to `Standard_B1ms` |
| `MELVAN_POSTGRES_TIER` | No | Production PostgreSQL tier; defaults to `Burstable` |
| `MELVAN_POSTGRES_VERSION` | No | Production PostgreSQL major version; defaults to `16` |
| `MELVAN_POSTGRES_STORAGE_GB` | No | Production PostgreSQL storage in GB; defaults to `32` |
| `MELVAN_MIN_REPLICAS` | No | Production minimum web replicas; defaults to `2` and cannot be lower |
| `MELVAN_MAX_REPLICAS` | No | Production maximum web replicas; defaults to `4` |

Run the deployment preflight before provisioning:

```bash
PYTHONPATH=src python -m melvan.preflight --profile local --azure-deployment
```

The command reports missing names without printing secret values.
