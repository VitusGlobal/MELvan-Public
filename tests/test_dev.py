from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from melvan.config import Settings
from melvan.dev import main
from melvan.model import ModelError


class StartupModel:
    mode = "foundry"

    def __init__(self, *, fail_connectivity: bool = False):
        self.events: list[str] = []
        self.fail_connectivity = fail_connectivity

    def refresh_auth(self) -> None:
        self.events.append("auth")

    def verify_connectivity(self) -> None:
        self.events.append("connectivity")
        if self.fail_connectivity:
            raise ModelError("deployment connectivity check failed")

    def health_check(self) -> dict[str, str]:
        self.events.append("health")
        return {"mode": self.mode, "model": "gpt-5-mini-test", "status": "ready"}


class DevelopmentStartupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            project_root=Path(self.tempdir.name),
            database_path=Path(self.tempdir.name) / "melvan.sqlite3",
            host="127.0.0.1",
            port=8765,
            model_mode="foundry",
            model_name="gpt-5-mini-test",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            auth_refresh_command=None,
            azure_tenant_id="test-tenant",
            initial_admin_email="owner@example.org",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_refresh_auth_probes_deployment_before_startup_succeeds(self) -> None:
        model = StartupModel()
        with (
            patch("melvan.dev.Settings.from_env", return_value=self.settings),
            patch("melvan.dev.build_model_adapter", return_value=model),
            redirect_stdout(io.StringIO()),
        ):
            result = main(["--refresh-auth", "--check"])
        self.assertEqual(0, result)
        self.assertEqual(["auth", "connectivity", "health"], model.events)

    def test_failed_deployment_probe_blocks_startup(self) -> None:
        model = StartupModel(fail_connectivity=True)
        error = io.StringIO()
        with (
            patch("melvan.dev.Settings.from_env", return_value=self.settings),
            patch("melvan.dev.build_model_adapter", return_value=model),
            redirect_stderr(error),
        ):
            result = main(["--refresh-auth", "--check"])
        self.assertEqual(1, result)
        self.assertEqual(["auth", "connectivity"], model.events)
        self.assertIn("deployment connectivity check failed", error.getvalue())
        self.assertIn("Recovery:", error.getvalue())


if __name__ == "__main__":
    unittest.main()
