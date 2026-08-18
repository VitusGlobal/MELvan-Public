from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from melvan.config import Settings
from melvan.production import create_application


class ProductionApplicationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(
            project_root=root,
            database_path=root / "melvan.sqlite3",
            host="0.0.0.0",
            port=8000,
            model_mode="foundry",
            model_name="test-gpt-5-mini",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            auth_refresh_command=None,
            foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            azure_auth_mode="managed_identity",
            environment="hosted",
            auth_pepper="hosted-test-pepper-value-with-more-than-32-characters",
            initial_admin_email="owner@example.org",
            email_mode="azure",
            email_endpoint="https://example.communication.azure.com",
            email_sender="DoNotReply@example.invalid",
            public_base_url="https://melvan.example.test",
            persistent_database_path=root / "persistent" / "melvan.sqlite3",
            users_path=root / "persistent" / "users.json",
            speech_mode="azure",
            speech_endpoint=(
                "https://melvan-speech.example.test/stt/speech/recognition/"
                "conversation/cognitiveservices/v1"
            ),
            ocr_mode="azure",
            ocr_endpoint="https://melvan-ocr.example.test",
        )
        self.application = create_application(self.settings)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def call(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        cookie: str | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict, dict[str, str]]:
        status, content, headers = self.call_raw(
            method,
            path,
            payload,
            cookie=cookie,
            origin=origin,
        )
        return status, json.loads(content), headers

    def call_raw(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        cookie: str | None = None,
        origin: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "HTTP_HOST": "melvan.example.test",
            "HTTP_X_FORWARDED_PROTO": "https",
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        if origin:
            environ["HTTP_ORIGIN"] = origin
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = headers

        content = b"".join(self.application(environ, start_response))
        headers = {key: value for key, value in captured["headers"]}
        return int(str(captured["status"]).split()[0]), content, headers

    def test_cookie_session_and_security_headers(self) -> None:
        temporary = self.application.api.sessions.bootstrap_owner()
        status, payload, headers = self.call(
            "POST",
            "/api/session",
            {
                "email": "owner@example.org",
                "password": temporary["temporary_password"],
                "duration": "7_days",
                "timezone": "UTC",
            },
            origin="https://melvan.example.test",
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["requires_password_change"])
        self.assertNotIn("token", payload)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual("DENY", headers["X-Frame-Options"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

        restricted_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, payload, headers = self.call(
            "POST",
            "/api/session/password",
            {
                "password": "OwnerStrong1!",
                "confirmation": "OwnerStrong1!",
                "user_guide_acknowledged": True,
            },
            cookie=restricted_cookie,
            origin="https://melvan.example.test",
        )
        self.assertEqual(200, status)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, session, _ = self.call("GET", "/api/session", cookie=cookie)
        self.assertEqual(200, status)
        self.assertEqual("owner@example.org", session["user"]["email"])

    def test_health_and_cross_origin_boundary(self) -> None:
        status, payload, headers = self.call("GET", "/api/live")
        self.assertEqual(200, status)
        self.assertEqual("alive", payload["status"])
        self.assertIn("max-age=31536000", headers["Strict-Transport-Security"])

        status, payload, _ = self.call(
            "POST",
            "/api/auth/recovery",
            {"email": "owner@example.org"},
            origin="https://other.example.test",
        )
        self.assertEqual(403, status)
        self.assertEqual("Cross-origin request denied.", payload["error"])

    def test_hosted_configuration_requires_owner_email(self) -> None:
        with self.assertRaisesRegex(ValueError, "MELVAN_INITIAL_ADMIN_EMAIL"):
            replace(self.settings, initial_admin_email=None).validate_hosted()

    def test_production_profile_requires_postgresql_and_blob_storage(self) -> None:
        production = replace(
            self.settings,
            deployment_profile="production",
            persistent_database_path=None,
            database_url=None,
            attachment_backend="local",
        )
        with self.assertRaisesRegex(ValueError, "PostgreSQL MELVAN_DATABASE_URL"):
            production.validate_hosted()

        production = replace(
            production,
            database_url="postgresql+psycopg://melvan:secret@database/melvan",
        )
        with self.assertRaisesRegex(ValueError, "azure_blob"):
            production.validate_hosted()

        production = replace(production, attachment_backend="azure_blob")
        with self.assertRaisesRegex(ValueError, "MELVAN_BLOB_ACCOUNT_URL"):
            production.validate_hosted()

        replace(
            production,
            blob_account_url="https://example.blob.core.windows.net",
        ).validate_hosted()

    def test_production_profile_selects_postgresql_adapter(self) -> None:
        production = replace(
            self.settings,
            deployment_profile="production",
            persistent_database_path=None,
            database_url="postgresql+psycopg://melvan:secret@database/melvan",
            attachment_backend="azure_blob",
            blob_account_url="https://example.blob.core.windows.net",
        )
        with patch("melvan.relational.PostgresDatabase") as postgres:
            from melvan.relational import build_database

            build_database(production)
        postgres.assert_called_once_with(production.database_url)

    def test_hosted_state_uses_local_sqlite_and_persistent_snapshots(self) -> None:
        self.assertTrue(self.settings.persistent_database_path.is_file())
        self.assertEqual(self.settings.users_path, self.application.api.service.users.path)
        self.assertNotEqual(
            self.application.api.service.database.path,
            self.application.api.service.database.snapshot_path,
        )

    def test_static_assets_are_not_reused_across_releases(self) -> None:
        status, stylesheet, headers = self.call_raw("GET", "/styles.css")
        self.assertEqual(200, status)
        self.assertIn(b".text-button {", stylesheet)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertTrue(headers["Content-Type"].startswith("text/css"))

        status, logo, headers = self.call_raw("GET", "/assets/melvan-logo.png")
        self.assertEqual(200, status)
        self.assertTrue(logo.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertTrue(headers["Content-Type"].startswith("image/png"))


if __name__ == "__main__":
    unittest.main()
