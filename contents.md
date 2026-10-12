# Repository contents

This map describes the source-controlled files in the MELvan repository.
Generated virtual environments, bytecode, build products, runtime databases,
attachments, and local configuration are intentionally excluded.

## Top level

- [`.dockerignore`](.dockerignore) - Excludes local and generated files from Docker build contexts.
- [`.env.example`](.env.example) - Documents environment variables without containing live values.
- [`.gitignore`](.gitignore) - Prevents local configuration, runtime data, dependencies, and build products from entering Git.
- [`ASSET-LICENSES.md`](ASSET-LICENSES.md) - Records the license and attribution terms for bundled project assets.
- [`CHANGELOG.md`](CHANGELOG.md) - Summarizes released application changes by version.
- [`CITATION.cff`](CITATION.cff) - Provides machine-readable project citation metadata.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) - Defines expected community behavior and confidential reporting procedures.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) - Explains development expectations and contribution licensing.
- [`DOCUMENTATION-LICENSE.md`](DOCUMENTATION-LICENSE.md) - Applies CC BY-SA 4.0 to repository documentation.
- [`Dockerfile`](Dockerfile) - Builds the deployable MELvan application image.
- [`Dockerfile.test`](Dockerfile.test) - Builds the PostgreSQL and Azurite integration-test image.
- [`LICENSE.md`](LICENSE.md) - Applies the MIT License to MELvan software.
- [`NOTICE.md`](NOTICE.md) - Records MELVan project attribution and citation guidance.
- [`README.md`](README.md) - Introduces MELvan, its support boundary, setup, tests, and documentation.
- [`SECURITY.md`](SECURITY.md) - Defines supported versions and private vulnerability reporting.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) - Identifies bundled third-party software and its licensing.
- [`alembic.ini`](alembic.ini) - Configures Alembic database migrations.
- [`compose.yaml`](compose.yaml) - Runs local PostgreSQL, Azurite, and their integration-test profile.
- [`contents.md`](contents.md) - Maps every source-controlled repository file to its purpose.
- [`package.json`](package.json) - Pins and exposes the browser acceptance-test toolchain.
- [`pyproject.toml`](pyproject.toml) - Defines the Python package, dependencies, version, and licensing metadata.

## `.github/`

### `workflows/`

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) - Runs publication, Python, PostgreSQL, and Azurite checks in GitHub Actions.
- [`.github/workflows/deploy-azure.yml`](.github/workflows/deploy-azure.yml) - Provides the manually triggered, fork-configured Azure deployment workflow.

## `docs/`

- [`docs/architecture.md`](docs/architecture.md) - Describes system boundaries, components, storage profiles, and responsibility ownership.
- [`docs/azure-deployment.md`](docs/azure-deployment.md) - Explains how a fork configures, deploys, and verifies the supplied Azure profile.
- [`docs/configuration.md`](docs/configuration.md) - Inventories application, provider, persistence, and deployment variables.
- [`docs/foundational-references.md`](docs/foundational-references.md) - Identifies MELvan's canonical sources, official links, authority hierarchy, and implementation role.
- [`docs/health-domain-safeguards.md`](docs/health-domain-safeguards.md) - Defines MELvan's canonical non-clinical boundary and health-domain safeguards.
- [`docs/principles.md`](docs/principles.md) - Records the product commitments that govern MELvan across all distributions and forks.
- [`docs/product-guide.md`](docs/product-guide.md) - Summarizes roles, records, and principal product workflows.
- [`docs/security-and-privacy.md`](docs/security-and-privacy.md) - Documents application security controls, data boundaries, and operator obligations.
- [`docs/testing.md`](docs/testing.md) - Describes deterministic, integration, browser, hosted, and publication verification.

## `infra/`

- [`infra/app.bicep`](infra/app.bicep) - Defines the Azure Container App, runtime configuration, identity, ingress, and storage mount.
- [`infra/foundation.bicep`](infra/foundation.bicep) - Defines the shared Azure resources used by the supplied deployment profile.
- [`infra/production-app.bicep`](infra/production-app.bicep) - Defines the stateless, multi-replica production Container App with restricted PostgreSQL and Blob access.
- [`infra/production-foundation.bicep`](infra/production-foundation.bicep) - Defines the PostgreSQL, Blob, identity, registry, Key Vault, and provider resources for the production profile.
- [`infra/production-migration-job.bicep`](infra/production-migration-job.bicep) - Defines the fail-closed Container Apps Job that applies schema migrations before web rollout.

