"""Initialize the one MELvan Owner credential for a new deployment."""

from __future__ import annotations

import json
from pathlib import Path

from .auth import SessionStore, build_email_sender
from .config import Settings
from .identity import UserDirectory
from .relational import build_database


def main() -> int:
    settings = Settings.from_env()
    settings.validate_hosted()
    database = build_database(settings)
    fixture = Path(__file__).resolve().parent / "fixtures" / "logic_model.json"
    users = UserDirectory(
        database,
        legacy_path=(
            settings.users_path or settings.database_path.parent / "users.json"
            if settings.deployment_profile != "production"
            else None
        ),
        initial_owner_email=settings.initial_admin_email,
    )
    with database.startup_lock():
        database.initialize(fixture)
        users.initialize()
    sessions = SessionStore(
        users,
        database,
        pepper=str(settings.auth_pepper),
        email_sender=build_email_sender(settings),
    )
    credential = sessions.bootstrap_owner()
    print(
        json.dumps(
            {
                "owner_email": users.owner()["email"],
                **credential,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
