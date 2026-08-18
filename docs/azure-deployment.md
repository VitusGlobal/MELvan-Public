# Azure deployment

## Current support boundary

Azure is MELvan's only maintained cloud platform. The repository supplies two
separate deployment paths:

- `hosted_demo` deploys one always-running Container Apps replica with SQLite
  runtime storage and a durable Azure Files snapshot.
- `production` deploys multiple stateless Container Apps replicas with Azure
  Database for PostgreSQL Flexible Server and private Azure Blob Storage.

Both paths use managed identity, Key Vault, Azure AI Foundry, Communication
Services, Speech, and Document Intelligence.

The supplied production topology is a maintained deployment baseline, not a
prescribed or certified production topology. Fork operators remain responsible
for sizing, hardening, operating, and validating it for their requirements. See
[Architecture](architecture.md) for the responsibility boundary.

These instructions cover configuration and deployment of the supplied Azure
profile. They do not cover ongoing operations, capacity planning, monitoring,
incident response, backup, disaster recovery, compliance, or migration of an
existing installation.

## PostgreSQL-only deployment

The MELvan application can run without SQLite by selecting the `production`
profile. In that profile, PostgreSQL is the only relational store and private
Azure Blob Storage is the attachment store. SQLite is neither created nor used.

The production deployment workflow and its Bicep templates:

1. Provision PostgreSQL Flexible Server and a private Blob container.
2. Store separate migration-owner and restricted runtime database URLs in Key
   Vault and use distinct managed identities for migration and web execution.
3. Run the included migrations in a one-off Container Apps Job. The migration
   also creates or rotates the constrained runtime role and grants its schema
   access.
4. Stop before deploying the web application if that migration job fails.
5. Give the web replicas only the runtime database URL and the managed identity
   used for Blob Storage, model, email, speech, and OCR access.
6. Deploy at least two stateless web replicas with no SQLite or Azure Files
   volume.

The runtime identity has the built-in **Storage Blob Data Contributor** role on
the attachment account and can read only its authentication-pepper and runtime
database secrets. The migration identity can read the migration and runtime
database secrets but has no Blob or model-provider access. Blob shared-key
access is disabled. PostgreSQL credentials remain in Key Vault because the
current database adapter uses password authentication; the web identity cannot
read the migration-owner secret.

The baseline permits connections from Azure services to PostgreSQL. Operators
with stricter network requirements can replace that rule with private
networking while preserving the database-role and migration boundaries. The
exact application variables are listed under [PostgreSQL-only
configuration](configuration.md#postgresql-only-configuration).

## Fork setup

1. Create a GitHub environment for deployment and protect it as appropriate.
   Store its name in the repository variable `MELVAN_DEPLOYMENT_ENVIRONMENT`.
2. Register an Entra application for GitHub OIDC.
3. Add a federated credential for your repository, branch or tag policy, and
   GitHub environment.
4. Grant only the Azure deployment and role-assignment permissions required by
   the Bicep templates.
5. Configure every other deployment value as a variable in that environment,
   using the inventory in [Configuration](configuration.md).
6. Register the Azure resource providers used by Container Apps, Container
   Registry, Key Vault, Managed Identity, Log Analytics, Storage, Cognitive
   Services, and Communication Services.
7. Run the preflight and manually start the deployment workflow, choosing
   `hosted_demo` or `production` explicitly.

Forks inherit workflow definitions only. They do not inherit environments,
variables, secrets, approvals, identities, domains, or Azure resources.

## Deployment

Use the manually triggered GitHub OIDC workflow and choose the required
`deployment_profile`. To deploy from an authenticated Azure CLI session, run
one of:

```bash
# Stable single-replica demonstration
./scripts/deploy-azure.sh

# PostgreSQL, Blob Storage, migrations, and multiple stateless replicas
./scripts/deploy-azure-production.sh
```

The script requires `MELVAN_RESOURCE_GROUP`, `MELVAN_LOCATION`, and
`MELVAN_APP_NAME`; it has no original-environment fallback. Other required
integration values are validated before provisioning starts.

Both deployments create a stable authentication pepper in Key Vault, build the
container in Azure Container Registry, assign the runtime managed identity,
deploy the application, and verify that the public health endpoint reports the
expected application version. The production path additionally creates the
PostgreSQL and Blob resources and completes its migration job before rolling
out the web application.

## Initial Owner

Set `MELVAN_INITIAL_ADMIN_EMAIL` before the first deployment. From a trusted
operator environment configured for the deployed data store, run:

```bash
PYTHONPATH=src .venv/bin/python -m melvan.bootstrap_owner
```

Record the temporary password securely and replace it at first login. Never put
temporary or permanent passwords in scripts, logs, tickets, or repository
files.

## Deployment verification

- `/api/live` confirms that the application process is running.
- `/api/ready` checks required runtime dependencies.
- `/api/health` reports non-secret application and version status.

The deployment workflow verifies the public health response. Fork operators
should also complete the hosted checks in [Testing](testing.md) using synthetic
data before admitting real program information.

## Custom domains

Custom domains are optional. Create and validate the DNS record and managed
certificate before setting `MELVAN_CUSTOM_DOMAIN`. A fork can otherwise use the
Azure-generated HTTPS hostname.

## Cost and teardown

Azure services can incur charges even when the application is idle. Configure a
budget and alerts in the target subscription. To decommission an isolated
installation, retain required exports and backups, then remove its resource
group using normal Azure controls.