## `migrations/`

- [`migrations/env.py`](migrations/env.py) - Connects Alembic to MELvan's PostgreSQL migration configuration.
- [`migrations/postgresql_initial.sql`](migrations/postgresql_initial.sql) - Provides the generated baseline PostgreSQL schema used by migration tooling and tests.
- [`migrations/script.py.mako`](migrations/script.py.mako) - Templates newly generated Alembic revision files.

### `versions/`

- [`migrations/versions/0001_postgresql_initial.py`](migrations/versions/0001_postgresql_initial.py) - Creates and removes the initial PostgreSQL schema.
- [`migrations/versions/0002_postgresql_workspace_isolation.py`](migrations/versions/0002_postgresql_workspace_isolation.py) - Adds workspace ownership constraints, indexes, and row-level isolation.
- [`migrations/versions/0003_password_setup_purpose.py`](migrations/versions/0003_password_setup_purpose.py) - Distinguishes initial password setup from password recovery across restricted sessions.

## `scripts/`

- [`scripts/check-publication.sh`](scripts/check-publication.sh) - Blocks publication when required files, licensing, privacy, or documentation checks fail.
- [`scripts/check_docs.py`](scripts/check_docs.py) - Validates local links in Markdown documentation.
- [`scripts/deploy-azure.sh`](scripts/deploy-azure.sh) - Validates fork configuration and deploys the supplied Azure profile.
- [`scripts/deploy-azure-production.sh`](scripts/deploy-azure-production.sh) - Provisions and verifies the PostgreSQL/Blob production deployment in migration-first order.
- [`scripts/generate-postgres-initial.py`](scripts/generate-postgres-initial.py) - Regenerates the baseline PostgreSQL SQL from the migration source.
- [`scripts/start.sh`](scripts/start.sh) - Creates or reuses a stable local Python environment and starts MELvan.

## `src/melvan/`

- [`src/melvan/__init__.py`](src/melvan/__init__.py) - Exposes package identity and the authoritative application version.
- [`src/melvan/attachment_migration.py`](src/melvan/attachment_migration.py) - Inventories, copies, resumes, and reconciles attachment-store migrations.
- [`src/melvan/auth.py`](src/melvan/auth.py) - Implements email delivery and revocable server-side sessions.
- [`src/melvan/bootstrap_owner.py`](src/melvan/bootstrap_owner.py) - Provides the trusted command for establishing the initial Owner password.
- [`src/melvan/config.py`](src/melvan/config.py) - Loads environment configuration into validated application settings.
- [`src/melvan/db.py`](src/melvan/db.py) - Implements the SQLite repository and local schema initialization.
- [`src/melvan/dev.py`](src/melvan/dev.py) - Runs local preflight, optional authentication refresh, reset, and development startup.
- [`src/melvan/identity.py`](src/melvan/identity.py) - Implements users, roles, workspace membership, passwords, recovery, lockout, and login auditing.
- [`src/melvan/learning.py`](src/melvan/learning.py) - Stores and retrieves bounded Reviewer-confirmed learning precedents.
- [`src/melvan/media.py`](src/melvan/media.py) - Implements speech, OCR, image normalization, and attachment-storage adapters.
- [`src/melvan/migrate.py`](src/melvan/migrate.py) - Provides the command-line entry point for PostgreSQL schema migrations.
- [`src/melvan/model.py`](src/melvan/model.py) - Implements model adapters, prompts, structured analysis contracts, and output validation.
- [`src/melvan/pdf_export.py`](src/melvan/pdf_export.py) - Produces workspace-scoped Evidence PDF exports with attachments.
- [`src/melvan/preflight.py`](src/melvan/preflight.py) - Validates runtime and deployment configuration before startup or provisioning.
- [`src/melvan/production.py`](src/melvan/production.py) - Creates the hosted WSGI application and production HTTP lifecycle.
- [`src/melvan/relational.py`](src/melvan/relational.py) - Implements the SQLAlchemy/PostgreSQL database boundary and transaction context.
- [`src/melvan/schema_migrations.py`](src/melvan/schema_migrations.py) - Runs migrations, grants runtime access, and checks schema revision state.
- [`src/melvan/service.py`](src/melvan/service.py) - Implements MELvan's domain workflows, authorization, review, Evidence, scoring, and Logic Models.
- [`src/melvan/web.py`](src/melvan/web.py) - Exposes the HTTP API and serves the browser application.

