from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from melvan.attachment_migration import (
    AttachmentMigrationError,
    migrate_attachments,
)
from melvan.media import AzureBlobAttachmentStore, LocalAttachmentStore


class InterruptingStore(LocalAttachmentStore):
    def __init__(self, root: Path, interrupt_on: int):
        super().__init__(root)
        self.interrupt_on = interrupt_on
        self.writes = 0

    def write_exact(self, storage_key: str, content: bytes, checksum: str) -> None:
        self.writes += 1
        if self.writes == self.interrupt_on:
            raise KeyboardInterrupt("simulated interruption")
        super().write_exact(storage_key, content, checksum)


class AttachmentMigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.database = root / "melvan.sqlite3"
        self.source_root = root / "source"
        self.destination_root = root / "destination"
        self.manifest = root / "manifest.json"
        self.source = LocalAttachmentStore(self.source_root)
        self.source.initialize()
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE media_staging (id TEXT PRIMARY KEY);
                CREATE TABLE statement_attachments (
                    id TEXT PRIMARY KEY,
                    statement_id TEXT NOT NULL UNIQUE,
                    workspace_id TEXT NOT NULL,
                    storage_key TEXT NOT NULL UNIQUE,
                    byte_size INTEGER NOT NULL,
                    checksum TEXT NOT NULL
                );
                """
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def add_attachment(self, number: int, content: bytes | None = None) -> dict:
        content = content or f"attachment-{number}".encode()
        key = f"workspace-one/statement-{number}/attachment-{number}.jpg"
        checksum = hashlib.sha256(content).hexdigest()
        self.source.write_exact(key, content, checksum)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT INTO statement_attachments
                    (id, statement_id, workspace_id, storage_key, byte_size, checksum)
                VALUES (?, ?, 'workspace-one', ?, ?, ?)
                """,
                (
                    f"attachment-{number}",
                    f"statement-{number}",
                    key,
                    len(content),
                    checksum,
                ),
            )
        return {"key": key, "content": content, "checksum": checksum}

    def migrate(self, destination=None, **kwargs):
        return migrate_attachments(
            database_path=self.database,
            source_root=self.source_root,
            destination=destination or LocalAttachmentStore(self.destination_root),
            manifest_path=self.manifest,
            **kwargs,
        )

    def test_dry_run_records_plan_without_copying(self) -> None:
        attachment = self.add_attachment(1)

        manifest = self.migrate(dry_run=True)

        self.assertEqual("dry_run_ready", manifest["status"])
        self.assertEqual("would_copy", manifest["entries"][0]["status"])
        self.assertFalse((self.destination_root / attachment["key"]).exists())
        self.assertEqual(attachment["content"], self.source.read(attachment["key"]))

    def test_copy_reconciles_and_rerun_is_idempotent(self) -> None:
        first = self.add_attachment(1)
        second = self.add_attachment(2)
        database_before = self.database.read_bytes()

        completed = self.migrate()
        rerun = self.migrate()

        self.assertEqual("completed", completed["status"])
        self.assertEqual("completed", rerun["status"])
        self.assertEqual(2, rerun["reconciliation"]["verified_count"])
        destination = LocalAttachmentStore(self.destination_root)
        self.assertEqual(first["content"], destination.read(first["key"]))
        self.assertEqual(second["content"], destination.read(second["key"]))
        self.assertEqual(first["content"], self.source.read(first["key"]))
        self.assertEqual(database_before, self.database.read_bytes())

    def test_interrupted_copy_resumes_from_manifest(self) -> None:
        first = self.add_attachment(1)
        second = self.add_attachment(2)
        interrupted = InterruptingStore(self.destination_root, interrupt_on=2)

        with self.assertRaises(KeyboardInterrupt):
            self.migrate(destination=interrupted)

        checkpoint = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual("copied", checkpoint["entries"][0]["status"])
        self.assertEqual("pending", checkpoint["entries"][1]["status"])

        completed = self.migrate()

        self.assertEqual("completed", completed["status"])
        destination = LocalAttachmentStore(self.destination_root)
        self.assertEqual(first["content"], destination.read(first["key"]))
        self.assertEqual(second["content"], destination.read(second["key"]))

    def test_conflicting_destination_fails_without_overwrite(self) -> None:
        attachment = self.add_attachment(1)
        destination = LocalAttachmentStore(self.destination_root)
        destination.initialize()
        conflict = b"different-content"
        destination.write_exact(
            attachment["key"],
            conflict,
            hashlib.sha256(conflict).hexdigest(),
        )

        with self.assertRaisesRegex(AttachmentMigrationError, "reconciliation failed"):
            self.migrate(destination=destination)

        self.assertEqual(conflict, destination.read(attachment["key"]))
        self.assertEqual(attachment["content"], self.source.read(attachment["key"]))

    def test_source_orphan_and_active_staging_block_migration(self) -> None:
        self.add_attachment(1)
        orphan = b"orphan"
        self.source.write_exact(
            "workspace-one/orphan/orphan.jpg",
            orphan,
            hashlib.sha256(orphan).hexdigest(),
        )
        with self.assertRaisesRegex(AttachmentMigrationError, "Unreferenced files"):
            self.migrate()

        self.source.delete("workspace-one/orphan/orphan.jpg")
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO media_staging (id) VALUES ('stage-one')")
        with self.assertRaisesRegex(AttachmentMigrationError, "no staged photos"):
            self.migrate()

    def test_unexpected_destination_object_blocks_copy(self) -> None:
        attachment = self.add_attachment(1)
        destination = LocalAttachmentStore(self.destination_root)
        destination.initialize()
        unexpected = b"not-in-source-inventory"
        destination.write_exact(
            "workspace-other/statement-other/attachment-other.jpg",
            unexpected,
            hashlib.sha256(unexpected).hexdigest(),
        )

        with self.assertRaisesRegex(AttachmentMigrationError, "Unexpected objects"):
            self.migrate(destination=destination)

        self.assertFalse((self.destination_root / attachment["key"]).exists())
        self.assertEqual(attachment["content"], self.source.read(attachment["key"]))

    def test_source_drift_invalidates_existing_manifest(self) -> None:
        self.add_attachment(1)
        self.migrate(dry_run=True)
        self.add_attachment(2)

        with self.assertRaisesRegex(AttachmentMigrationError, "source inventory changed"):
            self.migrate()

    def test_manifest_cannot_resume_against_another_destination(self) -> None:
        self.add_attachment(1)
        self.migrate(dry_run=True)

        with self.assertRaisesRegex(AttachmentMigrationError, "another destination"):
            self.migrate(
                destination=LocalAttachmentStore(
                    Path(self.tempdir.name) / "different-destination"
                )
            )

    def test_reconcile_only_detects_missing_destination(self) -> None:
        self.add_attachment(1)
        self.migrate(dry_run=True)

        with self.assertRaisesRegex(AttachmentMigrationError, "reconciliation failed"):
            self.migrate(reconcile_only=True)

    def test_manifest_cannot_be_written_inside_source_store(self) -> None:
        self.add_attachment(1)

        with self.assertRaisesRegex(AttachmentMigrationError, "outside the source"):
            migrate_attachments(
                database_path=self.database,
                source_root=self.source_root,
                destination=LocalAttachmentStore(self.destination_root),
                manifest_path=self.source_root / "manifest.json",
            )


