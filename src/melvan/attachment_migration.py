"""Resumable, source-preserving attachment migration to Azure Blob Storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from melvan.config import Settings
from melvan.media import (
    AttachmentStorage,
    AzureBlobAttachmentStore,
    LocalAttachmentStore,
    MediaError,
)


MANIFEST_SCHEMA_VERSION = 1


class AttachmentMigrationError(RuntimeError):
    """Raised when migration safety or reconciliation checks fail."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _inventory_fingerprint(entries: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "attachment_id": item["attachment_id"],
            "statement_id": item["statement_id"],
            "workspace_id": item["workspace_id"],
            "storage_key": item["storage_key"],
            "byte_size": item["byte_size"],
            "checksum": item["checksum"],
        }
        for item in entries
    ]
    return _checksum(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _destination_identity(destination: AttachmentStorage) -> dict[str, str]:
    if isinstance(destination, AzureBlobAttachmentStore):
        endpoint = str(destination.account_url or "")
        if not endpoint and destination.connection_string:
            parts = {}
            for item in destination.connection_string.split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    parts[key] = value
            endpoint = parts.get("BlobEndpoint") or parts.get("AccountName", "emulator")
        return {
            "backend": "azure_blob",
            "endpoint": endpoint,
            "container": destination.container,
        }
    if isinstance(destination, LocalAttachmentStore):
        return {
            "backend": "local",
            "root": str(destination.root.resolve()),
        }
    return {"backend": type(destination).__name__}


def inventory_attachments(
    database_path: Path,
    source: AttachmentStorage,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read and validate the authoritative committed-attachment inventory."""
    if not database_path.is_file():
        raise AttachmentMigrationError("The source SQLite database does not exist.")
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(statement_attachments)"
            ).fetchall()
        }
        if "workspace_id" not in columns:
            raise AttachmentMigrationError(
                "The source database must be upgraded before attachment migration."
            )
        active_staging = connection.execute(
            "SELECT COUNT(*) FROM media_staging"
        ).fetchone()[0]
        if active_staging:
            raise AttachmentMigrationError(
                "Attachment migration requires a quiescent source with no staged photos."
            )
        rows = connection.execute(
            """
            SELECT id, statement_id, workspace_id, storage_key, byte_size, checksum
            FROM statement_attachments
            ORDER BY workspace_id, statement_id, id
            """
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise AttachmentMigrationError(
            "The source attachment inventory could not be read."
        ) from exc
    finally:
        connection.close()

    entries: list[dict[str, Any]] = []
    expected_keys: set[str] = set()
    for row in rows:
        key = str(row["storage_key"])
        if key in expected_keys:
            raise AttachmentMigrationError(
                "The source database contains a duplicate attachment storage key."
            )
        try:
            content = source.read(key)
        except MediaError as exc:
            raise AttachmentMigrationError(
                f"Source attachment is missing or unreadable: {key}"
            ) from exc
        actual_checksum = _checksum(content)
        if len(content) != int(row["byte_size"]) or actual_checksum != str(row["checksum"]):
            raise AttachmentMigrationError(
                f"Source attachment metadata does not match its content: {key}"
            )
        expected_keys.add(key)
        entries.append(
            {
                "attachment_id": str(row["id"]),
                "statement_id": str(row["statement_id"]),
                "workspace_id": str(row["workspace_id"]),
                "storage_key": key,
                "byte_size": len(content),
                "checksum": actual_checksum,
                "status": "pending",
                "attempts": 0,
                "error": None,
            }
        )

    try:
        source_keys = set(source.list_keys())
    except Exception as exc:
        raise AttachmentMigrationError(
            "The source attachment store could not be inventoried."
        ) from exc
    orphans = sorted(source_keys - expected_keys)
    return entries, orphans


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = _utc_now()
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AttachmentMigrationError("The migration manifest is unreadable.") from exc
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AttachmentMigrationError("The migration manifest version is unsupported.")
    return manifest


def _new_manifest(
    database_path: Path,
    source_root: Path,
    entries: list[dict[str, Any]],
    destination_identity: dict[str, str],
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "source": {
            "database_path": str(database_path.resolve()),
            "attachment_root": str(source_root.resolve()),
            "inventory_fingerprint": _inventory_fingerprint(entries),
        },
        "destination": destination_identity,
        "entries": entries,
        "reconciliation": None,
    }


def _prepare_manifest(
    manifest_path: Path,
    database_path: Path,
    source_root: Path,
    entries: list[dict[str, Any]],
    destination_identity: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    fingerprint = _inventory_fingerprint(entries)
    if not manifest_path.exists():
        return _new_manifest(
            database_path,
            source_root,
            entries,
            destination_identity,
        )
    manifest = _load_manifest(manifest_path)
    if manifest.get("source", {}).get("inventory_fingerprint") != fingerprint:
        raise AttachmentMigrationError(
            "The source inventory changed after the migration manifest was created."
        )
    if manifest.get("source", {}).get("database_path") != str(database_path.resolve()):
        raise AttachmentMigrationError("The migration manifest belongs to another database.")
    if manifest.get("source", {}).get("attachment_root") != str(source_root.resolve()):
        raise AttachmentMigrationError(
            "The migration manifest belongs to another attachment root."
        )
    if manifest.get("destination") != destination_identity:
        raise AttachmentMigrationError(
            "The migration manifest belongs to another destination."
        )
    current = {item["storage_key"]: item for item in manifest.get("entries", [])}
    if (
        len(current) != len(manifest.get("entries", []))
        or set(current) != {item["storage_key"] for item in entries}
    ):
        raise AttachmentMigrationError("The migration manifest inventory is incomplete.")
    if not dry_run:
        for item in current.values():
            if item.get("status") == "would_copy":
                item["status"] = "pending"
    return manifest


def reconcile_attachments(
    expected: list[dict[str, Any]],
    destination: AttachmentStorage,
) -> dict[str, Any]:
    expected_by_key = {item["storage_key"]: item for item in expected}
    destination_keys = set(destination.list_keys())
    missing = sorted(set(expected_by_key) - destination_keys)
    unexpected = sorted(destination_keys - set(expected_by_key))
    mismatched: list[dict[str, Any]] = []
    verified = 0
    for key in sorted(set(expected_by_key) & destination_keys):
        expected_item = expected_by_key[key]
        try:
            content = destination.read(key)
            actual_size = len(content)
            actual_checksum = _checksum(content)
        except Exception as exc:
            mismatched.append(
                {
                    "storage_key": key,
                    "expected_size": expected_item["byte_size"],
                    "expected_checksum": expected_item["checksum"],
                    "error": str(exc),
                }
            )
            continue
        if (
            actual_size != int(expected_item["byte_size"])
            or actual_checksum != expected_item["checksum"]
        ):
            mismatched.append(
                {
                    "storage_key": key,
                    "expected_size": expected_item["byte_size"],
                    "actual_size": actual_size,
                    "expected_checksum": expected_item["checksum"],
                    "actual_checksum": actual_checksum,
                }
            )
        else:
            verified += 1
    return {
        "expected_count": len(expected_by_key),
        "destination_count": len(destination_keys),
        "verified_count": verified,
        "missing": missing,
        "unexpected": unexpected,
        "mismatched": mismatched,
        "passed": not missing and not unexpected and not mismatched,
        "checked_at": _utc_now(),
    }


def migrate_attachments(
    *,
    database_path: Path,
    source_root: Path,
    destination: AttachmentStorage,
    manifest_path: Path,
    dry_run: bool = False,
    reconcile_only: bool = False,
) -> dict[str, Any]:
    source_root_resolved = source_root.resolve()
    manifest_resolved = manifest_path.resolve()
    if manifest_resolved == source_root_resolved or source_root_resolved in manifest_resolved.parents:
        raise AttachmentMigrationError(
            "The migration manifest must be stored outside the source attachment root."
        )
    source = LocalAttachmentStore(source_root)
    entries, source_orphans = inventory_attachments(database_path, source)
    if source_orphans:
        raise AttachmentMigrationError(
            "Unreferenced files exist in the source attachment store: "
            + ", ".join(source_orphans)
        )
    manifest = _prepare_manifest(
        manifest_path,
        database_path,
        source_root,
        entries,
        _destination_identity(destination),
        dry_run=dry_run,
    )
    manifest_entries = {
        item["storage_key"]: item for item in manifest["entries"]
    }

    readiness = destination.readiness()
    if readiness.get("status") != "ready":
        raise AttachmentMigrationError(
            "The destination attachment store is not ready or is not private."
        )
    destination_keys = set(destination.list_keys())
    unexpected = sorted(destination_keys - set(manifest_entries))
    if unexpected:
        manifest["status"] = "failed"
        manifest["reconciliation"] = {
            "passed": False,
            "unexpected": unexpected,
            "checked_at": _utc_now(),
        }
        _write_manifest(manifest_path, manifest)
        raise AttachmentMigrationError(
            "Unexpected objects exist in the destination attachment container."
        )

    manifest["status"] = "dry_run" if dry_run else "in_progress"
    _write_manifest(manifest_path, manifest)
    if not reconcile_only:
        for source_entry in entries:
            item = manifest_entries[source_entry["storage_key"]]
            key = item["storage_key"]
            if item.get("status") == "verified":
                continue
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["error"] = None
            try:
                content = source.read(key)
                if key in destination_keys:
                    destination_content = destination.read(key)
                    if (
                        len(destination_content) != item["byte_size"]
                        or _checksum(destination_content) != item["checksum"]
                    ):
                        raise AttachmentMigrationError(
                            "The destination contains conflicting attachment content."
                        )
                    item["status"] = "already_present"
                elif dry_run:
                    item["status"] = "would_copy"
                else:
                    destination.write_exact(key, content, item["checksum"])
                    copied = destination.read(key)
                    if len(copied) != item["byte_size"] or _checksum(copied) != item["checksum"]:
                        raise AttachmentMigrationError(
                            "The copied attachment failed immediate verification."
                        )
                    item["status"] = "copied"
                    destination_keys.add(key)
            except Exception as exc:
                item["status"] = "error"
                item["error"] = str(exc)
            _write_manifest(manifest_path, manifest)

    if dry_run:
        failed = [item for item in manifest["entries"] if item["status"] == "error"]
        manifest["status"] = "failed" if failed else "dry_run_ready"
        manifest["reconciliation"] = {
            "passed": not failed,
            "expected_count": len(entries),
            "already_present_count": sum(
                item["status"] == "already_present" for item in manifest["entries"]
            ),
            "would_copy_count": sum(
                item["status"] == "would_copy" for item in manifest["entries"]
            ),
            "checked_at": _utc_now(),
        }
        _write_manifest(manifest_path, manifest)
        if failed:
            raise AttachmentMigrationError("The attachment migration dry run failed.")
        return manifest

    reconciliation = reconcile_attachments(entries, destination)
    manifest["reconciliation"] = reconciliation
    if reconciliation["passed"]:
        for item in manifest["entries"]:
            item["status"] = "verified"
            item["error"] = None
        manifest["status"] = "completed"
    else:
        manifest["status"] = "failed"
    _write_manifest(manifest_path, manifest)
    if not reconciliation["passed"]:
        raise AttachmentMigrationError("Attachment reconciliation failed.")
    return manifest


def _default_source_root(settings: Settings) -> Path:
    if settings.attachment_root:
        return settings.attachment_root
    database_path = settings.persistent_database_path or settings.database_path
    return database_path.parent / "attachments"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy MELvan attachments to Azure Blob Storage without changing the source."
    )
    parser.add_argument("--source-database", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--reconcile-only", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(model_mode="demo")
    database_path = args.source_database or (
        settings.persistent_database_path or settings.database_path
    )
    source_root = args.source_root or _default_source_root(settings)
    manifest_path = args.manifest or (
        settings.project_root / "data" / "attachment-migration-manifest.json"
    )
    if not settings.blob_account_url and not settings.blob_connection_string:
        print(
            "Attachment migration requires MELVAN_BLOB_ACCOUNT_URL or "
            "MELVAN_BLOB_CONNECTION_STRING.",
            file=sys.stderr,
        )
        return 2
    destination = AzureBlobAttachmentStore(
        account_url=settings.blob_account_url,
        container=settings.blob_container,
        connection_string=settings.blob_connection_string,
    )
    if not args.dry_run and not args.reconcile_only and not args.yes:
        if not sys.stdin.isatty():
            print("Use --yes for a reviewed non-interactive migration.", file=sys.stderr)
            return 2
        confirmation = input(
            "Copy committed attachments to Blob Storage without deleting the source? "
            "Type MIGRATE to continue: "
        )
        if confirmation != "MIGRATE":
            print("Attachment migration cancelled.")
            return 2
    try:
        manifest = migrate_attachments(
            database_path=database_path,
            source_root=source_root,
            destination=destination,
            manifest_path=manifest_path,
            dry_run=args.dry_run,
            reconcile_only=args.reconcile_only,
        )
    except (AttachmentMigrationError, MediaError) as exc:
        print(f"Attachment migration failed: {exc}", file=sys.stderr)
        return 1
    summary = manifest.get("reconciliation") or {}
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": str(manifest_path),
                "expected_count": summary.get("expected_count", len(manifest["entries"])),
                "verified_count": summary.get("verified_count"),
                "would_copy_count": summary.get("would_copy_count"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
