# Azure deployment

## Current support boundary

Azure is MELvan's only maintained cloud platform. The checked-in template
deploys the `hosted_demo` profile: one always-running Container Apps replica,
SQLite runtime storage with a durable Azure Files snapshot, managed identity,
Key Vault, Azure AI Foundry, Communication Services, Speech, and Document
Intelligence.

This profile provides a stable demonstration. It is not a prescribed or
certified production topology. Fork operators are responsible for deciding and
validating whether to use PostgreSQL, Blob Storage, additional replicas, or
other infrastructure for their operational requirements. See
[Architecture](architecture.md) for the responsibility boundary.

These instructions cover configuration and deployment of the supplied Azure
profile. They do not cover ongoing operations, capacity planning, monitoring,
incident response, backup, disaster recovery, compliance, or migration of an
existing installation.

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
7. Run the preflight and manually start the deployment workflow.

Forks inherit workflow definitions only. They do not inherit environments,
variables, secrets, approvals, identities, domains, or Azure resources.

## Deployment

Authenticate locally with Azure CLI or use the manually triggered GitHub OIDC
workflow, then run:

```bash
./scripts/deploy-azure.sh
```

The script requires `MELVAN_RESOURCE_GROUP`, `MELVAN_LOCATION`, and
`MELVAN_APP_NAME`; it has no original-environment fallback. Other required
integration values are validated before provisioning starts.

The deployment creates a stable authentication pepper and stores it in Key
Vault, builds the container in Azure Container Registry, assigns the runtime
managed identity, deploys the application, and verifies the public health
endpoint reports the expected application version.

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
