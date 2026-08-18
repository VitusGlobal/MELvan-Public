"""Secret-safe configuration preflight for MELvan deployment profiles."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass


PROFILE_REQUIREMENTS = {
    "local": (),
    "hosted_demo": (
        "MELVAN_AUTH_PEPPER",
        "MELVAN_EMAIL_ENDPOINT",
        "MELVAN_EMAIL_SENDER",
        "MELVAN_FOUNDRY_PROJECT_ENDPOINT",
        "MELVAN_INITIAL_ADMIN_EMAIL",
        "MELVAN_MODEL",
        "MELVAN_OCR_ENDPOINT",
        "MELVAN_PERSISTENT_DATABASE",
        "MELVAN_PUBLIC_BASE_URL",
        "MELVAN_SPEECH_ENDPOINT",
    ),
    "production": (
        "MELVAN_AUTH_PEPPER",
        "MELVAN_BLOB_ACCOUNT_URL",
        "MELVAN_DATABASE_URL",
        "MELVAN_EMAIL_ENDPOINT",
        "MELVAN_EMAIL_SENDER",
        "MELVAN_FOUNDRY_PROJECT_ENDPOINT",
        "MELVAN_INITIAL_ADMIN_EMAIL",
        "MELVAN_MODEL",
        "MELVAN_OCR_ENDPOINT",
        "MELVAN_PUBLIC_BASE_URL",
        "MELVAN_SPEECH_ENDPOINT",
    ),
}

AZURE_DEPLOYMENT_REQUIREMENTS = (
    "AZURE_CLIENT_ID",
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID",
    "MELVAN_APP_NAME",
    "MELVAN_COMMUNICATION_RESOURCE",
    "MELVAN_COMMUNICATION_RESOURCE_GROUP",
    "MELVAN_EMAIL_ENDPOINT",
    "MELVAN_EMAIL_SENDER",
    "MELVAN_FOUNDRY_ACCOUNT",
    "MELVAN_FOUNDRY_PROJECT_ENDPOINT",
    "MELVAN_FOUNDRY_RESOURCE_GROUP",
    "MELVAN_INITIAL_ADMIN_EMAIL",
    "MELVAN_LOCATION",
    "MELVAN_MODEL",
    "MELVAN_RESOURCE_GROUP",
)


@dataclass(frozen=True)
class PreflightResult:
    profile: str
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "ready": self.ready,
            "missing": list(self.missing),
        }


def check(profile: str, *, azure_deployment: bool = False) -> PreflightResult:
    selected = profile.strip().lower()
    if selected not in PROFILE_REQUIREMENTS:
        raise ValueError("Profile must be local, hosted_demo, or production.")
    required = list(PROFILE_REQUIREMENTS[selected])
    if azure_deployment:
        required.extend(AZURE_DEPLOYMENT_REQUIREMENTS)
    missing = tuple(sorted({name for name in required if not os.getenv(name)}))
    return PreflightResult(selected, missing)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Check MELvan configuration without printing values."
    )
    value.add_argument(
        "--profile",
        choices=tuple(PROFILE_REQUIREMENTS),
        default=os.getenv("MELVAN_DEPLOYMENT_PROFILE", "local"),
    )
    value.add_argument(
        "--azure-deployment",
        action="store_true",
        help="Also require the fork-owned GitHub/Azure deployment variables.",
    )
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = check(args.profile, azure_deployment=args.azure_deployment)
    if args.json:
        print(json.dumps(result.as_dict(), sort_keys=True))
    elif result.ready:
        print(f"MELvan {result.profile} configuration is ready.")
    else:
        print(f"MELvan {result.profile} configuration is incomplete.")
        for name in result.missing:
            print(f"MISSING {name}")
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