### `fixtures/`

- [`src/melvan/fixtures/logic_model.json`](src/melvan/fixtures/logic_model.json) - Supplies the synthetic demonstration Logic Model.

### `static/`

- [`src/melvan/static/app.js`](src/melvan/static/app.js) - Implements browser state, API calls, role views, capture, review, and Logic Model interactions.
- [`src/melvan/static/index.html`](src/melvan/static/index.html) - Defines the accessible application shell and interface controls.
- [`src/melvan/static/styles.css`](src/melvan/static/styles.css) - Defines responsive layout, visual states, and component styling.

#### `assets/`

- [`src/melvan/static/assets/melvan-logo.png`](src/melvan/static/assets/melvan-logo.png) - Provides the licensed MELvan interface logo.

#### `vendor/`

- [`src/melvan/static/vendor/LUCIDE-LICENSE`](src/melvan/static/vendor/LUCIDE-LICENSE) - Retains the license for the bundled Lucide icon library.
- [`src/melvan/static/vendor/lucide.min.js`](src/melvan/static/vendor/lucide.min.js) - Provides the vendored browser icon runtime.

## `tests/`

- [`tests/__init__.py`](tests/__init__.py) - Marks the test directory as an importable Python package.
- [`tests/test_attachment_migration.py`](tests/test_attachment_migration.py) - Tests attachment inventory, resumability, reconciliation, and Azurite integration.
- [`tests/test_db.py`](tests/test_db.py) - Tests SQLite initialization, persistence, reset, and compatibility migrations.
- [`tests/test_deployment_contract.py`](tests/test_deployment_contract.py) - Tests fork-owned Azure configuration and documented deployment boundaries.
- [`tests/test_dev.py`](tests/test_dev.py) - Tests development startup and model deployment probing.
- [`tests/test_frontend.py`](tests/test_frontend.py) - Tests static interface contracts, labels, controls, and responsive guardrails.
- [`tests/test_http.py`](tests/test_http.py) - Tests authenticated HTTP journeys, role enforcement, capture, review, and export routes.
- [`tests/test_identity.py`](tests/test_identity.py) - Tests identity lifecycle, passwords, recovery, lockout, roles, and workspace suspension.
- [`tests/test_learning.py`](tests/test_learning.py) - Tests learning-example governance, retrieval, scoring, and immutable session history.
- [`tests/test_media.py`](tests/test_media.py) - Tests image processing, attachment stores, speech transcription, and OCR adapters.
- [`tests/test_model.py`](tests/test_model.py) - Tests structured model validation, matching rules, confidence, and provider behavior.
- [`tests/test_pdf_export.py`](tests/test_pdf_export.py) - Tests Evidence PDF content, ordering, formatting, and attachment handling.
- [`tests/test_preflight.py`](tests/test_preflight.py) - Tests local and Azure configuration readiness checks.
- [`tests/test_production.py`](tests/test_production.py) - Tests hosted application startup, readiness, and shutdown behavior.
- [`tests/test_relational.py`](tests/test_relational.py) - Tests PostgreSQL translation, migrations, transaction behavior, and concurrency boundaries.
- [`tests/test_service.py`](tests/test_service.py) - Tests core domain rules for Statements, Evidence, review, mappings, and Logic Model changes.
- [`tests/test_version.py`](tests/test_version.py) - Ensures package and project versions remain synchronized.
- [`tests/test_workspace_isolation.py`](tests/test_workspace_isolation.py) - Tests Owner, Administrator, user, Statement, and review isolation across workspaces.
- [`tests/ui-smoke.cjs`](tests/ui-smoke.cjs) - Runs desktop and narrow-viewport Playwright acceptance journeys.
