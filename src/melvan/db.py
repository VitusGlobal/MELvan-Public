"""SQLite persistence and fixture loading."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Protocol, TypeVar


T = TypeVar("T")


class DatabaseRow(Protocol):
    def __getitem__(self, key: str | int) -> Any: ...

    def keys(self) -> list[str]: ...


class DatabaseCursor(Protocol):
    rowcount: int

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class DatabaseConnection(Protocol):
    def execute(
        self, statement: str, parameters: tuple[Any, ...] = ()
    ) -> DatabaseCursor: ...

    def executemany(
        self, statement: str, parameters: list[tuple[Any, ...]]
    ) -> DatabaseCursor: ...

    def executescript(self, script: str) -> DatabaseCursor: ...


class DatabaseAdapter(Protocol):
    path: Path
    snapshot_path: Path | None

    def reset(self) -> None: ...

    def initialize(self, fixture_path: Path) -> None: ...

    def connection(self) -> ContextManager[DatabaseConnection]: ...

    def workspace(
        self, workspace_id: str, *, read_only: bool = False
    ) -> ContextManager[None]: ...

    def current_workspace_id(self) -> str: ...

    def startup_lock(self) -> ContextManager[None]: ...

    def run_transaction(
        self,
        operation: Callable[[DatabaseConnection], T],
        *,
        attempts: int = 3,
    ) -> T: ...


class DatabaseIntegrityError(RuntimeError):
    """Engine-neutral integrity failure."""


DATABASE_INTEGRITY_ERRORS = (sqlite3.IntegrityError, DatabaseIntegrityError)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    nickname TEXT NOT NULL,
    workspace_type TEXT NOT NULL CHECK (workspace_type IN ('owner', 'administrator')),
    owner_user_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_owner_workspace
ON workspaces(workspace_type) WHERE workspace_type = 'owner';

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended')),
    password_salt TEXT,
    password_hash TEXT,
    password_state TEXT NOT NULL DEFAULT 'bootstrap_required' CHECK (
        password_state IN ('bootstrap_required', 'password_change_required', 'active')
    ),
    temporary_password_expires_at TEXT,
    recovery_salt TEXT,
    recovery_hash TEXT,
    recovery_expires_at TEXT,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    total_logins INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    user_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'administrator', 'reporter', 'reviewer')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, workspace_id, role),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_owner_role
ON user_roles(role) WHERE role = 'owner';

CREATE TABLE IF NOT EXISTS logic_models (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 1,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    source TEXT NOT NULL,
    fixture_json TEXT NOT NULL,
    source_filename TEXT,
    media_type TEXT,
    content_hash TEXT,
    source_path TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT,
    uploaded_at TEXT,
    committed_by TEXT,
    committed_at TEXT,
    change_summary_json TEXT NOT NULL DEFAULT '{}',
    impact_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS elements (
    id TEXT PRIMARY KEY,
    display_id TEXT NOT NULL,
    logic_model_id TEXT,
    label TEXT NOT NULL,
    element_type TEXT NOT NULL CHECK (element_type IN ('Output', 'Outcome')),
    level TEXT NOT NULL,
    group_name TEXT,
    source TEXT NOT NULL CHECK (source IN ('fixture', 'model', 'reviewer')),
    status TEXT NOT NULL CHECK (status IN ('published', 'provisional', 'approved')),
    created_at TEXT NOT NULL,
    stable_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    display_number INTEGER,
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id)
);

CREATE TABLE IF NOT EXISTS logic_model_drafts (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    base_logic_model_id TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (base_logic_model_id) REFERENCES logic_models(id)
);

CREATE TABLE IF NOT EXISTS element_relationships (
    from_element_id TEXT NOT NULL,
    to_element_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    PRIMARY KEY (from_element_id, to_element_id, relationship_type),
    FOREIGN KEY (from_element_id) REFERENCES elements(id),
    FOREIGN KEY (to_element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS statements (
    id TEXT PRIMARY KEY,
    original_text TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'awaiting_review', 'evidence', 'failed')),
    active_analysis_id TEXT,
    initial_classification TEXT,
    approved_classification TEXT,
    processing_error TEXT,
    current_context_revision_id TEXT,
    context_updated_at TEXT,
    program_irrelevant_at TEXT,
    program_irrelevant_by TEXT,
    program_irrelevant_analysis_id TEXT,
    program_irrelevant_reason TEXT,
    capture_method TEXT NOT NULL DEFAULT 'text'
        CHECK (capture_method IN ('text', 'voice', 'photo')),
    capture_candidate_text TEXT,
    capture_text_edited INTEGER NOT NULL DEFAULT 0
        CHECK (capture_text_edited IN (0, 1)),
    capture_provider TEXT,
    capture_provider_version TEXT,
    capture_processing_ms INTEGER,
    capture_result TEXT NOT NULL DEFAULT 'success'
);

CREATE TABLE IF NOT EXISTS statement_context_revisions (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    context_text TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (statement_id, version),
    FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_staging (
    id TEXT PRIMARY KEY,
    reporter_id TEXT NOT NULL,
    session_fingerprint TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    candidate_text TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    processing_ms INTEGER NOT NULL,
    processing_result TEXT NOT NULL CHECK (processing_result IN ('success', 'no_text')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS statement_attachments (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL UNIQUE,
    reporter_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    processing_ms INTEGER NOT NULL,
    processing_result TEXT NOT NULL CHECK (processing_result IN ('success', 'no_text')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    summary TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('Activity', 'Output', 'Outcome', 'Indicator')),
    classification_confidence REAL NOT NULL,
    classification_rationale TEXT NOT NULL,
    result_direction TEXT CHECK (result_direction IN ('Positive', 'Negative')),
    outcome_category TEXT,
    outcome_change_types TEXT NOT NULL DEFAULT '[]',
    match_status TEXT NOT NULL CHECK (match_status IN ('existing_match', 'no_suitable_match')),
    mapping_confidence REAL NOT NULL,
    mapping_rationale TEXT NOT NULL,
    proposed_element_id TEXT,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    logic_model_version TEXT NOT NULL,
    logic_model_id TEXT,
    context_revision_id TEXT,
    reviewer_instruction TEXT,
    learning_policy_version TEXT,
    learning_memory_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (learning_memory_enabled IN (0, 1)),
    learning_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (statement_id, version),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (proposed_element_id) REFERENCES elements(id) ON DELETE SET NULL,
    FOREIGN KEY (context_revision_id) REFERENCES statement_context_revisions(id)
);

CREATE TABLE IF NOT EXISTS analysis_mappings (
    analysis_id TEXT NOT NULL,
    element_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    confidence REAL,
    rationale TEXT,
    PRIMARY KEY (analysis_id, element_id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
    FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_feedback (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    corrected_classification TEXT,
    corrected_result_direction TEXT CHECK (corrected_result_direction IN ('Positive', 'Negative')),
    corrected_outcome_category TEXT CHECK (corrected_outcome_category IN ('A', 'B', 'C')),
    corrected_outcome_change_types TEXT NOT NULL DEFAULT '[]',
    comments TEXT,
    language_issue_types TEXT NOT NULL DEFAULT '[]',
    mapping_ids TEXT NOT NULL DEFAULT '[]',
    created_element_id TEXT,
    rejected_proposal_snapshot TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL UNIQUE,
    analysis_id TEXT NOT NULL,
    accepted_by TEXT,
    accepted_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS logic_model_activations (
    id TEXT PRIMARY KEY,
    logic_model_id TEXT NOT NULL,
    previous_logic_model_id TEXT,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('upload', 'commit', 'restore')),
    activated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed' CHECK (
        status IN ('preparing', 'completed', 'failed', 'no_op')
    ),
    impact_manifest_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    completed_at TEXT,
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (previous_logic_model_id) REFERENCES logic_models(id)
);

CREATE TABLE IF NOT EXISTS evidence_assessments (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    logic_model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'not_relevant')),
    suggested_mapping_ids TEXT NOT NULL DEFAULT '[]',
    suggested_mapping_details TEXT NOT NULL DEFAULT '[]',
    mapping_rationale TEXT NOT NULL DEFAULT '',
    suggested_classification TEXT,
    confirmed_classification TEXT CHECK (
        confirmed_classification IN ('Activity', 'Output', 'Outcome', 'Indicator')
    ),
    suggested_result_direction TEXT CHECK (
        suggested_result_direction IN ('Positive', 'Negative')
    ),
    confirmed_result_direction TEXT CHECK (
        confirmed_result_direction IN ('Positive', 'Negative')
    ),
    reviewer_id TEXT,
    reviewer_feedback TEXT,
    revision_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    accepted_analysis_id TEXT,
    source_logic_model_id TEXT,
    source_assessment_id TEXT,
    activation_id TEXT,
    change_set_json TEXT NOT NULL DEFAULT '[]',
    suggestion_version INTEGER NOT NULL DEFAULT 1,
    disposition_reason TEXT,
    UNIQUE (evidence_id, logic_model_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence(id),
    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (accepted_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (source_logic_model_id) REFERENCES logic_models(id),
    FOREIGN KEY (source_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (activation_id) REFERENCES logic_model_activations(id)
);

CREATE TABLE IF NOT EXISTS evidence_assessment_mappings (
    assessment_id TEXT NOT NULL,
    element_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    confidence REAL,
    rationale TEXT,
    PRIMARY KEY (assessment_id, element_id),
    FOREIGN KEY (assessment_id) REFERENCES evidence_assessments(id) ON DELETE CASCADE,
    FOREIGN KEY (element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id TEXT PRIMARY KEY,
    reviewer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    completion_reason TEXT CHECK (completion_reason IN ('exhausted', 'ended')),
    scoring_policy_version TEXT NOT NULL,
    report_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_review_session_per_reviewer
ON review_sessions(reviewer_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS review_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    statement_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    resulting_analysis_id TEXT,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'changed', 'not_relevant')),
    is_self_review INTEGER NOT NULL DEFAULT 0 CHECK (is_self_review IN (0, 1)),
    comments TEXT,
    created_at TEXT NOT NULL,
    evidence_assessment_id TEXT,
    FOREIGN KEY (session_id) REFERENCES review_sessions(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (resulting_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id)
);

CREATE TABLE IF NOT EXISTS scoring_events (
    id TEXT PRIMARY KEY,
    cycle_key TEXT NOT NULL UNIQUE,
    cycle_type TEXT NOT NULL CHECK (cycle_type IN ('statement', 'evidence_reassessment')),
    statement_id TEXT,
    evidence_assessment_id TEXT,
    first_review_event_id TEXT NOT NULL UNIQUE,
    first_review_approval INTEGER NOT NULL CHECK (first_review_approval IN (0, 1)),
    correct_level INTEGER NOT NULL CHECK (correct_level IN (0, 1)),
    correct_direction INTEGER CHECK (correct_direction IN (0, 1)),
    correct_mapping INTEGER NOT NULL CHECK (correct_mapping IN (0, 1)),
    overall_hygiene INTEGER NOT NULL CHECK (overall_hygiene IN (0, 1)),
    source_signals_json TEXT NOT NULL,
    explanations_json TEXT NOT NULL,
    scoring_policy_version TEXT NOT NULL,
    interpretation_model_version TEXT,
    interpretation_prompt_version TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (first_review_event_id) REFERENCES review_events(id)
);

CREATE TABLE IF NOT EXISTS learning_policies (
    version TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_examples (
    id TEXT PRIMARY KEY,
    review_event_id TEXT NOT NULL,
    statement_id TEXT NOT NULL,
    source_analysis_id TEXT NOT NULL,
    resulting_analysis_id TEXT,
    evidence_assessment_id TEXT,
    example_key TEXT NOT NULL,
    dimension TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    original_value_json TEXT,
    final_value_json TEXT,
    original_confidence REAL,
    truth_weight REAL NOT NULL DEFAULT 1.0,
    surprise_score REAL NOT NULL,
    learning_priority REAL NOT NULL,
    source_text TEXT NOT NULL,
    logic_model_id TEXT,
    stable_element_id TEXT,
    reviewer_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    eligibility_status TEXT NOT NULL DEFAULT 'eligible',
    superseded_by TEXT,
    exclusion_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (review_event_id, example_key),
    FOREIGN KEY (review_event_id) REFERENCES review_events(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (source_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (resulting_analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
    FOREIGN KEY (policy_version) REFERENCES learning_policies(version)
);

CREATE TABLE IF NOT EXISTS language_feedback (
    id TEXT PRIMARY KEY,
    review_event_id TEXT NOT NULL UNIQUE,
    statement_id TEXT NOT NULL,
    issue_types_json TEXT NOT NULL DEFAULT '[]',
    comments TEXT,
    original_summary TEXT NOT NULL,
    final_summary TEXT,
    example_status TEXT NOT NULL CHECK (
        example_status IN ('positive', 'paired', 'negative_only')
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_event_id) REFERENCES review_events(id),
    FOREIGN KEY (statement_id) REFERENCES statements(id)
);

CREATE TABLE IF NOT EXISTS learning_snapshots (
    id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL,
    analysis_id TEXT,
    policy_version TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL CHECK (memory_enabled IN (0, 1)),
    eligible_example_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieved_example_ids_json TEXT NOT NULL DEFAULT '[]',
    retrieval_error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id),
    FOREIGN KEY (policy_version) REFERENCES learning_policies(version)
);

CREATE TABLE IF NOT EXISTS analysis_learning_examples (
    analysis_id TEXT NOT NULL,
    learning_example_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    similarity REAL NOT NULL,
    ranking_score REAL NOT NULL,
    inclusion_reason TEXT NOT NULL,
    PRIMARY KEY (analysis_id, learning_example_id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
    FOREIGN KEY (learning_example_id) REFERENCES learning_examples(id)
);

CREATE TABLE IF NOT EXISTS review_session_score_snapshots (
    session_id TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    scoring_policy_version TEXT NOT NULL,
    learning_policy_version TEXT NOT NULL,
    memory_enabled INTEGER NOT NULL CHECK (memory_enabled IN (0, 1)),
    scores_json TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES review_sessions(id),
    FOREIGN KEY (learning_policy_version) REFERENCES learning_policies(version)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authenticated_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    active_role TEXT,
    workspace_id TEXT,
    session_kind TEXT NOT NULL DEFAULT 'full' CHECK (
        session_kind IN ('full', 'password_change')
    ),
    password_setup_purpose TEXT CHECK (
        password_setup_purpose IN ('initial_setup', 'recovery')
    ),
    duration_choice TEXT NOT NULL DEFAULT 'midnight' CHECK (
        duration_choice IN ('midnight', '7_days', '28_days')
    ),
    timezone_name TEXT NOT NULL DEFAULT 'UTC',
    requested_expires_at TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS authenticated_sessions_user
ON authenticated_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS authentication_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    user_id TEXT,
    challenge_id TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authentication_locks (
    user_id TEXT PRIMARY KEY,
    locked_until TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleared_at TEXT
);

CREATE TABLE IF NOT EXISTS password_recovery_requests (
    id TEXT PRIMARY KEY,
    email_fingerprint TEXT NOT NULL,
    user_id TEXT,
    request_source_fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS password_recovery_email_created
ON password_recovery_requests(email_fingerprint, created_at DESC);
"""


