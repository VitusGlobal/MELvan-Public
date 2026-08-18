from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AzureDeploymentContractTests(unittest.TestCase):
    def test_production_image_contains_database_migrations(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY pyproject.toml README.md alembic.ini ./", dockerfile)
        self.assertIn("COPY migrations ./migrations", dockerfile)

    def test_fork_operator_guides_define_the_support_boundary(self) -> None:
        guide = ROOT / "docs/azure-deployment.md"
        self.assertTrue(guide.is_file())
        content = guide.read_text()
        self.assertIn("Azure is MELvan's only maintained cloud platform", content)
        self.assertIn(
            "not a prescribed or certified production topology",
            " ".join(content.split()),
        )
        self.assertIn("Fork operators are responsible", content)
        self.assertIn(
            "They do not cover ongoing operations",
            " ".join(content.split()),
        )

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
