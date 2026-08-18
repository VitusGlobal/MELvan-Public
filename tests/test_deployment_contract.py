from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AzureDeploymentContractTests(unittest.TestCase):
    def test_production_image_contains_database_migrations(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY pyproject.toml README.md alembic.ini ./", dockerfile)
        self.assertIn("COPY migrations ./migrations", dockerfile)
        self.assertNotIn("MELVAN_DATABASE=/tmp", dockerfile)

    def test_production_topology_is_postgresql_blob_and_stateless(self) -> None:
        foundation = (ROOT / "infra/production-foundation.bicep").read_text()
        migration = (ROOT / "infra/production-migration-job.bicep").read_text()
        app = (ROOT / "infra/production-app.bicep").read_text()

        self.assertIn("Microsoft.DBforPostgreSQL/flexibleServers", foundation)
        self.assertIn("blobServices/containers", foundation)
        self.assertIn("allowSharedKeyAccess: false", foundation)
        self.assertIn("melvan-postgres-migration-url", foundation)
        self.assertIn("melvan-postgres-runtime-url", foundation)
        self.assertIn("migrationIdentityName = '${appName}-migration'", foundation)
        self.assertIn("scope: migrationDatabaseSecret", foundation)
        self.assertNotIn("scope: keyVault\n  name: guid(keyVault.id, runtimeIdentity.id", foundation)
        self.assertIn("Storage Blob Data Contributor", (ROOT / "docs/azure-deployment.md").read_text())
        self.assertNotIn("fileServices/shares", foundation)
        self.assertNotIn("managedEnvironments/storages", foundation)

        self.assertIn("Microsoft.App/jobs", migration)
        self.assertIn("melvan.migrate', 'upgrade", migration)
        self.assertIn("MELVAN_MIGRATION_DATABASE_URL", migration)
        self.assertIn("MELVAN_DATABASE_URL", migration)
        self.assertIn("identityName string = '${appName}-migration'", migration)

        self.assertIn("MELVAN_DEPLOYMENT_PROFILE', value: 'production", app)
        self.assertIn("MELVAN_ATTACHMENT_BACKEND', value: 'azure_blob", app)
        self.assertIn("MELVAN_BLOB_ACCOUNT_URL", app)
        self.assertIn("minReplicas: minReplicas", app)
        self.assertNotIn("MELVAN_DATABASE',", app)
        self.assertNotIn("MELVAN_PERSISTENT_DATABASE", app)
        self.assertNotIn("AzureFile", app)
        self.assertNotIn("volumeMounts", app)

    def test_production_deployment_migrates_before_web_rollout(self) -> None:
        script = (ROOT / "scripts/deploy-azure-production.sh").read_text()
        workflow = (ROOT / ".github/workflows/deploy-azure.yml").read_text()

        self.assertIn("deployment_profile:", workflow)
        self.assertIn("- production", workflow)
        self.assertIn("./scripts/deploy-azure-production.sh", workflow)
        self.assertIn("MELVAN_POSTGRES_SKU", workflow)
        self.assertLess(
            script.index("production-foundation.bicep"),
            script.index("production-migration-job.bicep"),
        )
        self.assertLess(
            script.index("containerapp job start"),
            script.index("production-app.bicep"),
        )
        self.assertIn("The web application was not deployed", script)
        self.assertIn("MELVAN_MIN_REPLICAS >= 2", script)

    def test_migration_provisions_restricted_runtime_role(self) -> None:
        command = (ROOT / "src/melvan/migrate.py").read_text()
        migrations = (ROOT / "src/melvan/schema_migrations.py").read_text()

        self.assertIn("ensure_runtime_role(migration_url, settings.database_url)", command)
        self.assertIn("CREATE ROLE", migrations)
        self.assertIn("ALTER ROLE", migrations)
        self.assertIn("NOSUPERUSER", migrations)
        self.assertIn("NOBYPASSRLS", migrations)

    def test_fork_operator_guides_define_the_support_boundary(self) -> None:
        guide = ROOT / "docs/azure-deployment.md"
        self.assertTrue(guide.is_file())
        content = guide.read_text()
        self.assertIn("Azure is MELvan's only maintained cloud platform", content)
        self.assertIn(
            "not a prescribed or certified production topology",
            " ".join(content.split()),
        )
        self.assertIn("Fork operators remain responsible", content)
        self.assertIn(
            "They do not cover ongoing operations",
            " ".join(content.split()),
        )
        self.assertIn("PostgreSQL-only deployment", content)
        self.assertIn("production deployment workflow", content)

        architecture = (ROOT / "docs/architecture.md").read_text()
        self.assertIn("MELvan maintainers are responsible", architecture)
        self.assertIn("Each fork operator is responsible", architecture)

    def test_deployment_requires_fork_owned_resource_coordinates(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-azure.yml").read_text()
        script = (ROOT / "scripts/deploy-azure.sh").read_text()
        foundation = (ROOT / "infra/foundation.bicep").read_text()

        for name in ("MELVAN_RESOURCE_GROUP", "MELVAN_LOCATION", "MELVAN_APP_NAME"):
            self.assertIn(f"{name}: ${{{{ vars.{name} }}}}", workflow)
            self.assertIn(name, script)
        self.assertNotIn("rg-melvan-demo", script + workflow + foundation)
        self.assertNotIn("canadacentral", script + workflow + foundation)
        self.assertIn("param appName string\n", foundation)
        self.assertIn("param location string\n", foundation)

    def test_cloud_deployment_is_manual(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-azure.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("branches: [main]", workflow)

    def test_custom_domain_is_an_explicit_deployment_input(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-azure.yml").read_text()
        script = (ROOT / "scripts/deploy-azure.sh").read_text()

        self.assertIn("MELVAN_CUSTOM_DOMAIN: ${{ vars.MELVAN_CUSTOM_DOMAIN }}", workflow)
        self.assertIn('CUSTOM_DOMAIN_NAME="${MELVAN_CUSTOM_DOMAIN:-}"', script)
        self.assertIn("containerapp env certificate list", script)
        self.assertIn('customDomainName="$CUSTOM_DOMAIN_NAME"', script)
        self.assertIn(
            'customDomainCertificateId="$CUSTOM_DOMAIN_CERTIFICATE_ID"', script
        )

    def test_app_template_preserves_tls_binding_and_canonical_url(self) -> None:
        template = (ROOT / "infra/app.bicep").read_text()

        self.assertIn("param customDomainName string = ''", template)
        self.assertIn("param customDomainCertificateId string = ''", template)
        self.assertIn("customDomains: hasCustomDomain ?", template)
        self.assertIn("bindingType: 'SniEnabled'", template)
        self.assertIn("certificateId: customDomainCertificateId", template)
        self.assertIn("? 'https://${customDomainName}'", template)

    def test_deployment_waits_for_revision_and_public_version(self) -> None:
        script = (ROOT / "scripts/deploy-azure.sh").read_text()

        self.assertIn("properties.latestRevisionName", script)
        self.assertIn("properties.runningState", script)
        self.assertIn("ActivationFailed|Degraded|Failed", script)
        self.assertIn('"$PUBLIC_URL/api/health"', script)
        self.assertIn('from melvan import __version__', script)
        self.assertIn("did not become healthy", script)
        self.assertIn("did not report MELvan", script)

    def test_hosted_speech_uses_managed_identity_and_no_keys(self) -> None:
        foundation = (ROOT / "infra/foundation.bicep").read_text()
        app = (ROOT / "infra/app.bicep").read_text()
        script = (ROOT / "scripts/deploy-azure.sh").read_text()

        self.assertIn("kind: 'SpeechServices'", foundation)
        self.assertIn("sku: { name: 'S0' }", foundation)
        self.assertIn("customSubDomainName: speechName", foundation)
        self.assertIn("disableLocalAuth: true", foundation)
        self.assertIn("output speechEndpoint string", foundation)
        self.assertIn("'MELVAN_SPEECH_MODE', value: 'azure'", app)
        self.assertIn("'MELVAN_SPEECH_ENDPOINT', value: speechEndpoint", app)
        self.assertIn('"Cognitive Services Speech User"', script)
        self.assertNotIn("MELVAN_SPEECH_KEY", foundation + app + script)

    def test_hosted_ocr_uses_document_intelligence_managed_identity_and_no_keys(
        self,
    ) -> None:
        foundation = (ROOT / "infra/foundation.bicep").read_text()
        app = (ROOT / "infra/app.bicep").read_text()
        script = (ROOT / "scripts/deploy-azure.sh").read_text()

        self.assertIn("kind: 'FormRecognizer'", foundation)
        self.assertIn("sku: { name: 'S0' }", foundation)
        self.assertIn(
            "customSubDomainName: documentIntelligenceName",
            foundation,
        )
        self.assertIn("disableLocalAuth: true", foundation)
        self.assertIn("output documentIntelligenceEndpoint string", foundation)
        self.assertIn("'MELVAN_OCR_MODE', value: 'azure'", app)
        self.assertIn(
            "'MELVAN_OCR_ENDPOINT', value: documentIntelligenceEndpoint",
            app,
        )
        self.assertIn('"Cognitive Services User"', script)
        self.assertNotIn("MELVAN_OCR_KEY", foundation + app + script)


if __name__ == "__main__":
    unittest.main()