OWNER_WORKSPACE_ID = "workspace-owner"
PROGRAM_TABLES = (
    "logic_models",
    "elements",
    "logic_model_drafts",
    "element_relationships",
    "statements",
    "statement_context_revisions",
    "media_staging",
    "statement_attachments",
    "analyses",
    "analysis_mappings",
    "review_feedback",
    "evidence",
    "logic_model_activations",
    "evidence_assessments",
    "evidence_assessment_mappings",
    "review_sessions",
    "review_events",
    "scoring_events",
    "learning_policies",
    "learning_examples",
    "language_feedback",
    "learning_snapshots",
    "analysis_learning_examples",
    "review_session_score_snapshots",
    "audit_events",
)

_workspace_context: ContextVar[str] = ContextVar(
    "melvan_workspace_id", default=OWNER_WORKSPACE_ID
)


def _fixture_number(elements: list[dict], target: dict) -> int:
    element_type = target["type"]
    return 1 + sum(
        1 for element in elements[: elements.index(target)] if element["type"] == element_type
    )


class Database:
    def __init__(self, path: Path, *, snapshot_path: Path | None = None):
        self.path = path
        self.snapshot_path = snapshot_path
        if snapshot_path is not None and snapshot_path == path:
            raise ValueError("The runtime database and persistent snapshot must be different files.")
        self._transaction_lock = threading.RLock()
        self._snapshot_restored = False

    def reset(self) -> None:
        """Clear demo workflow records while preserving administrative setup."""
        if not self.path.exists():
            return
        with self.connection() as connection:
            has_schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'statements'"
            ).fetchone()
            if not has_schema:
                return
            connection.execute("DELETE FROM scoring_events")
            connection.execute("DELETE FROM analysis_learning_examples")
            connection.execute("DELETE FROM learning_snapshots")
            connection.execute("DELETE FROM language_feedback")
            connection.execute("DELETE FROM learning_examples")
            connection.execute("DELETE FROM review_session_score_snapshots")
            connection.execute("DELETE FROM review_events")
            connection.execute("DELETE FROM review_sessions")
            connection.execute("DELETE FROM evidence_assessment_mappings")
            connection.execute("DELETE FROM evidence_assessments")
            connection.execute("DELETE FROM evidence")
            connection.execute("DELETE FROM review_feedback")
            connection.execute("DELETE FROM analysis_mappings")
            connection.execute("DELETE FROM analyses")
            connection.execute("DELETE FROM statement_attachments")
            connection.execute("DELETE FROM media_staging")
            connection.execute("DELETE FROM statement_context_revisions")
            connection.execute("DELETE FROM statements")
            connection.execute(
                """
                DELETE FROM audit_events
                WHERE entity_type IN ('statement', 'evidence_assessment')
                   OR (entity_type = 'element' AND entity_id IN (
                       SELECT id FROM elements WHERE logic_model_id IS NULL
                   ))
                """
            )
            connection.execute("DELETE FROM elements WHERE logic_model_id IS NULL")

    @contextmanager
    def startup_lock(self) -> Iterator[None]:
        with self._transaction_lock:
            yield

    def run_transaction(
        self,
        operation: Callable[[DatabaseConnection], T],
        *,
        attempts: int = 3,
    ) -> T:
        with self.connection() as connection:
            return operation(connection)

    def initialize(self, fixture_path: Path) -> None:
        self._restore_snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._apply_migrations(connection)
            self._apply_workspace_migration(connection)
            self._load_fixture(connection, fixture_path)

    @staticmethod
    def _apply_migrations(connection: sqlite3.Connection) -> None:
        logic_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(logic_models)").fetchall()
        }
        logic_additions = {
            "sequence": "INTEGER NOT NULL DEFAULT 1",
            "is_current": "INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1))",
            "source_filename": "TEXT",
            "media_type": "TEXT",
            "content_hash": "TEXT",
            "source_path": "TEXT",
            "extracted_text": "TEXT NOT NULL DEFAULT ''",
            "uploaded_by": "TEXT",
            "uploaded_at": "TEXT",
            "committed_by": "TEXT",
            "committed_at": "TEXT",
            "change_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "impact_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, declaration in logic_additions.items():
            if name not in logic_columns:
                connection.execute(f"ALTER TABLE logic_models ADD COLUMN {name} {declaration}")

        if not connection.execute(
            "SELECT 1 FROM logic_models WHERE is_current = 1 LIMIT 1"
        ).fetchone():
            first = connection.execute("SELECT id FROM logic_models ORDER BY rowid LIMIT 1").fetchone()
            if first:
                connection.execute("UPDATE logic_models SET is_current = 1 WHERE id = ?", (first["id"],))
        if "workspace_id" not in logic_columns:
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_current_logic_model "
                "ON logic_models(is_current) WHERE is_current = 1"
            )

        element_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(elements)").fetchall()
        }
        if "display_id" not in element_columns:
            connection.execute("ALTER TABLE elements ADD COLUMN display_id TEXT")
            connection.execute("UPDATE elements SET display_id = id WHERE display_id IS NULL")
        element_additions = {
            "stable_id": "TEXT",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "display_number": "INTEGER",
        }
        for name, declaration in element_additions.items():
            if name not in element_columns:
                connection.execute(f"ALTER TABLE elements ADD COLUMN {name} {declaration}")
        connection.execute("UPDATE elements SET stable_id = id WHERE stable_id IS NULL")

        assessment_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(evidence_assessments)").fetchall()
        }
        if "suggested_classification" not in assessment_columns:
            connection.execute("ALTER TABLE evidence_assessments ADD COLUMN suggested_classification TEXT")
        if "suggested_mapping_details" not in assessment_columns:
            connection.execute(
                "ALTER TABLE evidence_assessments "
                "ADD COLUMN suggested_mapping_details TEXT NOT NULL DEFAULT '[]'"
            )
            legacy_assessments = connection.execute(
                "SELECT id, suggested_mapping_ids, mapping_rationale FROM evidence_assessments"
            ).fetchall()
            for row in legacy_assessments:
                details = [
                    {
                        "element_id": element_id,
                        "confidence": None,
                        "rationale": row["mapping_rationale"],
                    }
                    for element_id in json.loads(row["suggested_mapping_ids"] or "[]")
                ]
                connection.execute(
                    "UPDATE evidence_assessments SET suggested_mapping_details = ? WHERE id = ?",
                    (json.dumps(details), row["id"]),
                )
        assessment_additions = {
            "accepted_analysis_id": "TEXT",
            "confirmed_classification": "TEXT",
            "suggested_result_direction": "TEXT",
            "confirmed_result_direction": "TEXT",
            "source_logic_model_id": "TEXT",
            "source_assessment_id": "TEXT",
            "activation_id": "TEXT",
            "change_set_json": "TEXT NOT NULL DEFAULT '[]'",
            "suggestion_version": "INTEGER NOT NULL DEFAULT 1",
            "disposition_reason": "TEXT",
        }
        for name, declaration in assessment_additions.items():
            if name not in assessment_columns:
                connection.execute(
                    f"ALTER TABLE evidence_assessments ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            UPDATE evidence_assessments
            SET accepted_analysis_id = COALESCE(
                accepted_analysis_id,
                (SELECT analysis_id FROM evidence WHERE evidence.id = evidence_id)
            ),
                source_logic_model_id = COALESCE(source_logic_model_id, logic_model_id),
                confirmed_classification = COALESCE(
                    confirmed_classification,
                    suggested_classification,
                    (SELECT a.classification FROM evidence e
                     JOIN analyses a ON a.id = e.analysis_id
                     WHERE e.id = evidence_id)
                ),
                suggested_result_direction = COALESCE(
                    suggested_result_direction,
                    (SELECT a.result_direction FROM evidence e
                     JOIN analyses a ON a.id = e.analysis_id
                     WHERE e.id = evidence_id)
                ),
                confirmed_result_direction = COALESCE(
                    confirmed_result_direction,
                    suggested_result_direction,
                    (SELECT a.result_direction FROM evidence e
                     JOIN analyses a ON a.id = e.analysis_id
                     WHERE e.id = evidence_id)
                ),
                change_set_json = COALESCE(change_set_json, '[]'),
                suggestion_version = COALESCE(suggestion_version, 1)
            """
        )

        assessment_mapping_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(evidence_assessment_mappings)"
            ).fetchall()
        }
        if "confidence" not in assessment_mapping_columns:
            connection.execute(
                "ALTER TABLE evidence_assessment_mappings ADD COLUMN confidence REAL"
            )
        if "rationale" not in assessment_mapping_columns:
            connection.execute(
                "ALTER TABLE evidence_assessment_mappings ADD COLUMN rationale TEXT"
            )
        connection.execute(
            """
            UPDATE evidence_assessment_mappings
            SET confidence = COALESCE(confidence, 1.0),
                rationale = COALESCE(
                    rationale,
                    'Approved by the Reviewer before per-mapping confidence was introduced.'
                )
            WHERE confidence IS NULL OR rationale IS NULL
            """
        )

        activation_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'logic_model_activations'"
        ).fetchone()
        if activation_sql and "'commit'" not in activation_sql["sql"]:
            connection.execute("ALTER TABLE logic_model_activations RENAME TO logic_model_activations_old")
            connection.execute(
                """
                CREATE TABLE logic_model_activations (
                    id TEXT PRIMARY KEY,
                    logic_model_id TEXT NOT NULL,
                    previous_logic_model_id TEXT,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK (reason IN ('upload', 'commit', 'restore')),
                    activated_at TEXT NOT NULL,
                    FOREIGN KEY (logic_model_id) REFERENCES logic_models(id),
                    FOREIGN KEY (previous_logic_model_id) REFERENCES logic_models(id)
                )
                """
            )
            connection.execute(
                "INSERT INTO logic_model_activations SELECT * FROM logic_model_activations_old"
            )
            connection.execute("DROP TABLE logic_model_activations_old")
        activation_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(logic_model_activations)"
            ).fetchall()
        }
        activation_additions = {
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "impact_manifest_json": "TEXT NOT NULL DEFAULT '{}'",
            "error_message": "TEXT",
            "completed_at": "TEXT",
        }
        for name, declaration in activation_additions.items():
            if name not in activation_columns:
                connection.execute(
                    f"ALTER TABLE logic_model_activations ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            UPDATE logic_model_activations
            SET status = COALESCE(status, 'completed'),
                impact_manifest_json = COALESCE(impact_manifest_json, '{}'),
                completed_at = COALESCE(completed_at, activated_at)
            """
        )

        analysis_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(analyses)").fetchall()
        }
        if "result_direction" not in analysis_columns:
            connection.execute(
                "ALTER TABLE analyses ADD COLUMN result_direction TEXT "
                "CHECK (result_direction IN ('Positive', 'Negative'))"
            )
        if "logic_model_id" not in analysis_columns:
            connection.execute("ALTER TABLE analyses ADD COLUMN logic_model_id TEXT")
            connection.execute(
                """
                UPDATE analyses
                SET logic_model_id = (
                    SELECT id FROM logic_models lm
                    WHERE lm.version = analyses.logic_model_version
                    ORDER BY lm.sequence DESC LIMIT 1
                )
                WHERE logic_model_id IS NULL
                """
            )
        if "context_revision_id" not in analysis_columns:
            connection.execute("ALTER TABLE analyses ADD COLUMN context_revision_id TEXT")
        analysis_learning_additions = {
            "learning_policy_version": "TEXT",
            "learning_memory_enabled": "INTEGER NOT NULL DEFAULT 0",
            "learning_snapshot_id": "TEXT",
        }
        for name, declaration in analysis_learning_additions.items():
            if name not in analysis_columns:
                connection.execute(f"ALTER TABLE analyses ADD COLUMN {name} {declaration}")

        mapping_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(analysis_mappings)").fetchall()
        }
        if "confidence" not in mapping_columns:
            connection.execute("ALTER TABLE analysis_mappings ADD COLUMN confidence REAL")
        if "rationale" not in mapping_columns:
            connection.execute("ALTER TABLE analysis_mappings ADD COLUMN rationale TEXT")
        connection.execute(
            """
            UPDATE analysis_mappings
            SET confidence = COALESCE(
                    confidence,
                    (SELECT mapping_confidence FROM analyses WHERE analyses.id = analysis_id)
                ),
                rationale = COALESCE(
                    rationale,
                    (SELECT mapping_rationale FROM analyses WHERE analyses.id = analysis_id)
                )
            WHERE confidence IS NULL OR rationale IS NULL
            """
        )

        statement_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(statements)").fetchall()
        }
        if "current_context_revision_id" not in statement_columns:
            connection.execute(
                "ALTER TABLE statements ADD COLUMN current_context_revision_id TEXT"
            )
        if "context_updated_at" not in statement_columns:
            connection.execute("ALTER TABLE statements ADD COLUMN context_updated_at TEXT")
        statement_additions = {
            "program_irrelevant_at": "TEXT",
            "program_irrelevant_by": "TEXT",
            "program_irrelevant_analysis_id": "TEXT",
            "program_irrelevant_reason": "TEXT",
            "capture_method": "TEXT NOT NULL DEFAULT 'text'",
            "capture_candidate_text": "TEXT",
            "capture_text_edited": "INTEGER NOT NULL DEFAULT 0",
            "capture_provider": "TEXT",
            "capture_provider_version": "TEXT",
            "capture_processing_ms": "INTEGER",
            "capture_result": "TEXT NOT NULL DEFAULT 'success'",
        }
        for name, declaration in statement_additions.items():
            if name not in statement_columns:
                connection.execute(f"ALTER TABLE statements ADD COLUMN {name} {declaration}")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS statement_context_revisions (
                id TEXT PRIMARY KEY,
                statement_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                context_text TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (statement_id, version),
                FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE
            )
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS media_staging (
                id TEXT PRIMARY KEY,
                reporter_id TEXT NOT NULL,
                session_fingerprint TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
                storage_key TEXT NOT NULL UNIQUE,
                media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                candidate_text TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL,
                provider_version TEXT NOT NULL,
                processing_ms INTEGER NOT NULL,
                processing_result TEXT NOT NULL CHECK (
                    processing_result IN ('success', 'no_text')
                ),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS statement_attachments (
                id TEXT PRIMARY KEY,
                statement_id TEXT NOT NULL UNIQUE,
                reporter_id TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('camera', 'library')),
                storage_key TEXT NOT NULL UNIQUE,
                media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_version TEXT NOT NULL,
                processing_ms INTEGER NOT NULL,
                processing_result TEXT NOT NULL CHECK (
                    processing_result IN ('success', 'no_text')
                ),
                created_at TEXT NOT NULL,
                FOREIGN KEY (statement_id) REFERENCES statements(id) ON DELETE CASCADE
            );
            """
        )
        legacy_statements = connection.execute(
            """
            SELECT id, reporter_id, submitted_at
            FROM statements
            WHERE current_context_revision_id IS NULL
            """
        ).fetchall()
        for row in legacy_statements:
            revision_id = f"ctx-legacy-{row['id']}"
            connection.execute(
                """
                INSERT OR IGNORE INTO statement_context_revisions
                    (id, statement_id, version, context_text, created_by, created_at)
                VALUES (?, ?, 1, '', ?, ?)
                """,
                (revision_id, row["id"], row["reporter_id"], row["submitted_at"]),
            )
            connection.execute(
                """
                UPDATE statements
                SET current_context_revision_id = ?, context_updated_at = ?
                WHERE id = ?
                """,
                (revision_id, row["submitted_at"], row["id"]),
            )
            connection.execute(
                """
                UPDATE analyses SET context_revision_id = ?
                WHERE statement_id = ? AND context_revision_id IS NULL
                """,
                (revision_id, row["id"]),
            )
        feedback_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(review_feedback)").fetchall()
        }
        if "corrected_result_direction" not in feedback_columns:
            connection.execute(
                "ALTER TABLE review_feedback ADD COLUMN corrected_result_direction TEXT "
                "CHECK (corrected_result_direction IN ('Positive', 'Negative'))"
            )
        if "corrected_outcome_category" not in feedback_columns:
            connection.execute(
                "ALTER TABLE review_feedback ADD COLUMN corrected_outcome_category TEXT "
                "CHECK (corrected_outcome_category IN ('A', 'B', 'C'))"
            )
        if "corrected_outcome_change_types" not in feedback_columns:
            connection.execute(
                "ALTER TABLE review_feedback "
                "ADD COLUMN corrected_outcome_change_types TEXT NOT NULL DEFAULT '[]'"
            )
        if "reviewer_id" not in feedback_columns:
            connection.execute("ALTER TABLE review_feedback ADD COLUMN reviewer_id TEXT")
        if "language_issue_types" not in feedback_columns:
            connection.execute(
                "ALTER TABLE review_feedback "
                "ADD COLUMN language_issue_types TEXT NOT NULL DEFAULT '[]'"
            )
        evidence_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(evidence)").fetchall()
        }
        if "accepted_by" not in evidence_columns:
            connection.execute("ALTER TABLE evidence ADD COLUMN accepted_by TEXT")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS review_sessions (
                id TEXT PRIMARY KEY,
                reviewer_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
                started_at TEXT NOT NULL,
                completed_at TEXT,
                completion_reason TEXT CHECK (completion_reason IN ('exhausted', 'ended')),
                scoring_policy_version TEXT NOT NULL,
                report_json TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_review_session_per_reviewer
            ON review_sessions(reviewer_id) WHERE status = 'open';
            CREATE TABLE IF NOT EXISTS review_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                statement_id TEXT NOT NULL,
                analysis_id TEXT NOT NULL,
                resulting_analysis_id TEXT,
                reviewer_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (
                    decision IN ('accepted', 'changed', 'not_relevant')
                ),
                comments TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES review_sessions(id),
                FOREIGN KEY (statement_id) REFERENCES statements(id),
                FOREIGN KEY (analysis_id) REFERENCES analyses(id),
                FOREIGN KEY (resulting_analysis_id) REFERENCES analyses(id)
            );
            CREATE TABLE IF NOT EXISTS scoring_events (
                id TEXT PRIMARY KEY,
                cycle_key TEXT NOT NULL UNIQUE,
                cycle_type TEXT NOT NULL CHECK (
                    cycle_type IN ('statement', 'evidence_reassessment')
                ),
                statement_id TEXT,
                evidence_assessment_id TEXT,
                first_review_event_id TEXT NOT NULL UNIQUE,
                first_review_approval INTEGER NOT NULL CHECK (
                    first_review_approval IN (0, 1)
                ),
                correct_level INTEGER NOT NULL CHECK (correct_level IN (0, 1)),
                correct_direction INTEGER CHECK (correct_direction IN (0, 1)),
                correct_mapping INTEGER NOT NULL CHECK (correct_mapping IN (0, 1)),
                overall_hygiene INTEGER NOT NULL CHECK (overall_hygiene IN (0, 1)),
                source_signals_json TEXT NOT NULL,
                explanations_json TEXT NOT NULL,
                scoring_policy_version TEXT NOT NULL,
                interpretation_model_version TEXT,
                interpretation_prompt_version TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (statement_id) REFERENCES statements(id),
                FOREIGN KEY (evidence_assessment_id) REFERENCES evidence_assessments(id),
                FOREIGN KEY (first_review_event_id) REFERENCES review_events(id)
            );
            """
        )
        review_event_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(review_events)").fetchall()
        }
        if "evidence_assessment_id" not in review_event_columns:
            connection.execute(
                "ALTER TABLE review_events ADD COLUMN evidence_assessment_id TEXT"
            )
        if "is_self_review" not in review_event_columns:
            connection.execute(
                """
                ALTER TABLE review_events
                ADD COLUMN is_self_review INTEGER NOT NULL DEFAULT 0
                CHECK (is_self_review IN (0, 1))
                """
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS learning_policies (
                version TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_examples (
                id TEXT PRIMARY KEY, review_event_id TEXT NOT NULL,
                statement_id TEXT NOT NULL, source_analysis_id TEXT NOT NULL,
                resulting_analysis_id TEXT, evidence_assessment_id TEXT,
                example_key TEXT NOT NULL, dimension TEXT NOT NULL,
                signal_type TEXT NOT NULL, original_value_json TEXT,
                final_value_json TEXT, original_confidence REAL,
                truth_weight REAL NOT NULL DEFAULT 1.0,
                surprise_score REAL NOT NULL, learning_priority REAL NOT NULL,
                source_text TEXT NOT NULL, logic_model_id TEXT,
                stable_element_id TEXT, reviewer_id TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                eligibility_status TEXT NOT NULL DEFAULT 'eligible',
                superseded_by TEXT, exclusion_reason TEXT, created_at TEXT NOT NULL,
                UNIQUE (review_event_id, example_key)
            );
            CREATE TABLE IF NOT EXISTS language_feedback (
                id TEXT PRIMARY KEY, review_event_id TEXT NOT NULL UNIQUE,
                statement_id TEXT NOT NULL, issue_types_json TEXT NOT NULL DEFAULT '[]',
                comments TEXT, original_summary TEXT NOT NULL, final_summary TEXT,
                example_status TEXT NOT NULL CHECK (
                    example_status IN ('positive', 'paired', 'negative_only')
                ), created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_snapshots (
                id TEXT PRIMARY KEY, statement_id TEXT NOT NULL, analysis_id TEXT,
                policy_version TEXT NOT NULL, memory_enabled INTEGER NOT NULL,
                eligible_example_ids_json TEXT NOT NULL DEFAULT '[]',
                retrieved_example_ids_json TEXT NOT NULL DEFAULT '[]',
                retrieval_error TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_learning_examples (
                analysis_id TEXT NOT NULL, learning_example_id TEXT NOT NULL,
                rank INTEGER NOT NULL, similarity REAL NOT NULL,
                ranking_score REAL NOT NULL, inclusion_reason TEXT NOT NULL,
                PRIMARY KEY (analysis_id, learning_example_id)
            );
            CREATE TABLE IF NOT EXISTS review_session_score_snapshots (
                session_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL,
                scoring_policy_version TEXT NOT NULL,
                learning_policy_version TEXT NOT NULL, memory_enabled INTEGER NOT NULL,
                scores_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO learning_policies
                (version, enabled, is_active, config_json, created_at)
            VALUES ('reviewer-memory-v1', 1, 1, ?, datetime('now'))
            """,
            (json.dumps({"limit": 8, "rolling_window": 3, "minimum_events": 20}),),
        )

    @staticmethod
    def _apply_workspace_migration(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE IF EXISTS login_challenges")
        workspace_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        if "nickname" not in workspace_columns:
            connection.execute(
                "ALTER TABLE workspaces ADD COLUMN nickname TEXT NOT NULL DEFAULT ''"
            )
        now = "datetime('now')"
        connection.execute(
            f"""
            INSERT OR IGNORE INTO workspaces
                (id, name, nickname, workspace_type, owner_user_id, created_by, status,
                 created_at, updated_at)
            VALUES (
                '{OWNER_WORKSPACE_ID}', 'MELvan', 'MELvan', 'owner', 'user-admin',
                'system-migration', 'active', {now}, {now}
            )
            """
        )
        Database._backfill_workspace_nicknames(connection)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_workspace_nickname
            ON workspaces(lower(nickname))
            """
        )

        session_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(authenticated_sessions)"
            ).fetchall()
        }
        session_additions = {
            "workspace_id": "TEXT",
            "session_kind": "TEXT NOT NULL DEFAULT 'full'",
            "password_setup_purpose": "TEXT",
            "duration_choice": "TEXT NOT NULL DEFAULT 'midnight'",
            "timezone_name": "TEXT NOT NULL DEFAULT 'UTC'",
            "requested_expires_at": "TEXT",
        }
        for name, declaration in session_additions.items():
            if name not in session_columns:
                connection.execute(
                    f"ALTER TABLE authenticated_sessions ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            """
            UPDATE authenticated_sessions
            SET password_setup_purpose = CASE
                WHEN EXISTS (
                    SELECT 1 FROM users
                    WHERE users.id = authenticated_sessions.user_id
                      AND users.total_logins = 0
                      AND users.password_state IN (
                          'bootstrap_required', 'password_change_required'
                      )
                ) THEN 'initial_setup'
                ELSE 'recovery'
            END
            WHERE session_kind = 'password_change'
              AND password_setup_purpose IS NULL
            """
        )
        connection.execute(
            """
            UPDATE authenticated_sessions
            SET workspace_id = COALESCE(
                    workspace_id,
                    (SELECT workspace_id FROM workspace_memberships
                     WHERE user_id = authenticated_sessions.user_id)
                ),
                requested_expires_at = COALESCE(requested_expires_at, expires_at)
            """
        )

        draft_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(logic_model_drafts)").fetchall()
        }
        if "workspace_id" not in draft_columns:
            connection.execute("ALTER TABLE logic_model_drafts RENAME TO logic_model_drafts_pre_v8")
            connection.execute(
                """
                CREATE TABLE logic_model_drafts (
                    workspace_id TEXT PRIMARY KEY,
                    base_logic_model_id TEXT,
                    draft_json TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
                    FOREIGN KEY (base_logic_model_id) REFERENCES logic_models(id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO logic_model_drafts
                    (workspace_id, base_logic_model_id, draft_json, updated_by, updated_at)
                SELECT ?, base_logic_model_id, draft_json, updated_by, updated_at
                FROM logic_model_drafts_pre_v8
                """,
                (OWNER_WORKSPACE_ID,),
            )
            connection.execute("DROP TABLE logic_model_drafts_pre_v8")

        for table in PROGRAM_TABLES:
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "workspace_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN workspace_id TEXT")
            connection.execute(
                f"UPDATE {table} SET workspace_id = ? WHERE workspace_id IS NULL",
                (OWNER_WORKSPACE_ID,),
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_workspace ON {table}(workspace_id)"
            )
            connection.execute(f"DROP TRIGGER IF EXISTS {table}_workspace_insert")
            connection.execute(
                f"""
                CREATE TRIGGER {table}_workspace_insert
                AFTER INSERT ON {table}
                BEGIN
                    UPDATE {table}
                    SET workspace_id = COALESCE(NEW.workspace_id, melvan_workspace_id())
                    WHERE rowid = NEW.rowid;
                    SELECT CASE
                        WHEN (SELECT workspace_id FROM {table} WHERE rowid = NEW.rowid)
                             <> melvan_workspace_id()
                        THEN RAISE(ABORT, 'workspace context mismatch')
                    END;
                    SELECT CASE
                        WHEN NOT EXISTS (
                            SELECT 1 FROM workspaces
                            WHERE id = (
                                SELECT workspace_id FROM {table} WHERE rowid = NEW.rowid
                            )
                        )
                        THEN RAISE(ABORT, 'workspace does not exist')
                    END;
                END
                """
            )
            connection.execute(f"DROP TRIGGER IF EXISTS {table}_workspace_immutable")
            connection.execute(
                f"""
                CREATE TRIGGER {table}_workspace_immutable
                BEFORE UPDATE OF workspace_id ON {table}
                WHEN OLD.workspace_id <> NEW.workspace_id
                BEGIN
                    SELECT RAISE(ABORT, 'workspace ownership is immutable');
                END
                """
            )

        connection.execute("DROP INDEX IF EXISTS one_current_logic_model")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_current_logic_model_per_workspace
            ON logic_models(workspace_id, is_current) WHERE is_current = 1
            """
        )
        connection.execute("DROP INDEX IF EXISTS one_active_learning_policy")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_learning_policy_per_workspace
            ON learning_policies(workspace_id, is_active) WHERE is_active = 1
            """
        )

        relationship_checks = {
            "elements": (
                ("logic_model_id", "logic_models", "id"),
            ),
            "logic_model_drafts": (
                ("base_logic_model_id", "logic_models", "id"),
            ),
            "statements": (),
            "statement_context_revisions": (
                ("statement_id", "statements", "id"),
            ),
            "media_staging": (),
            "statement_attachments": (
                ("statement_id", "statements", "id"),
            ),
            "analyses": (
                ("statement_id", "statements", "id"),
                ("logic_model_id", "logic_models", "id"),
                ("context_revision_id", "statement_context_revisions", "id"),
            ),
            "analysis_mappings": (
                ("analysis_id", "analyses", "id"),
                ("element_id", "elements", "id"),
            ),
            "review_feedback": (
                ("statement_id", "statements", "id"),
                ("analysis_id", "analyses", "id"),
            ),
            "evidence": (
                ("statement_id", "statements", "id"),
                ("analysis_id", "analyses", "id"),
            ),
            "evidence_assessments": (
                ("evidence_id", "evidence", "id"),
                ("logic_model_id", "logic_models", "id"),
            ),
            "evidence_assessment_mappings": (
                ("assessment_id", "evidence_assessments", "id"),
                ("element_id", "elements", "id"),
            ),
            "review_events": (
                ("session_id", "review_sessions", "id"),
                ("statement_id", "statements", "id"),
                ("analysis_id", "analyses", "id"),
            ),
            "learning_examples": (
                ("statement_id", "statements", "id"),
                ("source_analysis_id", "analyses", "id"),
            ),
            "language_feedback": (
                ("statement_id", "statements", "id"),
            ),
            "learning_snapshots": (
                ("statement_id", "statements", "id"),
            ),
        }
        for table, checks in relationship_checks.items():
            connection.execute(f"DROP TRIGGER IF EXISTS {table}_same_workspace")
            if not checks:
                continue
            clauses = []
            for column, parent_table, parent_key in checks:
                clauses.append(
                    f"""
                    SELECT CASE
                        WHEN NEW.{column} IS NOT NULL
                         AND NOT EXISTS (
                            SELECT 1 FROM {parent_table}
                            WHERE {parent_key} = NEW.{column}
                              AND workspace_id = COALESCE(
                                  NEW.workspace_id, melvan_workspace_id()
                              )
                         )
                        THEN RAISE(ABORT, 'cross-workspace relationship denied')
                    END;
                    """
                )
            connection.execute(
                f"""
                CREATE TRIGGER {table}_same_workspace
                BEFORE INSERT ON {table}
                BEGIN
                    {''.join(clauses)}
                END
                """
            )

    @staticmethod
    def _backfill_workspace_nicknames(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT w.id, w.name, w.nickname, w.workspace_type, u.email
            FROM workspaces w
            LEFT JOIN users u ON u.id = w.owner_user_id
            ORDER BY CASE w.workspace_type WHEN 'owner' THEN 0 ELSE 1 END,
                     w.created_at, w.id
            """
        ).fetchall()
        used: set[str] = set()
        for row in rows:
            if row["workspace_type"] == "owner":
                base = "MELvan"
            else:
                existing = " ".join(str(row["nickname"] or "").split())
                if existing:
                    base = existing
                else:
                    name = " ".join(str(row["name"] or "Workspace").split())
                    email_local = str(row["email"] or "workspace").partition("@")[0]
                    base = f"{name} ({email_local})"
            candidate = Database._unique_workspace_nickname(base, used)
            used.add(candidate.casefold())
            if candidate != row["nickname"]:
                connection.execute(
                    "UPDATE workspaces SET nickname = ?, updated_at = datetime('now') WHERE id = ?",
                    (candidate, row["id"]),
                )

    @staticmethod
    def _unique_workspace_nickname(base: str, used: set[str]) -> str:
        normalized = " ".join(str(base or "Workspace").split())[:40] or "Workspace"
        candidate = normalized
        sequence = 2
        while candidate.casefold() in used:
            suffix = f" {sequence}"
            candidate = f"{normalized[:40 - len(suffix)].rstrip()}{suffix}"
            sequence += 1
        return candidate

    @contextmanager
    def workspace(
        self, workspace_id: str, *, read_only: bool = False
    ) -> Iterator[None]:
        token = _workspace_context.set(str(workspace_id or OWNER_WORKSPACE_ID))
        try:
            yield
        finally:
            _workspace_context.reset(token)

    @staticmethod
    def current_workspace_id() -> str:
        return _workspace_context.get()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # The hosted-demo architecture permits one process and one replica. Serializing
        # transactions keeps the local SQLite database single-writer; a durable snapshot
        # is copied to network storage only after a successful commit.
        with self._transaction_lock:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "melvan_workspace_id", 0, self.current_workspace_id
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            changes_before = connection.total_changes
            committed = False
            try:
                yield connection
                connection.commit()
                committed = connection.total_changes > changes_before
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            if committed:
                self._persist_snapshot()

    def _restore_snapshot(self) -> None:
        if self._snapshot_restored:
            return
        self._snapshot_restored = True
        if self.snapshot_path is None or not self.snapshot_path.is_file():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.snapshot_path, self.path)

    def _persist_snapshot(self) -> None:
        if self.snapshot_path is None:
            return
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_name(
            f".{self.snapshot_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with self.path.open("rb") as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _load_fixture(connection: sqlite3.Connection, fixture_path: Path) -> None:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        has_models = connection.execute("SELECT 1 FROM logic_models LIMIT 1").fetchone() is not None
        connection.execute(
            """
            INSERT OR IGNORE INTO logic_models
                (id, title, version, sequence, is_current, source, fixture_json,
                 source_filename, media_type, extracted_text, uploaded_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                fixture["id"],
                fixture["title"],
                fixture["version"],
                0 if has_models else 1,
                fixture["source"],
                json.dumps(fixture, ensure_ascii=True),
                Path(fixture["source"]).name,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "\n".join(element["label"] for element in fixture["elements"]),
            ),
        )
        for element in fixture["elements"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO elements
                    (id, display_id, logic_model_id, label, element_type, level,
                     group_name, source, status, created_at, stable_id, revision, display_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'fixture', 'published', datetime('now'), ?, 1, ?)
                """,
                (
                    element["id"],
                    element["id"],
                    fixture["id"],
                    element["label"],
                    element["type"],
                    element["level"],
                    element.get("group"),
                    element["id"],
                    _fixture_number(fixture["elements"], element),
                ),
            )
        for relationship in fixture["relationships"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO element_relationships
                    (from_element_id, to_element_id, relationship_type)
                VALUES (?, ?, ?)
                """,
                (relationship["from"], relationship["to"], relationship["type"]),
            )