@unittest.skipUnless(
    os.getenv("MELVAN_TEST_AZURITE_CONNECTION_STRING"),
    "Azurite integration is not configured",
)
class AzuriteAttachmentMigrationTestCase(unittest.TestCase):
    def test_local_inventory_migrates_and_reconciles_in_azurite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "melvan.sqlite3"
            source_root = root / "source"
            manifest_path = root / "manifest.json"
            source = LocalAttachmentStore(source_root)
            source.initialize()
            content = b"production-like-attachment"
            checksum = hashlib.sha256(content).hexdigest()
            key = "workspace-one/statement-one/attachment-one.jpg"
            source.write_exact(key, content, checksum)
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE media_staging (id TEXT PRIMARY KEY);
                    CREATE TABLE statement_attachments (
                        id TEXT PRIMARY KEY,
                        statement_id TEXT NOT NULL UNIQUE,
                        workspace_id TEXT NOT NULL,
                        storage_key TEXT NOT NULL UNIQUE,
                        byte_size INTEGER NOT NULL,
                        checksum TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO statement_attachments
                        (id, statement_id, workspace_id, storage_key, byte_size, checksum)
                    VALUES ('attachment-one', 'statement-one', 'workspace-one', ?, ?, ?)
                    """,
                    (key, len(content), checksum),
                )
            container = f"melvan-migration-{uuid.uuid4().hex}"
            destination = AzureBlobAttachmentStore(
                account_url=None,
                container=container,
                connection_string=os.environ[
                    "MELVAN_TEST_AZURITE_CONNECTION_STRING"
                ],
            )
            try:
                destination.initialize()
                manifest = migrate_attachments(
                    database_path=database,
                    source_root=source_root,
                    destination=destination,
                    manifest_path=manifest_path,
                )
                self.assertEqual("completed", manifest["status"])
                self.assertEqual(content, destination.read(key))
                self.assertEqual(1, manifest["reconciliation"]["verified_count"])
                self.assertEqual(content, source.read(key))
            finally:
                try:
                    destination._client().delete_container()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
