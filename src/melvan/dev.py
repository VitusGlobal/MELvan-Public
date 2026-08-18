"""MELvan development startup with credential preflight."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .auth import SessionStore, build_email_sender
from .config import Settings
from .db import Database
from .identity import IdentityError
from .media import (
    build_attachment_store,
    build_photo_text_extractor,
    build_speech_transcriber,
)
from .model import ModelError, build_model_adapter
from .service import DomainError, MelvanService
from .web import MelvanHTTPServer


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the MELvan development server.")
    value.add_argument(
        "--reset",
        action="store_true",
        help="Clear Statement, review, and Evidence demo records before startup.",
    )
    value.add_argument(
        "--refresh-auth",
        action="store_true",
        help="Run the configured credential refresh before checking the model endpoint.",
    )
    value.add_argument("--host", default=None)
    value.add_argument("--port", type=int, default=None)
    value.add_argument("--model-mode", choices=["demo", "foundry", "openai"], default=None)
    value.add_argument(
        "--bootstrap-owner",
        action="store_true",
        help="Issue the Owner's initial temporary password and show it once.",
    )
    value.add_argument("--check", action="store_true", help="Run startup checks and exit.")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = Settings.from_env(host=args.host, port=args.port, model_mode=args.model_mode)
        settings.validate_hosted()
        database = Database(settings.database_path)
        if args.reset:
            database.reset()
            attachment_root = (
                settings.attachment_root
                or settings.database_path.parent / "attachments"
            )
            shutil.rmtree(attachment_root, ignore_errors=True)
        model = build_model_adapter(settings)
        if args.refresh_auth:
            model.refresh_auth()
            model.verify_connectivity()
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "logic_model.json"
        service = MelvanService(
            database,
            model,
            fixture_path,
            initial_admin_email=settings.initial_admin_email,
            attachment_store=build_attachment_store(settings),
            speech_transcriber=build_speech_transcriber(settings),
            photo_text_extractor=build_photo_text_extractor(settings, model),
        )
        service.initialize()
        sessions = SessionStore(
            service.users,
            database,
            pepper=settings.auth_pepper or "melvan-local-development-pepper",
            email_sender=build_email_sender(settings),
        )
        if args.bootstrap_owner:
            credential = sessions.bootstrap_owner()
            print(
                "Owner temporary password: "
                f"{credential['temporary_password']} "
                f"(expires {credential['expires_at']})"
            )
        status = service.health()
    except (DomainError, IdentityError, ModelError, OSError, ValueError) as exc:
        print(f"MELvan startup failed: {exc}", file=sys.stderr)
        print(
            "Recovery: check .env, model credentials, MELVAN_MODEL_MODE, and the configured endpoint.",
            file=sys.stderr,
        )
        return 1

    print(
        f"MELvan preflight ready: database={status['database']}, "
        f"identity={status['identity']}, logic_model={status['logic_model']['version']}, "
        f"model={status['model']['model']}"
    )
    if args.check:
        return 0

    try:
        server = MelvanHTTPServer(
            (settings.host, settings.port),
            service,
            sessions=sessions,
            secure_cookies=settings.environment == "hosted",
        )
    except OSError as exc:
        print(
            f"MELvan could not open http://{settings.host}:{settings.port}: {exc}",
            file=sys.stderr,
        )
        print("Recovery: choose another --port or check local network permissions.", file=sys.stderr)
        return 1
    print(f"MELvan is available at http://{settings.host}:{settings.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MELvan.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
