"""Runtime configuration loaded without third-party dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    host: str
    port: int
    model_mode: str
    model_name: str
    openai_api_key: str | None
    openai_base_url: str
    auth_refresh_command: str | None
    foundry_project_endpoint: str = ""
    azure_tenant_id: str | None = None
    azure_auth_mode: str = "browser"
    azure_managed_identity_client_id: str | None = None
    azure_token_scope: str = "https://ai.azure.com/.default"
    auth_cache_name: str = "melvan-local-auth"
    auth_timeout_seconds: int = 300
    environment: str = "development"
    auth_pepper: str | None = None
    initial_admin_email: str | None = None
    email_mode: str = "console"
    email_endpoint: str | None = None
    email_sender: str | None = None
    public_base_url: str | None = None
    persistent_database_path: Path | None = None
    users_path: Path | None = None
    attachment_root: Path | None = None
    speech_mode: str = "demo"
    speech_endpoint: str | None = None
    speech_language: str = "en-US"
    speech_token_scope: str = "https://cognitiveservices.azure.com/.default"
    ocr_mode: str = "model"
    ocr_endpoint: str | None = None
    ocr_api_version: str = "2024-11-30"
    ocr_token_scope: str = "https://cognitiveservices.azure.com/.default"
    deployment_profile: str = "hosted_demo"
    database_url: str | None = None
    attachment_backend: str = "local"
    blob_account_url: str | None = None
    blob_container: str = "melvan-attachments"
    blob_connection_string: str | None = None

    @classmethod
    def from_env(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        model_mode: str | None = None,
    ) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        selected_mode = (model_mode or os.getenv("MELVAN_MODEL_MODE", "foundry")).lower()
        if selected_mode not in {"demo", "foundry", "openai"}:
            raise ValueError("MELVAN_MODEL_MODE must be 'demo', 'foundry', or 'openai'.")
        default_model = "gpt-5-mini"
        environment = os.getenv("MELVAN_ENVIRONMENT", "development").strip().lower()
        if environment not in {"development", "test", "hosted"}:
            raise ValueError("MELVAN_ENVIRONMENT must be development, test, or hosted.")
        default_profile = "hosted_demo" if environment == "hosted" else "local"
        deployment_profile = os.getenv(
            "MELVAN_DEPLOYMENT_PROFILE", default_profile
        ).strip().lower()
        if deployment_profile not in {"local", "hosted_demo", "production"}:
            raise ValueError(
                "MELVAN_DEPLOYMENT_PROFILE must be local, hosted_demo, or production."
            )
        attachment_backend = os.getenv(
            "MELVAN_ATTACHMENT_BACKEND",
            "azure_blob" if deployment_profile == "production" else "local",
        ).strip().lower()
        if attachment_backend not in {"local", "azure_blob"}:
            raise ValueError(
                "MELVAN_ATTACHMENT_BACKEND must be local or azure_blob."
            )
        email_mode = os.getenv("MELVAN_EMAIL_MODE", "console").strip().lower()
        if email_mode not in {"console", "azure"}:
            raise ValueError("MELVAN_EMAIL_MODE must be console or azure.")
        speech_mode = os.getenv("MELVAN_SPEECH_MODE", "demo").strip().lower()
        if speech_mode not in {"demo", "azure", "disabled"}:
            raise ValueError("MELVAN_SPEECH_MODE must be demo, azure, or disabled.")
        ocr_mode = os.getenv("MELVAN_OCR_MODE", "model").strip().lower()
        if ocr_mode not in {"model", "azure", "disabled"}:
            raise ValueError("MELVAN_OCR_MODE must be model, azure, or disabled.")
        return cls(
            project_root=PROJECT_ROOT,
            database_path=Path(
                os.getenv("MELVAN_DATABASE", str(PROJECT_ROOT / "data" / "melvan.sqlite3"))
            ),
            host=host or os.getenv("MELVAN_HOST", "127.0.0.1"),
            port=port or int(os.getenv("MELVAN_PORT", "8765")),
            model_mode=selected_mode,
            model_name=os.getenv("MELVAN_MODEL", default_model),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            auth_refresh_command=os.getenv("MELVAN_AUTH_REFRESH_COMMAND"),
            foundry_project_endpoint=os.getenv(
                "MELVAN_FOUNDRY_PROJECT_ENDPOINT", ""
            ).rstrip("/"),
            azure_tenant_id=os.getenv("AZURE_TENANT_ID"),
            azure_auth_mode=os.getenv("MELVAN_AUTH_MODE", "browser").strip().lower(),
            azure_managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
            azure_token_scope=os.getenv(
                "MELVAN_AZURE_SCOPE", "https://ai.azure.com/.default"
            ),
            auth_cache_name=os.getenv("MELVAN_AUTH_CACHE_NAME", "melvan-local-auth"),
            auth_timeout_seconds=int(os.getenv("MELVAN_AUTH_TIMEOUT_SECONDS", "300")),
            environment=environment,
            auth_pepper=os.getenv("MELVAN_AUTH_PEPPER"),
            initial_admin_email=(
                os.environ["MELVAN_INITIAL_ADMIN_EMAIL"].strip().lower()
                if os.getenv("MELVAN_INITIAL_ADMIN_EMAIL")
                else None
            ),
            email_mode=email_mode,
            email_endpoint=os.getenv("MELVAN_EMAIL_ENDPOINT"),
            email_sender=os.getenv("MELVAN_EMAIL_SENDER"),
            public_base_url=os.getenv("MELVAN_PUBLIC_BASE_URL"),
            persistent_database_path=(
                Path(os.environ["MELVAN_PERSISTENT_DATABASE"])
                if os.getenv("MELVAN_PERSISTENT_DATABASE")
                else None
            ),
            users_path=(
                Path(os.environ["MELVAN_USERS_FILE"])
                if os.getenv("MELVAN_USERS_FILE")
                else None
            ),
            attachment_root=Path(
                os.getenv(
                    "MELVAN_ATTACHMENT_ROOT",
                    str(PROJECT_ROOT / "data" / "attachments"),
                )
            ),
            speech_mode=speech_mode,
            speech_endpoint=os.getenv("MELVAN_SPEECH_ENDPOINT"),
            speech_language=os.getenv("MELVAN_SPEECH_LANGUAGE", "en-US"),
            speech_token_scope=os.getenv(
                "MELVAN_SPEECH_SCOPE",
                "https://cognitiveservices.azure.com/.default",
            ),
            ocr_mode=ocr_mode,
            ocr_endpoint=os.getenv("MELVAN_OCR_ENDPOINT"),
            ocr_api_version=os.getenv("MELVAN_OCR_API_VERSION", "2024-11-30"),
            ocr_token_scope=os.getenv(
                "MELVAN_OCR_SCOPE",
                "https://cognitiveservices.azure.com/.default",
            ),
            deployment_profile=deployment_profile,
            database_url=os.getenv("MELVAN_DATABASE_URL"),
            attachment_backend=attachment_backend,
            blob_account_url=os.getenv("MELVAN_BLOB_ACCOUNT_URL"),
            blob_container=os.getenv(
                "MELVAN_BLOB_CONTAINER", "melvan-attachments"
            ),
            blob_connection_string=os.getenv("MELVAN_BLOB_CONNECTION_STRING"),
        )

    def validate_hosted(self) -> None:
        if self.environment != "hosted":
            return
        if not self.auth_pepper or len(self.auth_pepper) < 32:
            raise ValueError("Hosted MELvan requires MELVAN_AUTH_PEPPER with at least 32 characters.")
        if not self.initial_admin_email:
            raise ValueError(
                "Hosted MELvan requires MELVAN_INITIAL_ADMIN_EMAIL for Owner bootstrap."
            )
        if (
            self.email_mode != "azure"
            or not self.email_endpoint
            or not self.email_endpoint.startswith("https://")
            or not self.email_sender
        ):
            raise ValueError(
                "Hosted MELvan requires Azure email mode, MELVAN_EMAIL_ENDPOINT, and MELVAN_EMAIL_SENDER."
            )
        if not self.public_base_url or not self.public_base_url.startswith("https://"):
            raise ValueError("Hosted MELvan requires an HTTPS MELVAN_PUBLIC_BASE_URL.")
        if (
            self.model_mode != "foundry"
            or not self.model_name
            or not self.foundry_project_endpoint.startswith("https://")
        ):
            raise ValueError("Hosted MELvan requires a configured Azure Foundry project endpoint.")
        if self.azure_auth_mode != "managed_identity":
            raise ValueError("Hosted MELvan requires managed-identity model authentication.")
        if self.deployment_profile == "hosted_demo":
            if not self.persistent_database_path:
                raise ValueError(
                    "The hosted_demo profile requires MELVAN_PERSISTENT_DATABASE."
                )
            if self.database_path == self.persistent_database_path:
                raise ValueError(
                    "The hosted_demo profile requires separate runtime and persistent "
                    "database paths."
                )
            if self.attachment_backend != "local":
                raise ValueError(
                    "The hosted_demo profile requires local attachment storage."
                )
        elif self.deployment_profile == "production":
            if (
                not self.database_url
                or not self.database_url.startswith(
                    ("postgresql://", "postgresql+psycopg://")
                )
            ):
                raise ValueError(
                    "The production profile requires a PostgreSQL MELVAN_DATABASE_URL."
                )
            if self.attachment_backend != "azure_blob":
                raise ValueError(
                    "The production profile requires MELVAN_ATTACHMENT_BACKEND=azure_blob."
                )
            if not self.blob_account_url and not self.blob_connection_string:
                raise ValueError(
                    "The production profile requires MELVAN_BLOB_ACCOUNT_URL "
                    "or MELVAN_BLOB_CONNECTION_STRING."
                )
            if self.persistent_database_path:
                raise ValueError(
                    "The production profile must not use MELVAN_PERSISTENT_DATABASE."
                )
        else:
            raise ValueError(
                "Hosted MELvan requires hosted_demo or production deployment profile."
            )
        if self.speech_mode != "azure":
            raise ValueError("Hosted MELvan requires Azure speech transcription.")
        if not self.speech_endpoint or not self.speech_endpoint.startswith("https://"):
            raise ValueError(
                "Azure speech mode requires an HTTPS MELVAN_SPEECH_ENDPOINT."
            )
        if self.ocr_mode != "azure":
            raise ValueError("Hosted MELvan requires Azure photo text extraction.")
        if not self.ocr_endpoint or not self.ocr_endpoint.startswith("https://"):
            raise ValueError(
                "Azure photo text extraction requires an HTTPS MELVAN_OCR_ENDPOINT."
            )
