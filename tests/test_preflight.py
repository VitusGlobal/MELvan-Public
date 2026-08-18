from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from melvan.preflight import check, main


class PreflightTestCase(unittest.TestCase):
    def test_local_profile_is_secret_free(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(check("local").ready)

    def test_production_reports_names_without_values(self) -> None:
        secret = "postgresql+psycopg://user:do-not-print@db/melvan"
        with patch.dict(
            "os.environ",
            {"MELVAN_DATABASE_URL": secret},
            clear=True,
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--profile", "production", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(2, status)
        self.assertFalse(payload["ready"])
        self.assertIn("MELVAN_AUTH_PEPPER", payload["missing"])
        self.assertNotIn(secret, output.getvalue())

    def test_azure_deployment_requires_fork_owned_identity(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = check("local", azure_deployment=True)
        self.assertIn("AZURE_CLIENT_ID", result.missing)
        self.assertIn("AZURE_TENANT_ID", result.missing)
        self.assertIn("AZURE_SUBSCRIPTION_ID", result.missing)
        self.assertIn("MELVAN_RESOURCE_GROUP", result.missing)
        self.assertIn("MELVAN_LOCATION", result.missing)
        self.assertIn("MELVAN_APP_NAME", result.missing)


if __name__ == "__main__":
    unittest.main()
