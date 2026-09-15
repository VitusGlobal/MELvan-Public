"""MELvan workflow and persistence operations."""

from __future__ import annotations

import json
import re
import uuid
from base64 import b64decode
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .db import (
    DATABASE_INTEGRITY_ERRORS,
    DatabaseAdapter,
    DatabaseConnection,
    DatabaseRow,
)
from .identity import IdentityError, UserDirectory
from .learning import LearningContext, LearningMemory
from .media import (
    AttachmentStorage,
    AttachmentStore,
    MAX_AUDIO_BYTES,
    MediaError,
    PhotoTextExtractor,
    SpeechTranscriber,
    DemoSpeechTranscriber,
    STAGING_TTL_SECONDS,
)
from .model import AnalysisResult, MappingDecision, ModelAdapter, ModelError
from .pdf_export import build_evidence_pdf, export_filename


RECAP_KEYS = ("risks", "evidence", "context", "assumptions", "people")
OUTCOME_LEVELS = {
    "short_term_outcome": 9,
    "medium_term_outcome": 7,
    "long_term_outcome": 5,
    "ultimate_goal": 1,
}
SCORING_POLICY_VERSION = "review-scoring-v2"
COMMENT_INTERPRETATION_VERSION = "deterministic-review-feedback-v1"
EVIDENCE_CONFIDENCE_REVIEW_THRESHOLD = 0.25
MAPPING_DISPLAY_CONFIDENCE_THRESHOLD = 0.25
MAPPING_SUPPORTED_CONFIDENCE_THRESHOLD = 0.50
MAX_TENTATIVE_MAPPINGS = 3
ACTIVATION_POLICY_VERSION = "logic-model-reassessment-v1"


class DomainError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class MelvanService:
    def __init__(
        self,
        database: DatabaseAdapter,
        model: ModelAdapter,
        fixture_path: Path,
        *,
        user_directory: UserDirectory | None = None,
        initial_admin_email: str | None = None,
        attachment_store: AttachmentStorage | None = None,
        speech_transcriber: SpeechTranscriber | None = None,
        photo_text_extractor: Any | None = None,
    ):
        self.database = database
        self.model = model
        self.fixture_path = fixture_path
        self.users = user_directory or UserDirectory(
            database,
            legacy_path=database.path.parent / "users.json",
            initial_owner_email=initial_admin_email,
        )
        self.learning = LearningMemory(database)
        self.attachments = attachment_store or AttachmentStore(
            database.path.parent / "attachments"
        )
        self.speech = speech_transcriber or DemoSpeechTranscriber()
        self.photo_text = photo_text_extractor or PhotoTextExtractor(model)

    def initialize(self) -> None:
        with self.database.startup_lock():
            self.database.initialize(self.fixture_path)
            self.attachments.initialize()
            self.users.initialize()
            self._backfill_evidence_assessments()
            self.cleanup_expired_media()

    @contextmanager
    def workspace(self, workspace_id: str, *, read_only: bool = False):
        with self.database.workspace(workspace_id, read_only=read_only):
            yield

    def _workspace_id(self) -> str:
        return self.database.current_workspace_id()

    def create_statement(
        self,
        text: str,
        reporter_id: str = "reporter-demo",
        context: str = "",
        capture: dict[str, Any] | None = None,
        session_fingerprint: str = "",
    ) -> dict[str, Any]:
        original_text = str(text or "")
        if not original_text.strip():
            raise DomainError("Enter a Statement before submitting it.")
        if len(original_text) > 10_000:
            raise DomainError("The Statement must be 10,000 characters or fewer.")
        context_text = self._validate_statement_context(context)
        capture_data = self._validate_capture(capture)
        capture_method = capture_data["method"]
        if capture_method in {"voice", "photo"}:
            capture_data["edited"] = (
                original_text.strip()
                != str(capture_data.get("candidate_text") or "").strip()
            )
        staged_photo: DatabaseRow | None = None
        if capture_method == "photo":
            staged_photo = self._staged_photo(
                str(capture_data.get("stage_id") or ""),
                reporter_id,
                session_fingerprint,
            )
            capture_data.update(
                {
                    "candidate_text": staged_photo["candidate_text"],
                    "provider": staged_photo["provider"],
                    "provider_version": staged_photo["provider_version"],
                    "processing_ms": staged_photo["processing_ms"],
                    "outcome": staged_photo["processing_result"],
                }
            )
            capture_data["edited"] = (
                original_text.strip() != staged_photo["candidate_text"].strip()
            )

        statement_id = self._new_id("stmt")
        context_revision_id = self._new_id("ctx")
        submitted_at = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO statements
                    (id, original_text, reporter_id, submitted_at, status,
                     current_context_revision_id, context_updated_at,
                     capture_method, capture_candidate_text, capture_text_edited,
                     capture_provider, capture_provider_version,
                     capture_processing_ms, capture_result)
                VALUES (?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    statement_id,
                    original_text,
                    reporter_id,
                    submitted_at,
                    context_revision_id,
                    submitted_at,
                    capture_method,
                    capture_data.get("candidate_text"),
                    int(bool(capture_data.get("edited"))),
                    capture_data.get("provider"),
                    capture_data.get("provider_version"),
                    capture_data.get("processing_ms"),
                    capture_data.get("outcome", "success"),
                ),
            )
            connection.execute(
                """
                INSERT INTO statement_context_revisions
                    (id, statement_id, version, context_text, created_by, created_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (
                    context_revision_id,
                    statement_id,
                    context_text,
                    reporter_id,
                    submitted_at,
                ),
            )
            self._audit(
                connection,
                "statement_submitted",
                "statement",
                statement_id,
                {
                    "reporter_id": reporter_id,
                    "context_revision_id": context_revision_id,
                    "active_role": "reporter",
                    "capture_method": capture_method,
                },
            )
            if staged_photo:
                attachment_id = self._new_id("attachment")
                final_key = None
                try:
                    final_key = self.attachments.promote(
                        staged_photo["storage_key"],
                        self._workspace_id(),
                        statement_id,
                        attachment_id,
                    )
                    connection.execute(
                        """
                        INSERT INTO statement_attachments
                            (id, statement_id, reporter_id, source, storage_key,
                             media_type, byte_size, width, height, checksum,
                             provider, provider_version, processing_ms,
                             processing_result, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            attachment_id,
                            statement_id,
                            reporter_id,
                            staged_photo["source"],
                            final_key,
                            staged_photo["media_type"],
                            staged_photo["byte_size"],
                            staged_photo["width"],
                            staged_photo["height"],
                            staged_photo["checksum"],
                            staged_photo["provider"],
                            staged_photo["provider_version"],
                            staged_photo["processing_ms"],
                            staged_photo["processing_result"],
                            submitted_at,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM media_staging WHERE id = ?",
                        (staged_photo["id"],),
                    )
                except Exception:
                    if final_key:
                        self.attachments.delete(final_key)
                    raise

        logic_model_id = self._current_logic_model_id()
        learning_context = self.learning.prepare_context(
            statement_id, original_text, logic_model_id
        )
        try:
            result = self._apply_mapping_proposal_threshold(
                self.model.analyze(
                    original_text,
                    self._catalogue_for_matching(logic_model_id),
                    statement_context=context_text,
                    approved_examples=learning_context.examples,
                )
            )
            with self.database.connection() as connection:
                analysis_id = self._persist_analysis(
                    connection,
                    statement_id,
                    result,
                    logic_model_id=logic_model_id,
                    context_revision_id=context_revision_id,
                    learning_context=learning_context,
                )
                connection.execute(
                    """
                    UPDATE statements
                    SET status = 'awaiting_review', active_analysis_id = ?,
                        initial_classification = ?, processing_error = NULL
                    WHERE id = ?
                    """,
                    (analysis_id, result.classification, statement_id),
                )
                self._audit(
                    connection,
                    "analysis_created",
                    "statement",
                    statement_id,
                    {"analysis_id": analysis_id, "classification": result.classification},
                )
        except (ModelError, ValueError, KeyError, TypeError) as exc:
            with self.database.connection() as connection:
                connection.execute(
                    "UPDATE statements SET status = 'failed', processing_error = ? WHERE id = ?",
                    (str(exc), statement_id),
                )
                self._audit(
                    connection,
                    "analysis_failed",
                    "statement",
                    statement_id,
                    {"error": str(exc)},
                )
            raise DomainError(f"The Statement was saved, but analysis failed: {exc}", status=502) from exc

        return self.get_statement(statement_id)

    def transcribe_voice(
        self, encoded_audio: str, media_type: str
    ) -> dict[str, Any]:
        audio = self._decode_media(encoded_audio, MAX_AUDIO_BYTES, "recording")
        if not media_type.startswith(("audio/", "video/webm")):
            raise DomainError("This recording format is not supported.")
        try:
            return self.speech.transcribe(audio, media_type).to_dict()
        except MediaError as exc:
            raise DomainError(str(exc), status=502) from exc
        finally:
            audio = b""

    def stage_photo(
        self,
        encoded_photo: str,
        source: str,
        reporter_id: str,
        session_fingerprint: str,
    ) -> dict[str, Any]:
        if source not in {"camera", "library"}:
            raise DomainError("Choose whether the photo came from the camera or library.")
        self.cleanup_expired_media()
        photo_bytes = self._decode_media(
            encoded_photo, 10 * 1024 * 1024, "photo"
        )
        with self.database.connection() as connection:
            prior = connection.execute(
                """
                SELECT id, storage_key FROM media_staging
                WHERE reporter_id = ? AND session_fingerprint = ?
                  AND workspace_id = melvan_workspace_id()
                """,
                (reporter_id, session_fingerprint),
            ).fetchall()
            for row in prior:
                self.attachments.delete(row["storage_key"])
            connection.execute(
                """
                DELETE FROM media_staging
                WHERE reporter_id = ? AND session_fingerprint = ?
                  AND workspace_id = melvan_workspace_id()
                """,
                (reporter_id, session_fingerprint),
            )
        storage_key = None
        try:
            stage_id, normalized, storage_key = self.attachments.stage(photo_bytes)
            extracted = self.photo_text.extract(normalized.content, normalized.media_type)
        except MediaError as exc:
            if storage_key:
                self.attachments.delete(storage_key)
            raise DomainError(str(exc), status=502) from exc
        now = datetime.now(timezone.utc)
        try:
            with self.database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO media_staging
                        (id, reporter_id, session_fingerprint, source, storage_key,
                         media_type, byte_size, width, height, checksum,
                         candidate_text, provider, provider_version, processing_ms,
                         processing_result, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stage_id,
                        reporter_id,
                        session_fingerprint,
                        source,
                        storage_key,
                        normalized.media_type,
                        len(normalized.content),
                        normalized.width,
                        normalized.height,
                        normalized.checksum,
                        extracted.text,
                        extracted.provider,
                        extracted.provider_version,
                        extracted.processing_ms,
                        extracted.outcome,
                        now.isoformat(timespec="seconds"),
                        (now + timedelta(seconds=STAGING_TTL_SECONDS)).isoformat(
                            timespec="seconds"
                        ),
                    ),
                )
        except Exception:
            self.attachments.delete(storage_key)
            raise
        return {
            "stage_id": stage_id,
            "candidate_text": extracted.text,
            "provider": extracted.provider,
            "provider_version": extracted.provider_version,
            "processing_ms": extracted.processing_ms,
            "outcome": extracted.outcome,
            "media_type": normalized.media_type,
        }

    def discard_staged_photo(
        self, stage_id: str, reporter_id: str, session_fingerprint: str
    ) -> None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT storage_key FROM media_staging
                WHERE id = ? AND reporter_id = ? AND session_fingerprint = ?
                  AND workspace_id = melvan_workspace_id()
                """,
                (stage_id, reporter_id, session_fingerprint),
            ).fetchone()
            if not row:
                return
            connection.execute("DELETE FROM media_staging WHERE id = ?", (stage_id,))
        self.attachments.delete(row["storage_key"])

    def cleanup_expired_media(self) -> None:
        now = utc_now()
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT id, storage_key FROM media_staging WHERE expires_at <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                self.attachments.delete(row["storage_key"])
            connection.execute("DELETE FROM media_staging WHERE expires_at <= ?", (now,))

    def attachment_content(
        self,
        attachment_id: str,
        user_id: str,
        roles: set[str],
        *,
        observer_mode: bool = False,
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT sa.*, s.status, s.reporter_id AS statement_reporter_id,
                       s.program_irrelevant_at
                FROM statement_attachments sa
                JOIN statements s ON s.id = sa.statement_id
                WHERE sa.id = ? AND sa.workspace_id = melvan_workspace_id()
                """,
                (attachment_id,),
            ).fetchone()
        authorized = bool(
            row
            and not row["program_irrelevant_at"]
            and (
                (roles == {"reporter"} and row["statement_reporter_id"] == user_id)
                or "reviewer" in roles
                or "administrator" in roles
                or observer_mode
            )
        )
        if not authorized:
            raise DomainError("Photo not found.", status=404)
        try:
            content = self.attachments.read(row["storage_key"])
        except MediaError as exc:
            raise DomainError("Photo not found.", status=404) from exc
        return {
            "media_type": row["media_type"],
            "data": content,
        }

    def update_statement_context(
        self, statement_id: str, context: str, reporter_id: str
    ) -> dict[str, Any]:
        context_text = self._validate_statement_context(context)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM statements
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (statement_id,),
            ).fetchone()
            if not row or row["reporter_id"] != reporter_id:
                if row:
                    self._audit(
                        connection,
                        "statement_context_edit_denied",
                        "statement",
                        statement_id,
                        {
                            "reporter_id": reporter_id,
                            "reason": "not_owner",
                            "active_role": "reporter",
                        },
                    )
                raise DomainError("Statement not found.", status=404)
            if row["status"] == "evidence":
                self._audit(
                    connection,
                    "statement_context_edit_denied",
                    "statement",
                    statement_id,
                    {"reporter_id": reporter_id, "reason": "evidence_locked"},
                )
                raise DomainError("Evidence Context cannot be edited.", status=409)
            current = connection.execute(
                """
                SELECT * FROM statement_context_revisions
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (row["current_context_revision_id"],),
            ).fetchone()
            if current and current["context_text"] == context_text:
                return self._statement_from_row(connection, row)
            version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM statement_context_revisions
                WHERE statement_id = ? AND workspace_id = melvan_workspace_id()
                """,
                (statement_id,),
            ).fetchone()[0]
            context_revision_id = self._new_id("ctx")
            changed_at = utc_now()
            connection.execute(
                """
                INSERT INTO statement_context_revisions
                    (id, statement_id, version, context_text, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context_revision_id,
                    statement_id,
                    version,
                    context_text,
                    reporter_id,
                    changed_at,
                ),
            )
            connection.execute(
                """
                UPDATE statements
                SET current_context_revision_id = ?, context_updated_at = ?,
                    status = 'processing', active_analysis_id = NULL, processing_error = NULL
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (context_revision_id, changed_at, statement_id),
            )
            self._audit(
                connection,
                "statement_context_revised",
                "statement",
                statement_id,
                {
                    "reporter_id": reporter_id,
                    "previous_context_revision_id": (
                        current["id"] if current else None
                    ),
                    "context_revision_id": context_revision_id,
                    "version": version,
                    "active_role": "reporter",
                },
            )

        logic_model_id = self._current_logic_model_id()
        learning_context = self.learning.prepare_context(
            statement_id, row["original_text"], logic_model_id
        )
        try:
            result = self._apply_mapping_proposal_threshold(
                self.model.analyze(
                    row["original_text"],
                    self._catalogue_for_matching(logic_model_id),
                    statement_context=context_text,
                    approved_examples=learning_context.examples,
                )
            )
            with self.database.connection() as connection:
                latest = connection.execute(
                    """
                    SELECT current_context_revision_id FROM statements
                    WHERE id = ? AND workspace_id = melvan_workspace_id()
                    """,
                    (statement_id,),
                ).fetchone()
                if not latest or latest["current_context_revision_id"] != context_revision_id:
                    raise DomainError(
                        "Statement Context changed again during analysis. Reload and try again.",
                        status=409,
                    )
                analysis_id = self._persist_analysis(
                    connection,
                    statement_id,
                    result,
                    logic_model_id=logic_model_id,
                    context_revision_id=context_revision_id,
                    learning_context=learning_context,
                )
                connection.execute(
                    """
                    UPDATE statements
                    SET status = 'awaiting_review', active_analysis_id = ?,
                        processing_error = NULL
                    WHERE id = ? AND workspace_id = melvan_workspace_id()
                    """,
                    (analysis_id, statement_id),
                )
                self._audit(
                    connection,
                    "statement_context_reanalysis_created",
                    "statement",
                    statement_id,
                    {
                        "analysis_id": analysis_id,
                        "context_revision_id": context_revision_id,
                    },
                )
        except DomainError:
            raise
        except (ModelError, ValueError, KeyError, TypeError) as exc:
            with self.database.connection() as connection:
                connection.execute(
                    """
                    UPDATE statements SET status = 'failed', processing_error = ?
                    WHERE id = ? AND current_context_revision_id = ?
                      AND workspace_id = melvan_workspace_id()
                    """,
                    (str(exc), statement_id, context_revision_id),
                )
                self._audit(
                    connection,
                    "statement_context_reanalysis_failed",
                    "statement",
                    statement_id,
                    {
                        "error": str(exc),
                        "context_revision_id": context_revision_id,
                    },
                )
            raise DomainError(
                f"Context was saved, but analysis failed: {exc}", status=502
            ) from exc
        return self.get_statement_for_reporter(statement_id, reporter_id)

    def submit_feedback(
        self,
        statement_id: str,
        payload: dict[str, Any],
        reviewer_id: str = "reviewer-demo",
        *,
        accept_as_evidence: bool = False,
    ) -> dict[str, Any]:
        corrected = payload.get("corrected_classification") or None
        corrected_direction = payload.get("corrected_result_direction") or None
        comments = str(payload.get("comments") or "").strip() or None
        try:
            language_issue_types = self.learning.validate_language_feedback(
                payload.get("language_issue_types"), comments
            )
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        mapping_supplied = "mapping_ids" in payload
        mapping_ids = list(dict.fromkeys(payload.get("mapping_ids") or []))
        new_element = payload.get("new_element") or None
        reject_proposal = bool(payload.get("reject_proposal"))
        displayed_analysis_id = str(payload.get("analysis_id") or "")

        if corrected and corrected not in {"Activity", "Output", "Outcome", "Indicator"}:
            raise DomainError("Choose a valid corrected classification.")
        if corrected_direction and corrected_direction not in {"Positive", "Negative"}:
            raise DomainError("Choose a valid result direction.")
        if new_element:
            raise DomainError("Only Administrators can change the Logic Model.", status=403)
        if not any(
            [corrected, corrected_direction, comments, language_issue_types,
             mapping_supplied, new_element, reject_proposal]
        ):
            raise DomainError("Provide at least one correction or comment.")

        current = self._review_context(statement_id, displayed_analysis_id, reviewer_id)
        current_analysis = current["analysis"]
        current_proposal = current.get("proposed_element")
        if reject_proposal and not current_proposal:
            raise DomainError("This analysis has no provisional element to reject.")
        if mapping_ids:
            self._validate_mapping_ids(mapping_ids)
        final_classification = corrected or current_analysis["classification"]
        if corrected_direction and final_classification != "Outcome":
            raise DomainError("Result direction applies only to an Outcome.")

        structured_instructions = []
        if corrected:
            structured_instructions.append(f"Reviewer classification: {corrected}.")
        if corrected_direction:
            structured_instructions.append(
                f"Reviewer result direction: {corrected_direction}."
            )
        if mapping_supplied:
            structured_instructions.append(
                f"Reviewer selected existing element IDs: {', '.join(mapping_ids)}."
                if mapping_ids
                else "Reviewer selected no existing Logic Model mapping."
            )
        if new_element:
            structured_instructions.append(
                f"Reviewer created {new_element['element_type']}: {new_element['label']}."
            )
        if reject_proposal:
            structured_instructions.append("Reviewer rejected the current proposed element.")
        if comments:
            structured_instructions.append(comments)
        reviewer_instruction = " ".join(structured_instructions)

        logic_model_id = self._current_logic_model_id()
        learning_context = self.learning.prepare_context(
            statement_id, current["original_text"], logic_model_id
        )
        result = self._apply_mapping_proposal_threshold(
            self.model.analyze(
                current["original_text"],
                self._catalogue_for_matching(logic_model_id),
                statement_context=current.get("context", {}).get("text", ""),
                current_analysis=current_analysis,
                reviewer_comments=reviewer_instruction,
                rejected_proposal=current_proposal["label"] if reject_proposal else None,
                approved_examples=learning_context.examples,
            )
        )
        result = self._apply_authoritative_result(result, corrected, corrected_direction)
        feedback_id = self._new_id("feedback")
        rejected_snapshot = None
        with self.database.connection() as connection:
            created_element_id = None
            authoritative_mapping_ids: list[str] | None = None
            if new_element:
                created_element_id = self._new_id("RW")
                connection.execute(
                    """
                    INSERT INTO elements
                        (id, display_id, logic_model_id, label, element_type, level, group_name,
                         source, status, created_at)
                    VALUES (?, ?, NULL, ?, ?, ?, 'Reviewer-created', 'reviewer', 'approved', ?)
                    """,
                    (
                        created_element_id,
                        created_element_id,
                        new_element["label"],
                        new_element["element_type"],
                        "working_outcome",
                        utc_now(),
                    ),
                )
                authoritative_mapping_ids = [created_element_id]
                self._audit(
                    connection,
                    "working_element_created",
                    "element",
                    created_element_id,
                    {"source": "reviewer", "label": new_element["label"]},
                )
            elif mapping_supplied:
                authoritative_mapping_ids = mapping_ids

            if reject_proposal and current_proposal:
                rejected_snapshot = dict(current_proposal)
                connection.execute(
                    """
                    DELETE FROM elements
                    WHERE id = ? AND source = 'model' AND status = 'provisional'
                      AND workspace_id = melvan_workspace_id()
                    """,
                    (current_proposal["id"],),
                )
                self._audit(
                    connection,
                    "provisional_element_rejected",
                    "element",
                    current_proposal["id"],
                    rejected_snapshot,
                )
                if authoritative_mapping_ids is None:
                    result = replace(
                        result,
                        match_status="no_suitable_match",
                        mapping_decisions=[],
                        proposed_element=None,
                        no_match_rationale="The Reviewer rejected the proposed element.",
                    )

            if authoritative_mapping_ids is not None:
                result = replace(
                    result,
                    match_status="existing_match",
                    mapping_decisions=[
                        MappingDecision(
                            element_id=element_id,
                            confidence=1.0,
                            rationale="Applied directly from authoritative Reviewer feedback.",
                        )
                        for element_id in authoritative_mapping_ids
                    ],
                    proposed_element=None,
                    no_match_rationale="",
                )
            if accept_as_evidence and not result.mapping_decisions:
                raise DomainError(
                    "A Statement must have at least one approved Logic Model mapping before it can become Evidence."
                )

            analysis_id = self._persist_analysis(
                connection,
                statement_id,
                result,
                reviewer_instruction=reviewer_instruction,
                logic_model_id=logic_model_id,
                learning_context=learning_context,
            )
            connection.execute(
                """
                INSERT INTO review_feedback
                    (id, statement_id, analysis_id, corrected_classification,
                     corrected_result_direction, comments,
                     mapping_ids, created_element_id, rejected_proposal_snapshot,
                     reviewer_id, language_issue_types, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    statement_id,
                    displayed_analysis_id,
                    corrected,
                    corrected_direction,
                    comments,
                    json.dumps(mapping_ids),
                    created_element_id,
                    json.dumps(rejected_snapshot) if rejected_snapshot else None,
                    reviewer_id,
                    json.dumps(language_issue_types),
                    utc_now(),
                ),
            )
            connection.execute(
                """
                UPDATE statements
                SET active_analysis_id = ?, status = 'awaiting_review', processing_error = NULL
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (analysis_id, statement_id),
            )
            self._audit(
                connection,
                "review_feedback_applied",
                "statement",
                statement_id,
                {
                    "feedback_id": feedback_id,
                    "analysis_id": analysis_id,
                    "reviewer_id": reviewer_id,
                },
            )
            self._record_review_event(
                connection,
                reviewer_id=reviewer_id,
                statement_id=statement_id,
                analysis_id=displayed_analysis_id,
                resulting_analysis_id=analysis_id,
                decision="changed",
                comments=comments,
                score_context={
                    "current_analysis": current_analysis,
                    "current_mappings": current["mappings"],
                    "selected_mapping_ids": mapping_ids if mapping_supplied else None,
                    "corrected_classification": corrected,
                    "corrected_result_direction": corrected_direction,
                    "mapping_supplied": mapping_supplied,
                    "new_element": bool(new_element),
                    "reject_proposal": reject_proposal,
                    "comments": comments,
                    "language_issue_types": language_issue_types,
                },
                language_issue_types=language_issue_types,
            )
            if accept_as_evidence:
                statement_row = connection.execute(
                    """
                    SELECT * FROM statements
                    WHERE id = ? AND workspace_id = melvan_workspace_id()
                    """,
                    (statement_id,),
                ).fetchone()
                revised = self._statement_from_row(connection, statement_row)
                self._accept_statement_in_connection(
                    connection,
                    revised,
                    statement_id,
                    analysis_id,
                    reviewer_id,
                    utc_now(),
                )
                self._complete_if_exhausted(connection, reviewer_id)
        return self.get_statement(statement_id)

    def accept_statement(
        self,
        statement_id: str,
        analysis_id: str,
        reviewer_id: str = "reviewer-demo",
    ) -> dict[str, Any]:
        current = self._review_context(statement_id, analysis_id, reviewer_id)
        if not any(mapping.get("logic_model_id") for mapping in current["mappings"]):
            raise DomainError(
                "A Statement must have at least one approved Logic Model mapping before it can become Evidence."
            )
        now = utc_now()
        with self.database.connection() as connection:
            self._accept_statement_in_connection(
                connection, current, statement_id, analysis_id, reviewer_id, now
            )
            self._record_review_event(
                connection,
                reviewer_id=reviewer_id,
                statement_id=statement_id,
                analysis_id=analysis_id,
                resulting_analysis_id=analysis_id,
                decision="accepted",
                comments=None,
                score_context={"current_analysis": current["analysis"]},
                language_issue_types=[],
            )
            self._complete_if_exhausted(connection, reviewer_id)
        return self.get_statement(statement_id)

    def _accept_statement_in_connection(
        self,
        connection: DatabaseConnection,
        current: dict[str, Any],
        statement_id: str,
        analysis_id: str,
        reviewer_id: str,
        accepted_at: str,
    ) -> str:
        evidence_id = self._new_id("evidence")
        try:
            connection.execute(
                """
                INSERT INTO evidence
                    (id, statement_id, analysis_id, accepted_by, accepted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (evidence_id, statement_id, analysis_id, reviewer_id, accepted_at),
            )
        except DATABASE_INTEGRITY_ERRORS as exc:
            raise DomainError("This Statement has already been accepted.", status=409) from exc
        logic_model_id = current["analysis"].get("logic_model_id")
        if logic_model_id:
            assessment_id = self._new_id("assessment")
            mapping_ids = [
                item["id"] for item in current["mappings"] if item.get("logic_model_id")
            ]
            connection.execute(
                """
                INSERT INTO evidence_assessments
                    (id, evidence_id, logic_model_id, status, suggested_mapping_ids,
                     mapping_rationale, suggested_classification,
                     confirmed_classification, suggested_result_direction,
                     confirmed_result_direction, accepted_analysis_id,
                     source_logic_model_id, reviewer_id, created_at, decided_at)
                VALUES (?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    evidence_id,
                    logic_model_id,
                    json.dumps(mapping_ids),
                    current["analysis"]["mapping_rationale"],
                    current["analysis"]["classification"],
                    current["analysis"]["classification"],
                    current["analysis"]["result_direction"],
                    current["analysis"]["result_direction"],
                    analysis_id,
                    logic_model_id,
                    reviewer_id,
                    accepted_at,
                    accepted_at,
                ),
            )
            for rank, element_id in enumerate(mapping_ids, start=1):
                connection.execute(
                    "INSERT INTO evidence_assessment_mappings "
                    "(assessment_id, element_id, rank, confidence, rationale) "
                    "SELECT ?, ?, ?, confidence, rationale FROM analysis_mappings "
                    "WHERE analysis_id = ? AND element_id = ?",
                    (assessment_id, element_id, rank, analysis_id, element_id),
                )
        connection.execute(
            """
            UPDATE statements
            SET status = 'evidence', approved_classification = ?
            WHERE id = ? AND workspace_id = melvan_workspace_id()
            """,
            (current["analysis"]["classification"], statement_id),
        )
        self._audit(
            connection,
            "statement_accepted",
            "statement",
            statement_id,
            {
                "analysis_id": analysis_id,
                "evidence_id": evidence_id,
                "reviewer_id": reviewer_id,
                "accepted_after_change": current["analysis"].get("version", 1) > 1,
            },
        )
        return evidence_id

    def get_statement(self, statement_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM statements
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (statement_id,),
            ).fetchone()
            if not row:
                raise DomainError("Statement not found.", status=404)
            return self._statement_from_row(connection, row)

    def get_statement_for_reporter(
        self, statement_id: str, reporter_id: str
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM statements
                WHERE id = ? AND reporter_id = ? AND program_irrelevant_at IS NULL
                  AND workspace_id = melvan_workspace_id()
                """,
                (statement_id, reporter_id),
            ).fetchone()
            if not row:
                raise DomainError("Statement not found.", status=404)
            return self._statement_from_row(connection, row)

    def reporter_statements(self, reporter_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM statements
                WHERE reporter_id = ? AND program_irrelevant_at IS NULL
                  AND workspace_id = melvan_workspace_id()
                ORDER BY submitted_at DESC, id DESC
                """,
                (reporter_id,),
            ).fetchall()
            return [self._statement_from_row(connection, row) for row in rows]

    def review_statements(self, reviewer_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM statements
                WHERE status = 'awaiting_review' AND program_irrelevant_at IS NULL
                  AND workspace_id = melvan_workspace_id()
                ORDER BY submitted_at DESC, id DESC
                """
            ).fetchall()
            return [self._statement_from_row(connection, row) for row in rows]

    def review_workspace(self, reviewer_id: str, *, start: bool) -> dict[str, Any]:
        with self.database.connection() as connection:
            rows = self._eligible_review_rows(connection, reviewer_id)
            has_evidence = self._has_eligible_evidence_review(connection, reviewer_id)
            session = self._open_review_session_row(
                connection, reviewer_id, create=start and bool(rows or has_evidence)
            )
            if start and not rows and not has_evidence and session:
                report = self._complete_review_session(connection, session["id"], "exhausted")
                session = None
            else:
                report = None
            if not session and report is None:
                completed = connection.execute(
                    """
                    SELECT report_json FROM review_sessions
                    WHERE reviewer_id = ? AND status = 'completed'
                      AND workspace_id = melvan_workspace_id()
                    ORDER BY completed_at DESC, id DESC LIMIT 1
                    """,
                    (reviewer_id,),
                ).fetchone()
                report = json.loads(completed["report_json"]) if completed and completed["report_json"] else None
            return {
                "statements": [self._statement_from_row(connection, row) for row in rows],
                "session": dict(session) if session else None,
                "report": report,
            }

    def open_review_session(self, reviewer_id: str) -> dict[str, Any]:
        return self.review_workspace(reviewer_id, start=True)

    def end_review_session(self, reviewer_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            session = self._open_review_session_row(connection, reviewer_id, create=False)
            if not session:
                raise DomainError("There is no open review to end.", status=409)
            return self._complete_review_session(connection, session["id"], "ended")

    def mark_statement_irrelevant(
        self,
        statement_id: str,
        analysis_id: str,
        reviewer_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        current = self._review_context(statement_id, analysis_id, reviewer_id)
        normalized_reason = " ".join(str(reason or "").split()) or None
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE statements
                SET program_irrelevant_at = ?, program_irrelevant_by = ?,
                    program_irrelevant_analysis_id = ?, program_irrelevant_reason = ?
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (now, reviewer_id, analysis_id, normalized_reason, statement_id),
            )
            self._record_review_event(
                connection,
                reviewer_id=reviewer_id,
                statement_id=statement_id,
                analysis_id=analysis_id,
                resulting_analysis_id=None,
                decision="not_relevant",
                comments=normalized_reason,
                score_context=None,
            )
            self._audit(
                connection,
                "statement_marked_program_irrelevant",
                "statement",
                statement_id,
                {
                    "analysis_id": analysis_id,
                    "reviewer_id": reviewer_id,
                    "reason": normalized_reason,
                },
            )
            self._complete_if_exhausted(connection, reviewer_id)
        return {"id": statement_id, "status": "not_relevant"}

    @staticmethod
    def _eligible_review_rows(
        connection: DatabaseConnection, reviewer_id: str
    ) -> list[DatabaseRow]:
        return connection.execute(
            """
            SELECT * FROM statements
            WHERE status = 'awaiting_review'
              AND program_irrelevant_at IS NULL
              AND workspace_id = melvan_workspace_id()
            ORDER BY submitted_at DESC, id DESC
            """
        ).fetchall()

    @staticmethod
    def _has_eligible_evidence_review(
        connection: DatabaseConnection, reviewer_id: str
    ) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM evidence_assessments ea
            JOIN evidence e ON e.id = ea.evidence_id
            JOIN statements s ON s.id = e.statement_id
            JOIN logic_models lm ON lm.id = ea.logic_model_id
            WHERE ea.status = 'pending' AND lm.is_current = 1
              AND s.reporter_id <> ?
              AND ea.workspace_id = melvan_workspace_id()
            LIMIT 1
            """,
            (reviewer_id,),
        ).fetchone() is not None

    def _open_review_session_row(
        self,
        connection: DatabaseConnection,
        reviewer_id: str,
        *,
        create: bool,
    ) -> DatabaseRow | None:
        session = connection.execute(
            """
            SELECT * FROM review_sessions
            WHERE reviewer_id = ? AND status = 'open'
              AND workspace_id = melvan_workspace_id()
            LIMIT 1
            """,
            (reviewer_id,),
        ).fetchone()
        if session or not create:
            return session
        session_id = self._new_id("review-session")
        connection.execute(
            """
            INSERT INTO review_sessions
                (id, reviewer_id, status, started_at, scoring_policy_version)
            VALUES (?, ?, 'open', ?, ?)
            """,
            (session_id, reviewer_id, utc_now(), SCORING_POLICY_VERSION),
        )
        self._audit(
            connection,
            "review_session_started",
            "review_session",
            session_id,
            {"reviewer_id": reviewer_id},
        )
        return connection.execute(
            """
            SELECT * FROM review_sessions
            WHERE id = ? AND workspace_id = melvan_workspace_id()
            """,
            (session_id,),
        ).fetchone()

    def _record_review_event(
        self,
        connection: DatabaseConnection,
        *,
        reviewer_id: str,
        statement_id: str,
        analysis_id: str,
        resulting_analysis_id: str | None,
        decision: str,
        comments: str | None,
        score_context: dict[str, Any] | None,
        language_issue_types: list[str] | None = None,
    ) -> str:
        session = self._open_review_session_row(connection, reviewer_id, create=True)
        if not session:
            raise DomainError("Review session could not be started.", status=409)
        event_id = self._new_id("review-event")
        now = utc_now()
        statement = connection.execute(
            """
            SELECT reporter_id FROM statements
            WHERE id = ? AND workspace_id = melvan_workspace_id()
            """,
            (statement_id,),
        ).fetchone()
        if not statement:
            raise DomainError("Statement not found.", status=404)
        is_self_review = int(statement["reporter_id"] == reviewer_id)
        connection.execute(
            """
            INSERT INTO review_events
                (id, session_id, statement_id, analysis_id, resulting_analysis_id,
                 reviewer_id, decision, is_self_review, comments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session["id"],
                statement_id,
                analysis_id,
                resulting_analysis_id,
                reviewer_id,
                decision,
                is_self_review,
                comments,
                now,
            ),
        )
        if score_context is not None:
            score = self._statement_score(decision, score_context)
            connection.execute(
                """
                INSERT OR IGNORE INTO scoring_events
                    (id, cycle_key, cycle_type, statement_id, first_review_event_id,
                     first_review_approval, correct_level, correct_direction,
                     correct_mapping, overall_hygiene, source_signals_json,
                     explanations_json, scoring_policy_version,
                     interpretation_model_version, interpretation_prompt_version, created_at)
                VALUES (?, ?, 'statement', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._new_id("score"),
                    f"statement:{statement_id}",
                    statement_id,
                    event_id,
                    score["first_review_approval"],
                    score["correct_level"],
                    score["correct_direction"],
                    score["correct_mapping"],
                    score["overall_hygiene"],
                    json.dumps(score["signals"], ensure_ascii=True),
                    json.dumps(score["explanations"], ensure_ascii=True),
                    SCORING_POLICY_VERSION,
                    COMMENT_INTERPRETATION_VERSION if comments else None,
                    None,
                    now,
                ),
            )
            self.learning.record_statement_review(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                language_issue_types=language_issue_types or [],
                comments=comments,
            )
        return event_id

    @staticmethod
    def _statement_score(decision: str, context: dict[str, Any]) -> dict[str, Any]:
        analysis = context["current_analysis"]
        corrected_level = context.get("corrected_classification")
        corrected_direction = context.get("corrected_result_direction")
        comments = str(context.get("comments") or "").lower()
        language_issue_types = set(context.get("language_issue_types") or [])
        correction_language = bool(
            re.search(
                r"\b(wrong|incorrect|inaccurate|inaccurately|change|changed|revise|revised|"
                r"rewrite|rewritten|problem|issue|misleading|unsupported|incomplete|"
                r"hallucinat\w*|invent\w*|fabricat\w*)\b|"
                r"\bnot\s+(?:accurate|correct|supported|complete)\b|"
                r"\bdoes(?:n't| not)\b",
                comments,
            )
        )
        level_comment = bool(
            comments
            and (
                "wrong classification" in comments
                or "wrong level" in comments
                or "reclassif" in comments
                or "should be an outcome" in comments
                or "should be an output" in comments
                or "should be an activity" in comments
                or "should be an indicator" in comments
            )
        )
        direction_comment = bool(
            comments
            and (
                "wrong direction" in comments
                or "incorrect direction" in comments
                or "should be positive" in comments
                or "should be negative" in comments
                or "not positive" in comments
                or "not negative" in comments
            )
        )
        mapping_comment = bool(
            comments
            and (
                any(
                    term in comments
                    for term in (
                        "wrong mapping",
                        "incorrect mapping",
                        "map this to",
                        "should map",
                        "mapping should",
                        "remap",
                        "not mapped",
                        "wrong logic model element",
                        "wrong classification node",
                    )
                )
                or (
                    correction_language
                    and any(
                        term in comments
                        for term in ("mapping", "logic model element", "classification node")
                    )
                )
            )
        )
        hygiene_comment = bool(language_issue_types) or bool(
            comments
            and correction_language
            and (
                any(
                    term in comments
                    for term in ("summary", "rationale", "criteria", "criterion", "quality")
                )
                or any(
                    term in comments
                    for term in ("hallucination", "hallucinated", "invented", "fabricated")
                )
            )
        )
        final_classification = corrected_level or analysis["classification"]
        level_ok = not corrected_level and not level_comment
        direction_applicable = final_classification == "Outcome"
        direction_ok = None
        if direction_applicable:
            direction_ok = int(
                analysis["classification"] == "Outcome"
                and not corrected_direction
                and not direction_comment
            )
        mapping_ok = not any(
            (
                context.get("mapping_supplied"),
                context.get("new_element"),
                context.get("reject_proposal"),
                mapping_comment,
            )
        )
        current_mappings = [
            mapping
            for mapping in context.get("current_mappings") or []
            if mapping.get("status") != "provisional"
        ]
        selected_mapping_ids = context.get("selected_mapping_ids")
        tentative_rejections: list[str] = []
        only_tentative_rejections = False
        if context.get("mapping_supplied") and selected_mapping_ids is not None:
            current_ids = {mapping["id"] for mapping in current_mappings}
            selected_ids = set(selected_mapping_ids)
            tentative_ids = {
                mapping["id"]
                for mapping in current_mappings
                if MelvanService._mapping_is_tentative(mapping.get("confidence"))
            }
            removed_ids = current_ids - selected_ids
            added_ids = selected_ids - current_ids
            tentative_rejections = sorted(removed_ids & tentative_ids)
            only_tentative_rejections = bool(tentative_rejections) and (
                not added_ids and removed_ids <= tentative_ids
            )
        if only_tentative_rejections and not any(
            (context.get("new_element"), context.get("reject_proposal"))
        ):
            mapping_ok = True
        first_review_ok = decision == "accepted" or (
            decision == "changed"
            and only_tentative_rejections
            and level_ok
            and (direction_ok in {None, 1})
            and not hygiene_comment
        )
        return {
            "first_review_approval": int(first_review_ok),
            "correct_level": int(level_ok),
            "correct_direction": direction_ok,
            "correct_mapping": int(mapping_ok),
            "overall_hygiene": int(not hygiene_comment),
            "signals": {
                "decision": decision,
                "corrected_classification": corrected_level,
                "corrected_result_direction": corrected_direction,
                "mapping_supplied": bool(context.get("mapping_supplied")),
                "tentative_mapping_ids": sorted(
                    mapping["id"]
                    for mapping in current_mappings
                    if MelvanService._mapping_is_tentative(mapping.get("confidence"))
                ),
                "tentative_mapping_rejections": tentative_rejections,
                "only_tentative_mapping_rejections": only_tentative_rejections,
                "new_element": bool(context.get("new_element")),
                "reject_proposal": bool(context.get("reject_proposal")),
                "comment_dimensions": {
                    "level": level_comment,
                    "direction": direction_comment,
                    "mapping": mapping_comment,
                    "hygiene": hygiene_comment,
                },
                "applicability": {
                    "first_review_approval": True,
                    "correct_level": True,
                    "correct_direction": direction_applicable,
                    "correct_mapping": True,
                    "overall_hygiene": True,
                },
            },
            "explanations": {
                "first_review_approval": "The first Reviewer action accepted MELvan's presentation."
                if first_review_ok and not only_tentative_rejections
                else (
                    "The Reviewer removed only mappings MELvan labelled Low confidence; "
                    "this does not reduce first-review approval."
                )
                if first_review_ok
                else "The first Reviewer action requested a change.",
                "correct_level": "No level correction was identified."
                if level_ok
                else "The Reviewer disputed the Activity, Output, Outcome, or Indicator level.",
                "correct_direction": "No Outcome direction correction was identified."
                if direction_applicable and direction_ok
                else "The Outcome direction was corrected or was unavailable because the level changed."
                if direction_applicable
                else "Direction is not applicable to this classification.",
                "correct_mapping": "The mapping or no-match decision was not disputed."
                if mapping_ok and not only_tentative_rejections
                else (
                    "The Reviewer removed only mappings MELvan labelled Low confidence; "
                    "this does not reduce mapping correctness."
                )
                if mapping_ok
                else "The Reviewer disputed or changed the mapping decision.",
                "overall_hygiene": "No hygiene defect was identified."
                if not hygiene_comment
                else "Reviewer feedback identified a summary, rationale, criteria, hallucination, or quality defect.",
            },
        }

    def _complete_if_exhausted(
        self, connection: DatabaseConnection, reviewer_id: str
    ) -> dict[str, Any] | None:
        if self._eligible_review_rows(connection, reviewer_id) or self._has_eligible_evidence_review(
            connection, reviewer_id
        ):
            return None
        session = self._open_review_session_row(connection, reviewer_id, create=False)
        if not session:
            return None
        return self._complete_review_session(connection, session["id"], "exhausted")

    def _complete_review_session(
        self,
        connection: DatabaseConnection,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        session = connection.execute(
            """
            SELECT * FROM review_sessions
            WHERE id = ? AND status = 'open'
              AND workspace_id = melvan_workspace_id()
            """,
            (session_id,),
        ).fetchone()
        if not session:
            raise DomainError("This review session is already complete.", status=409)
        events = connection.execute(
            """
            SELECT * FROM review_events
            WHERE session_id = ? AND workspace_id = melvan_workspace_id()
            ORDER BY created_at, id
            """,
            (session_id,),
        ).fetchall()
        unique_statements = {row["statement_id"] for row in events}
        counts = {
            "reviewed": len(unique_statements),
            "accepted": len({row["statement_id"] for row in events if row["decision"] == "accepted"}),
            "sent_back": len({row["statement_id"] for row in events if row["decision"] == "changed"}),
            "not_relevant": len({row["statement_id"] for row in events if row["decision"] == "not_relevant"}),
        }
        session_scores = connection.execute(
            """
            SELECT se.* FROM scoring_events se
            JOIN review_events re ON re.id = se.first_review_event_id
            WHERE re.session_id = ?
              AND se.workspace_id = melvan_workspace_id()
              AND re.workspace_id = melvan_workspace_id()
            """,
            (session_id,),
        ).fetchall()
        lifetime_scores = connection.execute(
            """
            SELECT * FROM scoring_events
            WHERE workspace_id = melvan_workspace_id()
            """
        ).fetchall()
        completed_at = utc_now()
        learning_policy = self.learning.active_policy(connection)
        report = {
            "session_id": session_id,
            "reviewer_id": session["reviewer_id"],
            "started_at": session["started_at"],
            "completed_at": completed_at,
            "completion_reason": reason,
            "review_event_ids": [row["id"] for row in events],
            "scoring_policy_version": SCORING_POLICY_VERSION,
            "learning_policy_version": learning_policy["version"],
            "learning_memory_enabled": learning_policy["enabled"],
            "counts": counts,
            "session_scores": self._score_summary(session_scores),
            "lifetime_scores": self._score_summary(lifetime_scores),
        }
        diagnostics = self.learning.session_diagnostics(connection, session_id)
        report["diagnostics"] = diagnostics
        connection.execute(
            """
            INSERT INTO review_session_score_snapshots
                (session_id, completed_at, scoring_policy_version,
                 learning_policy_version, memory_enabled, scores_json,
                 diagnostics_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, completed_at, SCORING_POLICY_VERSION,
                learning_policy["version"],
                int(learning_policy["enabled"]),
                json.dumps(report["session_scores"], ensure_ascii=True),
                json.dumps(diagnostics, ensure_ascii=True),
            ),
        )
        report["session_trend"] = self._review_session_trend(connection)
        connection.execute(
            """
            UPDATE review_sessions
            SET status = 'completed', completed_at = ?, completion_reason = ?, report_json = ?
            WHERE id = ?
            """,
            (completed_at, reason, json.dumps(report, ensure_ascii=True), session_id),
        )
        self._audit(
            connection,
            "review_session_completed",
            "review_session",
            session_id,
            {"reason": reason, "counts": counts},
        )
        return report

    @staticmethod
    def _review_session_trend(connection: DatabaseConnection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT * FROM review_session_score_snapshots
            WHERE workspace_id = melvan_workspace_id()
            ORDER BY completed_at, session_id
            """
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "completed_at": row["completed_at"],
                "scoring_policy_version": row["scoring_policy_version"],
                "learning_policy_version": row["learning_policy_version"],
                "memory_enabled": bool(row["memory_enabled"]),
                "scores": json.loads(row["scores_json"]),
                "diagnostics": json.loads(row["diagnostics_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _score_summary(rows: list[DatabaseRow]) -> dict[str, dict[str, Any]]:
        dimensions = (
            "first_review_approval",
            "correct_level",
            "correct_direction",
            "correct_mapping",
            "overall_hygiene",
        )
        summary: dict[str, dict[str, Any]] = {}
        for dimension in dimensions:
            values = [row[dimension] for row in rows if row[dimension] is not None]
            matches = sum(int(value) for value in values)
            total = len(values)
            summary[dimension] = {
                "matches": matches,
                "total": total,
                "percentage": round(matches / total * 100, 1) if total else None,
            }
        return summary

    def evidence(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM statements s
                JOIN evidence e ON e.statement_id = s.id
                WHERE s.program_irrelevant_at IS NULL
                  AND s.workspace_id = melvan_workspace_id()
                  AND NOT EXISTS (
                    SELECT 1 FROM evidence_assessments ea
                    JOIN logic_models lm ON lm.id = ea.logic_model_id
                    WHERE ea.evidence_id = e.id AND lm.is_current = 1
                      AND ea.status = 'not_relevant'
                  )
                ORDER BY e.accepted_at DESC
                """
            ).fetchall()
            values = [self._statement_from_row(connection, row) for row in rows]
            return values

    def _apply_current_evidence_assessment(
        self, connection: DatabaseConnection, statement: dict[str, Any]
    ) -> None:
        evidence = statement.get("evidence")
        if not evidence:
            return
        assessment = connection.execute(
            """
            SELECT ea.*, lm.version AS logic_model_version
            FROM evidence_assessments ea
            JOIN logic_models lm ON lm.id = ea.logic_model_id
            WHERE ea.evidence_id = ? AND lm.is_current = 1 AND ea.status = 'approved'
              AND ea.workspace_id = melvan_workspace_id()
            """,
            (evidence["id"],),
        ).fetchone()
        if not assessment:
            return
        mapping_rows = connection.execute(
            """
            SELECT el.*, eam.rank, eam.confidence, eam.rationale
            FROM evidence_assessment_mappings eam
            JOIN elements el ON el.id = eam.element_id
            WHERE eam.assessment_id = ? ORDER BY eam.rank
            """,
            (assessment["id"],),
        ).fetchall()
        statement["accepted_analysis"] = dict(statement["analysis"])
        statement["analysis"] = dict(statement["analysis"])
        confirmed_classification = (
            assessment["confirmed_classification"]
            or assessment["suggested_classification"]
            or statement["analysis"]["classification"]
        )
        statement["analysis"].update(
            {
                "classification": confirmed_classification,
                "result_direction": (
                    assessment["confirmed_result_direction"]
                    or assessment["suggested_result_direction"]
                    or statement["analysis"]["result_direction"]
                )
                if confirmed_classification == "Outcome"
                else None,
                "logic_model_id": assessment["logic_model_id"],
                "logic_model_version": assessment["logic_model_version"],
                "mapping_rationale": assessment["mapping_rationale"],
                "match_status": "existing_match" if mapping_rows else "no_suitable_match",
            }
        )
        statement["mappings"] = [dict(row) for row in mapping_rows]
        statement["evidence_assessment"] = {
            "id": assessment["id"],
            "status": assessment["status"],
            "logic_model_id": assessment["logic_model_id"],
            "logic_model_version": assessment["logic_model_version"],
            "decided_at": assessment["decided_at"],
            "reviewer_id": assessment["reviewer_id"],
        }

    def bootstrap(self, reviewer_id: str | None = None) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            review_count = connection.execute(
                """
                SELECT COUNT(*) FROM statements
                WHERE status = 'awaiting_review' AND program_irrelevant_at IS NULL
                  AND workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()[0]
            evidence_count = len(self.evidence())
            elements = self._selectable_elements(connection)
        review_state = self.review_workspace(reviewer_id, start=False) if reviewer_id else None
        try:
            logic_model = self.logic_model()
        except DomainError:
            logic_model = self.empty_logic_model_state()
        return {
            "app_version": __version__,
            "review": review_state["statements"] if review_state else self.review_statements(),
            "evidence": self.evidence(),
            "evidence_review": self.evidence_review_items(reviewer_id),
            "elements": elements,
            "logic_model": logic_model,
            "logic_model_versions": self.logic_model_versions(),
            "logic_model_draft": self.logic_model_draft(),
            "counts": {
                "review": review_count,
                "evidence": evidence_count,
                "evidence_review": len(self.evidence_review_items(reviewer_id)),
            },
            "quality": self.classification_agreement(),
            "review_session": review_state["session"] if review_state else None,
            "review_report": review_state["report"] if review_state else None,
            "model": self.model.health_check(),
        }

    @staticmethod
    def empty_logic_model_state() -> dict[str, Any]:
        return {
            "status": "empty",
            "database_id": None,
            "id": None,
            "title": "No Logic Model",
            "version": None,
            "sequence": None,
            "is_current": False,
            "recap": {
                key: {"stable_id": f"recap-{key}", "revision": 1, "text": ""}
                for key in RECAP_KEYS
            },
            "elements": [],
        }

    def preload_demo_logic_model(self, actor_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise DomainError("Confirm that you want to pre-load the demo Logic Model.")
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM logic_models WHERE workspace_id = ? LIMIT 1",
                (workspace_id,),
            ).fetchone():
                raise DomainError(
                    "The demo Logic Model can only be pre-loaded before this workspace "
                    "has committed Logic Model history.",
                    status=409,
                )
            source = connection.execute(
                """
                SELECT * FROM logic_models
                WHERE workspace_id = 'workspace-owner' AND is_current = 1
                LIMIT 1
                """
            ).fetchone()
            if not source:
                raise DomainError("The Owner demonstration Logic Model is unavailable.", status=503)
            source_fixture = self._normalize_logic_model_snapshot(
                json.loads(source["fixture_json"])
            )
            now = utc_now()
            logic_model_id = self._new_id("logic-model")
            fixture = {
                **source_fixture,
                "id": logic_model_id,
                "version": "1.0",
                "source": (
                    f"Owner demonstration Logic Model version {source['version']}"
                ),
                "elements": [],
            }
            for element in source_fixture["elements"]:
                fixture["elements"].append(
                    {
                        **element,
                        "internal_id": self._new_id("element"),
                        "stable_id": self._new_id("stable-element"),
                        "revision": 1,
                    }
                )
            connection.execute(
                """
                INSERT INTO logic_models
                    (id, title, version, sequence, is_current, source, fixture_json,
                     committed_by, committed_at, change_summary_json, impact_json)
                VALUES (?, ?, '1.0', 1, 1, ?, ?, ?, ?, '{}', '{}')
                """,
                (
                    logic_model_id,
                    fixture["title"],
                    fixture["source"],
                    json.dumps(fixture, ensure_ascii=True),
                    actor_id,
                    now,
                ),
            )
            for element in fixture["elements"]:
                connection.execute(
                    """
                    INSERT INTO elements
                        (id, display_id, logic_model_id, label, element_type, level,
                         group_name, source, status, created_at, stable_id, revision,
                         display_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'fixture', 'published', ?, ?, 1, ?)
                    """,
                    (
                        element["internal_id"],
                        element["id"],
                        logic_model_id,
                        element["label"],
                        element["type"],
                        element["level"],
                        element.get("group"),
                        now,
                        element["stable_id"],
                        element["number"],
                    ),
                )
            self._audit(
                connection,
                "demo_logic_model_preloaded",
                "logic_model",
                logic_model_id,
                {
                    "actor_id": actor_id,
                    "source_logic_model_id": source["id"],
                    "source_version": source["version"],
                },
            )
        return self.logic_model(logic_model_id)

    def logic_model(self, logic_model_id: str | None = None) -> dict[str, Any]:
        """Return one authoritative version, excluding working-catalogue elements."""
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            if logic_model_id:
                row = connection.execute(
                    "SELECT * FROM logic_models WHERE id = ? AND workspace_id = ?",
                    (logic_model_id, workspace_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM logic_models
                    WHERE is_current = 1 AND workspace_id = ? LIMIT 1
                    """,
                    (workspace_id,),
                ).fetchone()
            activation = connection.execute(
                """
                SELECT * FROM logic_model_activations
                WHERE logic_model_id = ? AND workspace_id = ?
                ORDER BY activated_at DESC, id DESC LIMIT 1
                """,
                (row["id"], workspace_id),
            ).fetchone() if row else None
        if not row:
            raise DomainError("Logic Model is not available.", status=503)
        value = self._normalize_logic_model_snapshot(json.loads(row["fixture_json"]))
        value.update(
            {
                "database_id": row["id"],
                "sequence": row["sequence"],
                "is_current": bool(row["is_current"]),
                "source_filename": row["source_filename"] or Path(row["source"]).name,
                "media_type": row["media_type"],
                "uploaded_by": row["uploaded_by"],
                "uploaded_at": row["uploaded_at"],
                "committed_by": row["committed_by"] or row["uploaded_by"],
                "committed_at": row["committed_at"] or row["uploaded_at"],
                "change_summary": json.loads(row["change_summary_json"] or "{}"),
                "impact": json.loads(row["impact_json"] or "{}"),
                "extracted_text": row["extracted_text"],
                "activation": (
                    {
                        "id": activation["id"],
                        "status": activation["status"],
                        "reason": activation["reason"],
                        "activated_at": activation["activated_at"],
                        "completed_at": activation["completed_at"],
                        "error_message": activation["error_message"],
                        "manifest": json.loads(activation["impact_manifest_json"] or "{}"),
                    }
                    if activation else None
                ),
            }
        )
        return value

    def logic_model_versions(self) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, title, version, sequence, is_current, source_filename,
                       media_type, content_hash, uploaded_by, uploaded_at
                FROM logic_models WHERE workspace_id = ? ORDER BY sequence DESC
                """,
                (workspace_id,),
            ).fetchall()
        versions = [dict(row) | {"is_current": bool(row["is_current"]), "is_draft": False} for row in rows]
        draft = self.logic_model_draft()
        if draft:
            versions.insert(
                0,
                {
                    "id": "draft",
                    "title": draft["title"] or "Untitled Logic Model",
                    "version": "In progress",
                    "sequence": None,
                    "is_current": False,
                    "is_draft": True,
                    "updated_at": draft["updated_at"],
                },
            )
        return versions

    def logic_model_draft(self) -> dict[str, Any] | None:
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM logic_model_drafts WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if not row:
            return None
        value = self._normalize_logic_model_snapshot(json.loads(row["draft_json"]))
        value.update(
            {
                "id": "draft",
                "status": "in_progress",
                "base_logic_model_id": row["base_logic_model_id"],
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
        )
        return value

    def start_logic_model_draft(self, actor_id: str) -> dict[str, Any]:
        existing = self.logic_model_draft()
        if existing:
            return existing
        try:
            current = self.logic_model()
            draft = {
                "title": current["title"],
                "title_revision": current.get("title_revision", 1),
                "recap": current["recap"],
                "elements": current["elements"],
            }
            base_logic_model_id = current["database_id"]
        except DomainError:
            current = None
            draft = {
                "title": "",
                "title_revision": 1,
                "recap": {
                    key: {"stable_id": f"recap-{key}", "revision": 1, "text": ""}
                    for key in RECAP_KEYS
                },
                "elements": [],
            }
            base_logic_model_id = None
        now = utc_now()
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO logic_model_drafts
                    (workspace_id, base_logic_model_id, draft_json, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    base_logic_model_id,
                    json.dumps(draft, ensure_ascii=True),
                    actor_id,
                    now,
                ),
            )
            self._audit(
                connection,
                "logic_model_draft_started",
                "logic_model_draft",
                "draft",
                {"actor_id": actor_id, "base_logic_model_id": base_logic_model_id},
            )
        return self.logic_model_draft() or draft

    def save_logic_model_draft(self, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        current = self.logic_model_draft() or self.start_logic_model_draft(actor_id)
        sanitized = self._sanitize_logic_model_draft(payload, current)
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE logic_model_drafts
                SET draft_json = ?, updated_by = ?, updated_at = ?
                WHERE workspace_id = ?
                """,
                (
                    json.dumps(sanitized, ensure_ascii=True),
                    actor_id,
                    now,
                    self._workspace_id(),
                ),
            )
        return self.logic_model_draft() or sanitized

    def cancel_logic_model_draft(self, actor_id: str, *, confirmed: bool) -> None:
        if not confirmed:
            raise DomainError("Confirm that you want to cancel all changes.")
        with self.database.connection() as connection:
            deleted = connection.execute(
                "DELETE FROM logic_model_drafts WHERE workspace_id = ?",
                (self._workspace_id(),),
            ).rowcount
            if deleted:
                self._audit(
                    connection,
                    "logic_model_draft_cancelled",
                    "logic_model_draft",
                    "draft",
                    {"actor_id": actor_id},
                )

    def logic_model_commit_summary(self) -> dict[str, Any]:
        draft = self.logic_model_draft()
        if not draft:
            raise DomainError("There is no in-progress Logic Model.", status=404)
        base = (
            self.logic_model(draft["base_logic_model_id"])
            if draft["base_logic_model_id"] is not None
            else self._empty_logic_model_snapshot()
        )
        self._validate_commit_ready(draft)
        return self._logic_model_change_summary(base, draft)

    def commit_logic_model_draft(self, actor_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise DomainError("Confirm that you want to commit these Logic Model updates.")
        draft = self.logic_model_draft()
        if not draft:
            raise DomainError("There is no in-progress Logic Model.", status=404)
        base = (
            self.logic_model(draft["base_logic_model_id"])
            if draft["base_logic_model_id"] is not None
            else self._empty_logic_model_snapshot()
        )
        self._validate_commit_ready(draft)
        summary = self._logic_model_change_summary(base, draft)
        if not summary["has_changes"]:
            raise DomainError("There are no Logic Model changes to commit.")

        base_elements = {item["stable_id"]: item for item in base["elements"]}
        committed_elements: list[dict[str, Any]] = []
        now = utc_now()
        logic_model_id = self._new_id("logic-model")
        for item in draft["elements"]:
            previous = base_elements.get(item["stable_id"])
            changed = not previous or self._element_revision_value(previous) != self._element_revision_value(item)
            revision = 1 if not previous else int(previous.get("revision", 1)) + (1 if changed else 0)
            committed_elements.append(dict(item) | {"revision": revision})

        committed_recap: dict[str, dict[str, Any]] = {}
        for key in RECAP_KEYS:
            previous = base["recap"][key]
            item = draft["recap"][key]
            changed = previous.get("text", "") != item.get("text", "")
            revision = int(previous.get("revision", 1)) + (1 if changed else 0)
            committed_recap[key] = dict(item) | {"revision": revision}

        sequence: int
        with self.database.connection() as connection:
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM logic_models WHERE workspace_id = ?
                    """,
                    (self._workspace_id(),),
                ).fetchone()[0]
            )
        version = f"{sequence}.0"
        title_changed = base["title"] != draft["title"]
        fixture = {
            "id": logic_model_id,
            "title": draft["title"].strip(),
            "title_revision": int(base.get("title_revision", 1)) + (1 if title_changed else 0),
            "version": version,
            "source": "MELvan builder",
            "recap": committed_recap,
            "elements": [],
            "relationships": [],
            "annotations": [],
            "tombstones": summary["tombstones"],
        }

        with self.database.connection() as connection:
            for item in committed_elements:
                internal_id = self._new_id("element")
                fixture["elements"].append(
                    {
                        "id": item["display_id"],
                        "internal_id": internal_id,
                        "stable_id": item["stable_id"],
                        "revision": item["revision"],
                        "number": item["number"],
                        "type": item["type"],
                        "level": item["level"],
                        "group": item.get("group"),
                        "label": item["label"].strip(),
                    }
                )
            connection.execute(
                """
                INSERT INTO logic_models
                    (id, title, version, sequence, is_current, source, fixture_json,
                     committed_by, committed_at, change_summary_json, impact_json)
                VALUES (?, ?, ?, ?, 0, 'MELvan builder', ?, ?, ?, ?, ?)
                """,
                (
                    logic_model_id,
                    fixture["title"],
                    version,
                    sequence,
                    json.dumps(fixture, ensure_ascii=True),
                    actor_id,
                    now,
                    json.dumps(summary, ensure_ascii=True),
                    json.dumps(summary["impact"], ensure_ascii=True),
                ),
            )
            for item in fixture["elements"]:
                connection.execute(
                    """
                    INSERT INTO elements
                        (id, display_id, logic_model_id, label, element_type, level,
                         group_name, source, status, created_at, stable_id, revision,
                         display_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'fixture', 'published', ?, ?, ?, ?)
                    """,
                    (
                        item["internal_id"], item["id"], logic_model_id, item["label"],
                        item["type"], item["level"], item.get("group"), now,
                        item["stable_id"], item["revision"], item["number"],
                    ),
                )
            self._audit(
                connection,
                "logic_model_committed",
                "logic_model",
                logic_model_id,
                {"actor_id": actor_id, "version": version, "summary": summary},
            )

        result = self.activate_logic_model(
            logic_model_id,
            actor_id,
            reason="commit",
            impact=summary["impact"],
        )
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM logic_model_drafts WHERE workspace_id = ?",
                (self._workspace_id(),),
            )
        return result

    @staticmethod
    def _empty_logic_model_snapshot() -> dict[str, Any]:
        return {
            "database_id": "__new__",
            "id": "__new__",
            "title": "",
            "title_revision": 1,
            "version": "0.0",
            "recap": {
                key: {"stable_id": f"recap-{key}", "revision": 1, "text": ""}
                for key in RECAP_KEYS
            },
            "elements": [],
        }

    def _normalize_logic_model_snapshot(self, value: dict[str, Any]) -> dict[str, Any]:
        value = dict(value)
        recap = value.get("recap") or {}
        normalized_recap: dict[str, dict[str, Any]] = {}
        for key in RECAP_KEYS:
            raw = recap.get(key, {})
            if isinstance(raw, list):
                entries = [entry if isinstance(entry, dict) else {"text": entry} for entry in raw]
                raw = {
                    "revision": max((int(entry.get("revision", 1)) for entry in entries), default=1),
                    "text": "\n".join(str(entry.get("text") or "") for entry in entries),
                }
            elif isinstance(raw, str):
                raw = {"text": raw}
            normalized_recap[key] = {
                "stable_id": f"recap-{key}",
                "revision": int(raw.get("revision", 1)),
                "text": str(raw.get("text") or ""),
            }
        value["recap"] = normalized_recap
        value["title_revision"] = int(value.get("title_revision", 1))
        counters = {"Output": 0, "Outcome": 0}
        normalized_elements = []
        for element in value.get("elements", []):
            element_type = element.get("type") or element.get("element_type")
            legacy_level = element.get("level")
            level = "output" if element_type == "Output" else element.get("level")
            counters[element_type] = counters.get(element_type, 0) + 1
            number = int(element.get("number") or counters[element_type])
            counters[element_type] = max(counters[element_type], number)
            display_id = element.get("display_id") or element.get("id") or self._display_id(
                element_type, level, number
            )
            normalized_elements.append(
                {
                    "id": display_id,
                    "display_id": display_id,
                    "internal_id": element.get("internal_id"),
                    "stable_id": element.get("stable_id") or element.get("internal_id") or display_id,
                    "revision": int(element.get("revision", 1)),
                    "number": number,
                    "type": element_type,
                    "level": level,
                    "group": (
                        "Enabling outputs" if legacy_level == "enabling_output"
                        else element.get("group")
                    ) if element_type == "Output" else None,
                    "label": str(element.get("label") or ""),
                }
            )
        value["elements"] = normalized_elements
        value.setdefault("tombstones", [])
        return value

    def _sanitize_logic_model_draft(
        self, payload: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        source = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
        confirmed_deletions = set(
            source.get("confirmed_deletions") or payload.get("confirmed_deletions") or []
        )
        previous_elements = {item["stable_id"]: item for item in current["elements"]}
        elements: list[dict[str, Any]] = []
        seen: set[str] = set()
        max_numbers = {
            "Output": max((item["number"] for item in current["elements"] if item["type"] == "Output"), default=0),
            "Outcome": max((item["number"] for item in current["elements"] if item["type"] == "Outcome"), default=0),
        }
        for raw in source.get("elements", []):
            element_type = str(raw.get("type") or "")
            level = str(raw.get("level") or "")
            if element_type not in {"Output", "Outcome"}:
                raise DomainError("Choose Output or Outcome for each Logic Model element.")
            if element_type == "Output":
                level = "output"
            elif level not in OUTCOME_LEVELS:
                raise DomainError("Choose a valid Outcome level.")
            stable_id = str(raw.get("stable_id") or self._new_id("logic-object"))
            if stable_id in seen:
                raise DomainError("A Logic Model object appears more than once.")
            seen.add(stable_id)
            previous = previous_elements.get(stable_id)
            if previous:
                number = previous["number"]
                display_id = previous["display_id"]
                revision = previous["revision"]
            else:
                requested = int(raw.get("number") or 0)
                if requested > max_numbers[element_type]:
                    number = requested
                else:
                    number = max_numbers[element_type] + 1
                max_numbers[element_type] = max(max_numbers[element_type], number)
                display_id = self._display_id(element_type, level, number)
                revision = 0
            elements.append(
                {
                    "id": display_id,
                    "display_id": display_id,
                    "stable_id": stable_id,
                    "revision": revision,
                    "number": number,
                    "type": element_type,
                    "level": level,
                    "group": (
                        " ".join(str(raw.get("group") or "").split()) or None
                    ) if element_type == "Output" else None,
                    "label": str(raw.get("label") or ""),
                }
            )
        removed = set(previous_elements) - seen
        ultimate_ids = {item["stable_id"] for item in current["elements"] if item["level"] == "ultimate_goal"}
        if removed & ultimate_ids:
            raise DomainError("The Ultimate Goal cannot be deleted.")
        if not removed.issubset(confirmed_deletions):
            raise DomainError("Confirm every Logic Model element deletion.")

        counts = {level: 0 for level in OUTCOME_LEVELS}
        for item in elements:
            if item["type"] == "Outcome":
                counts[item["level"]] += 1
        for level, maximum in OUTCOME_LEVELS.items():
            if counts[level] > maximum:
                raise DomainError(f"The Logic Model exceeds the {maximum} {level.replace('_', ' ')} limit.")

        current_recap = current.get("recap") or {}
        recap_source = source.get("recap") or {}
        recap: dict[str, dict[str, Any]] = {}
        for key in RECAP_KEYS:
            previous = current_recap.get(key) or {
                "stable_id": f"recap-{key}", "revision": 1, "text": ""
            }
            raw = recap_source.get(key, previous)
            if isinstance(raw, list):
                raw = {"text": "\n".join(
                    str(item.get("text") or "") if isinstance(item, dict) else str(item)
                    for item in raw
                )}
            elif isinstance(raw, str):
                raw = {"text": raw}
            recap[key] = {
                "stable_id": f"recap-{key}",
                "revision": int(previous.get("revision", 1)),
                "text": str(raw.get("text") or ""),
            }

        return {
            "title": str(source.get("title") or ""),
            "title_revision": int(current.get("title_revision", 1)),
            "recap": recap,
            "elements": elements,
        }

    def _validate_commit_ready(self, draft: dict[str, Any]) -> None:
        errors = []
        if not draft["title"].strip():
            errors.append("Enter a Logic Model title")
        outputs = [item for item in draft["elements"] if item["type"] == "Output"]
        outcomes = [
            item for item in draft["elements"]
            if item["type"] == "Outcome" and item["level"] != "ultimate_goal"
        ]
        goals = [item for item in draft["elements"] if item["level"] == "ultimate_goal"]
        if not outputs:
            errors.append("add at least one Output")
        if not outcomes:
            errors.append("add at least one Short-, Medium-, or Long-term Outcome")
        if len(goals) != 1:
            errors.append("include exactly one Ultimate Goal")
        if any(not item["label"].strip() for item in draft["elements"]):
            errors.append("complete every Output, Outcome, and Ultimate Goal")
        if errors:
            raise DomainError("Before committing, " + "; ".join(errors) + ".")

    def _logic_model_change_summary(
        self, base: dict[str, Any], draft: dict[str, Any]
    ) -> dict[str, Any]:
        base_elements = {item["stable_id"]: item for item in base["elements"]}
        draft_elements = {item["stable_id"]: item for item in draft["elements"]}
        added = [item for key, item in draft_elements.items() if key not in base_elements]
        deleted = [item for key, item in base_elements.items() if key not in draft_elements]
        edited = []
        group_only = []
        stylistic_only = []
        for key in set(base_elements) & set(draft_elements):
            before, after = base_elements[key], draft_elements[key]
            if self._element_revision_value(before) == self._element_revision_value(after):
                continue
            if (
                before["label"] == after["label"]
                and before["level"] == after["level"]
                and before["type"] == after["type"]
                and before.get("group") != after.get("group")
            ):
                group_only.append(after)
            elif (
                before["type"] == after["type"]
                and before["level"] == after["level"]
                and before.get("group") == after.get("group")
                and self._normalized_label(before["label"])
                == self._normalized_label(after["label"])
            ):
                stylistic_only.append(after)
            else:
                edited.append(after)

        recap_changes = 0
        for key in RECAP_KEYS:
            if base["recap"][key].get("text") != draft["recap"][key].get("text"):
                recap_changes += 1
        tombstones = [
            {
                "stable_id": item["stable_id"],
                "revision": int(item.get("revision", 1)) + 1,
                "kind": item["type"].lower(),
                "number": item["number"],
            }
            for item in deleted
        ]
        material_ids = [item["stable_id"] for item in [*added, *edited, *deleted]]
        impact = {
            "requires_assessment": bool(material_ids),
            "recap_only": bool(recap_changes) and not material_ids and not group_only,
            "metadata_only": not material_ids,
            "changed_stable_ids": material_ids,
            "added_stable_ids": [item["stable_id"] for item in added],
            "edited_stable_ids": [item["stable_id"] for item in edited],
            "deleted_stable_ids": [item["stable_id"] for item in deleted],
            "has_new_elements": bool(added),
            "rationale": (
                "Logic Model name, RECAP, and Output Category changes do not affect "
                "classification or mapping."
                if not material_ids
                else "Element additions, wording, level changes, or deletions may affect classification or mapping."
            ),
        }
        return {
            "has_changes": bool(
                base["title"] != draft["title"]
                or recap_changes
                or added
                or edited
                or deleted
                or group_only
                or stylistic_only
            ),
            "title_changed": base["title"] != draft["title"],
            "recap_changes": recap_changes,
            "outputs_added": sum(1 for item in added if item["type"] == "Output"),
            "outcomes_added": sum(1 for item in added if item["type"] == "Outcome"),
            "elements_edited": len(edited),
            "group_labels_changed": len(group_only),
            "stylistic_changes": len(stylistic_only),
            "elements_deleted": len(deleted),
            "tombstones": tombstones,
            "impact": impact,
        }

    @staticmethod
    def _normalized_label(label: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", label.casefold()))

    @staticmethod
    def _element_revision_value(item: dict[str, Any]) -> tuple[Any, ...]:
        return (item["type"], item["level"], item.get("group"), item["label"])

    @staticmethod
    def _display_id(element_type: str, level: str | None, number: int) -> str:
        if element_type == "Output":
            return f"O{number}"
        prefixes = {
            "short_term_outcome": "SO",
            "medium_term_outcome": "MO",
            "long_term_outcome": "LO",
            "ultimate_goal": "UG",
        }
        return f"{prefixes.get(level or '', 'SO')}{number}"

    def activate_logic_model(
        self,
        logic_model_id: str,
        actor_id: str,
        *,
        reason: str = "restore",
        impact: dict[str, Any] | None = None,
        discard_draft: bool = False,
        activation_id: str | None = None,
    ) -> dict[str, Any]:
        if reason not in {"commit", "restore"}:
            raise DomainError("Choose a valid Logic Model activation reason.")
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            target = connection.execute(
                "SELECT * FROM logic_models WHERE id = ? AND workspace_id = ?",
                (logic_model_id, workspace_id),
            ).fetchone()
            current = connection.execute(
                """
                SELECT * FROM logic_models
                WHERE is_current = 1 AND workspace_id = ? LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
        if not target:
            raise DomainError("Logic Model version not found.", status=404)
        activation_id = activation_id or self._new_id("activation")
        with self.database.connection() as connection:
            prior_attempt = connection.execute(
                """
                SELECT * FROM logic_model_activations
                WHERE id = ? AND workspace_id = ?
                """,
                (activation_id, workspace_id),
            ).fetchone()
        if prior_attempt and prior_attempt["logic_model_id"] != logic_model_id:
            raise DomainError("This activation identifier belongs to another Logic Model.", status=409)
        if prior_attempt and prior_attempt["status"] in {"completed", "no_op"}:
            return self.logic_model(logic_model_id)
        is_retry = bool(prior_attempt and prior_attempt["status"] == "failed")
        prior_error = prior_attempt["error_message"] if is_retry else None

        if current and current["id"] == logic_model_id:
            now = utc_now()
            manifest = {
                "policy_version": ACTIVATION_POLICY_VERSION,
                "outcome": "no_op",
                "reason": "target_already_current",
                "records": [],
            }
            with self.database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO logic_model_activations
                        (id, logic_model_id, previous_logic_model_id, actor_id, reason,
                         activated_at, status, impact_manifest_json, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'no_op', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET status = 'no_op',
                        impact_manifest_json = excluded.impact_manifest_json,
                        error_message = NULL, completed_at = excluded.completed_at
                    """,
                    (
                        activation_id, logic_model_id, logic_model_id, actor_id, reason,
                        now, json.dumps(manifest, ensure_ascii=True), now,
                    ),
                )
                self._audit(
                    connection,
                    "logic_model_activation_no_op",
                    "logic_model",
                    logic_model_id,
                    {"activation_id": activation_id, "actor_id": actor_id},
                )
            return self.logic_model(logic_model_id)

        draft = self.logic_model_draft()
        if reason == "restore" and draft and not discard_draft:
            raise DomainError(
                "Any changes or additions to other Logic Models will be lost. To save them, "
                "you should cancel this rollback, update the Logic Model from draft to current, "
                "then roll back to this version.",
                status=409,
            )
        if impact is None and current:
            impact = self._logic_model_change_summary(
                self.logic_model(current["id"]), self.logic_model(logic_model_id)
            )["impact"]
        impact = impact or {
            "requires_assessment": False,
            "recap_only": False,
            "changed_stable_ids": [],
            "has_new_elements": False,
        }

        now = utc_now()
        previous_id = current["id"] if current else None
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO logic_model_activations
                    (id, logic_model_id, previous_logic_model_id, actor_id, reason,
                     activated_at, status, impact_manifest_json)
                VALUES (?, ?, ?, ?, ?, ?, 'preparing', '{}')
                ON CONFLICT(id) DO UPDATE SET status = 'preparing', error_message = NULL,
                    previous_logic_model_id = excluded.previous_logic_model_id,
                    actor_id = excluded.actor_id, reason = excluded.reason
                """,
                (activation_id, logic_model_id, previous_id, actor_id, reason, now),
            )
            if is_retry:
                self._audit(
                    connection,
                    "logic_model_activation_retried",
                    "logic_model",
                    logic_model_id,
                    {
                        "activation_id": activation_id,
                        "actor_id": actor_id,
                        "previous_error": prior_error,
                    },
                )

        source_model = self.logic_model(previous_id) if previous_id else None
        target_model = self.logic_model(logic_model_id)
        source_elements = {
            item["stable_id"]: item for item in (source_model or {}).get("elements", [])
        }
        target_elements = {item["stable_id"]: item for item in target_model["elements"]}
        added_stable_ids = set(impact.get("added_stable_ids") or ())
        edited_stable_ids = set(impact.get("edited_stable_ids") or ())
        deleted_stable_ids = set(impact.get("deleted_stable_ids") or ())
        if impact.get("requires_assessment") and not (
            added_stable_ids or edited_stable_ids or deleted_stable_ids
        ):
            added_stable_ids = set(target_elements) - set(source_elements)
            deleted_stable_ids = set(source_elements) - set(target_elements)
            edited_stable_ids = {
                stable_id
                for stable_id in set(source_elements) & set(target_elements)
                if int(source_elements[stable_id].get("revision", 1))
                != int(target_elements[stable_id].get("revision", 1))
            }

        full_catalogue = (
            self._catalogue_for_matching(logic_model_id)
            if impact.get("requires_assessment")
            else []
        )
        target_catalogue_by_id = {item["id"]: item for item in full_catalogue}
        target_catalogue_by_stable = {
            item.get("stable_id"): item for item in full_catalogue if item.get("stable_id")
        }
        changed_catalogue = [
            item
            for item in full_catalogue
            if item.get("stable_id") in added_stable_ids | edited_stable_ids
        ]
        prepared_analyses: list[tuple[str, AnalysisResult]] = []
        prepared_assessments: list[dict[str, Any]] = []
        manifest_records: list[dict[str, Any]] = []
        with self.database.connection() as connection:
            pending_rows = []
            evidence_rows = []
            if impact["requires_assessment"]:
                pending_rows = connection.execute(
                    """
                    SELECT * FROM statements
                    WHERE status = 'awaiting_review' AND workspace_id = ?
                    ORDER BY submitted_at
                    """,
                    (workspace_id,),
                ).fetchall()
                evidence_rows = connection.execute(
                    """
                    SELECT e.id AS evidence_id, s.original_text, a.*
                    FROM evidence e
                    JOIN statements s ON s.id = e.statement_id
                    JOIN analyses a ON a.id = e.analysis_id
                    WHERE e.workspace_id = ?
                    """
                    ,
                    (workspace_id,),
                ).fetchall()
            pending_values = [self._statement_from_row(connection, row) for row in pending_rows]

        try:
            for statement in pending_values:
                baseline = [
                    {
                        "element_id": item["id"],
                        "stable_id": item.get("stable_id"),
                        "label": item["label"],
                        "confidence": item.get("confidence"),
                        "rationale": item.get("rationale") or "",
                    }
                    for item in statement["mappings"]
                    if item.get("stable_id")
                ]
                has_deleted_mapping = any(
                    item["stable_id"] in deleted_stable_ids for item in baseline
                )
                catalogue = full_catalogue if has_deleted_mapping else changed_catalogue
                if not catalogue:
                    manifest_records.append(
                        {
                            "record_type": "statement",
                            "record_id": statement["id"],
                            "outcome": "excluded",
                            "reason": "no_changed_nodes_to_assess",
                        }
                    )
                    continue
                result = self._apply_mapping_proposal_threshold(
                    self.model.analyze(
                        statement["original_text"],
                        catalogue,
                        current_analysis=statement["analysis"],
                        reviewer_comments="Reassess this Statement against the newly current Logic Model.",
                    )
                )
                merged = self._merge_target_mappings(
                    baseline,
                    result,
                    target_catalogue_by_id,
                    target_catalogue_by_stable,
                    edited_stable_ids,
                )
                current_stable = {item["stable_id"] for item in baseline}
                proposed_stable = {
                    target_catalogue_by_id[item.element_id].get("stable_id")
                    for item in merged
                    if item.element_id in target_catalogue_by_id
                }
                if (
                    result.classification != statement["analysis"]["classification"]
                    or proposed_stable != current_stable
                ):
                    prepared_analyses.append(
                        (
                            statement["id"],
                            replace(
                                result,
                                mapping_decisions=merged,
                                match_status="existing_match" if merged else "no_suitable_match",
                            ),
                        )
                    )
                    outcome = "reanalyzed"
                else:
                    outcome = "excluded"
                manifest_records.append(
                    {
                        "record_type": "statement",
                        "record_id": statement["id"],
                        "outcome": outcome,
                        "reason": (
                            "classification_or_mapping_changed"
                            if outcome == "reanalyzed"
                            else "no_classification_or_mapping_change"
                        ),
                    }
                )
            for row in evidence_rows:
                with self.database.connection() as connection:
                    target_assessment = connection.execute(
                        """
                        SELECT * FROM evidence_assessments
                        WHERE evidence_id = ? AND logic_model_id = ?
                        """,
                        (row["evidence_id"], logic_model_id),
                    ).fetchone()
                    if target_assessment and (
                        not target_assessment["accepted_analysis_id"]
                        or target_assessment["accepted_analysis_id"] == row["id"]
                    ):
                        outcome = (
                            "resumed" if target_assessment["status"] == "pending" else "reused"
                        )
                        manifest_records.append(
                            {
                                "record_type": "evidence",
                                "record_id": row["evidence_id"],
                                "assessment_id": target_assessment["id"],
                                "outcome": outcome,
                                "reason": (
                                    "unfinished_exact_assessment"
                                    if outcome == "resumed"
                                    else "decided_exact_assessment"
                                ),
                            }
                        )
                        continue
                    source_assessment = connection.execute(
                        """
                        SELECT * FROM evidence_assessments
                        WHERE evidence_id = ? AND logic_model_id = ?
                          AND status IN ('approved', 'not_relevant')
                        """,
                        (row["evidence_id"], previous_id),
                    ).fetchone() if previous_id else None
                    if source_assessment and source_assessment["status"] == "approved":
                        baseline = self._assessment_mapping_baseline(
                            connection, source_assessment["id"]
                        )
                    elif source_assessment and source_assessment["status"] == "not_relevant":
                        baseline = []
                    else:
                        baseline = self._analysis_mapping_baseline(connection, row["id"])

                current_analysis = dict(row)
                current_analysis["outcome_change_types"] = json.loads(
                    current_analysis["outcome_change_types"]
                )
                source_classification = (
                    source_assessment["confirmed_classification"]
                    if source_assessment and source_assessment["confirmed_classification"]
                    else row["classification"]
                )
                current_analysis["classification"] = source_classification
                source_direction = (
                    source_assessment["confirmed_result_direction"]
                    if source_assessment and source_assessment["confirmed_result_direction"]
                    else row["result_direction"]
                )
                current_analysis["result_direction"] = (
                    source_direction if source_classification == "Outcome" else None
                )
                has_deleted_mapping = any(
                    item["stable_id"] in deleted_stable_ids for item in baseline
                )
                catalogue = full_catalogue if has_deleted_mapping else changed_catalogue
                if not catalogue:
                    manifest_records.append(
                        {
                            "record_type": "evidence",
                            "record_id": row["evidence_id"],
                            "outcome": "excluded",
                            "reason": "no_changed_nodes_to_assess",
                        }
                    )
                    continue
                result = self._apply_mapping_proposal_threshold(
                    self.model.analyze(
                        row["original_text"],
                        catalogue,
                        current_analysis=current_analysis,
                        reviewer_comments="Suggest mapping only for reassessment under the newly current Logic Model.",
                    )
                )
                merged = self._merge_target_mappings(
                    baseline,
                    result,
                    target_catalogue_by_id,
                    target_catalogue_by_stable,
                    edited_stable_ids,
                )
                proposed = self._mapping_records(merged, target_catalogue_by_id)
                change_set = self._evidence_change_set(
                    source_classification=source_classification,
                    proposed_classification=result.classification,
                    baseline=baseline,
                    proposed=proposed,
                    source_version=(source_model or {}).get("version"),
                    target_version=target_model["version"],
                    deleted_stable_ids=deleted_stable_ids,
                )
                if change_set:
                    assessment_id = self._new_id("assessment")
                    suggested_direction = (
                        source_direction
                        if source_classification == "Outcome"
                        and result.classification == "Outcome"
                        else result.result_direction
                        if result.classification == "Outcome"
                        else None
                    )
                    prepared_assessments.append(
                        {
                            "id": assessment_id,
                            "evidence_id": row["evidence_id"],
                            "accepted_analysis_id": row["id"],
                            "source_logic_model_id": previous_id,
                            "source_assessment_id": source_assessment["id"] if source_assessment else None,
                            "classification": result.classification,
                            "result_direction": suggested_direction,
                            "mapping_details": [
                                {
                                    "element_id": item.element_id,
                                    "confidence": item.confidence,
                                    "rationale": item.rationale,
                                }
                                for item in merged
                            ],
                            "rationale": result.no_match_rationale,
                            "change_set": change_set,
                        }
                    )
                    outcome = "new"
                else:
                    outcome = "excluded"
                manifest_records.append(
                    {
                        "record_type": "evidence",
                        "record_id": row["evidence_id"],
                        "outcome": outcome,
                        "assessment_id": assessment_id if outcome == "new" else None,
                        "reason": "material_change" if outcome == "new" else "no_material_change",
                        "observations": (
                            []
                            if outcome == "new"
                            else self._non_material_mapping_observations(baseline, proposed)
                        ),
                    }
                )
        except (ModelError, ValueError, KeyError, TypeError) as exc:
            failed_manifest = {
                "policy_version": ACTIVATION_POLICY_VERSION,
                "activation_id": activation_id,
                "retry": is_retry,
                "source_logic_model_id": previous_id,
                "target_logic_model_id": logic_model_id,
                "impact": impact,
                "records": manifest_records,
                "outcome": "failed",
                "reason": "preparation_failed",
                "error": str(exc),
            }
            with self.database.connection() as connection:
                connection.execute(
                    "UPDATE logic_model_activations SET status = 'failed', error_message = ?, "
                    "impact_manifest_json = ? WHERE id = ?",
                    (str(exc), json.dumps(failed_manifest, ensure_ascii=True), activation_id),
                )
                self._audit(
                    connection,
                    "logic_model_activation_failed",
                    "logic_model",
                    logic_model_id,
                    {"activation_id": activation_id, "phase": "preparation", "error": str(exc)},
                )
            raise DomainError(
                f"The Logic Model was stored, but activation could not prepare reassessment: {exc}",
                status=502,
            ) from exc

        manifest = {
            "policy_version": ACTIVATION_POLICY_VERSION,
            "activation_id": activation_id,
            "retry": is_retry,
            "source_logic_model_id": previous_id,
            "target_logic_model_id": logic_model_id,
            "impact": impact,
            "records": manifest_records,
        }
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE logic_model_activations SET impact_manifest_json = ? WHERE id = ?",
                (json.dumps(manifest, ensure_ascii=True), activation_id),
            )
            self._audit(
                connection,
                "logic_model_activation_prepared",
                "logic_model",
                logic_model_id,
                {
                    "activation_id": activation_id,
                    "record_count": len(manifest_records),
                    "retry": is_retry,
                },
            )
        completed_at = utc_now()
        try:
            with self.database.connection() as connection:
                live_current = connection.execute(
                    """
                    SELECT id FROM logic_models
                    WHERE is_current = 1 AND workspace_id = ? LIMIT 1
                    """,
                    (workspace_id,),
                ).fetchone()
                if (live_current["id"] if live_current else None) != previous_id:
                    raise DomainError(
                        "The current Logic Model changed while activation was preparing. Retry activation.",
                        status=409,
                    )
                connection.execute(
                    """
                    UPDATE logic_models SET is_current = 0
                    WHERE is_current = 1 AND workspace_id = ?
                    """,
                    (workspace_id,),
                )
                connection.execute(
                    """
                    UPDATE logic_models SET is_current = 1
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (logic_model_id, workspace_id),
                )
                for statement_id, result in prepared_analyses:
                    analysis_id = self._persist_analysis(
                        connection,
                        statement_id,
                        result,
                        reviewer_instruction="Reanalyzed after Logic Model activation.",
                        logic_model_id=logic_model_id,
                    )
                    connection.execute(
                        "UPDATE statements SET active_analysis_id = ?, processing_error = NULL WHERE id = ?",
                        (analysis_id, statement_id),
                    )
                for item in prepared_assessments:
                    mapping_ids = [mapping["element_id"] for mapping in item["mapping_details"]]
                    connection.execute(
                        """
                        INSERT INTO evidence_assessments
                            (id, evidence_id, logic_model_id, status, suggested_mapping_ids,
                             suggested_mapping_details, mapping_rationale,
                             suggested_classification, suggested_result_direction,
                             accepted_analysis_id,
                             source_logic_model_id, source_assessment_id, activation_id,
                             change_set_json, created_at)
                        VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["id"], item["evidence_id"], logic_model_id,
                            json.dumps(mapping_ids), json.dumps(item["mapping_details"]),
                            item["rationale"], item["classification"], item["result_direction"],
                            item["accepted_analysis_id"], item["source_logic_model_id"],
                            item["source_assessment_id"], activation_id,
                            json.dumps(item["change_set"], ensure_ascii=True), now,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE logic_model_activations
                    SET status = 'completed', impact_manifest_json = ?,
                        error_message = NULL, completed_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(manifest, ensure_ascii=True), completed_at, activation_id),
                )
                if reason == "restore" and draft and discard_draft:
                    connection.execute(
                        "DELETE FROM logic_model_drafts WHERE workspace_id = ?",
                        (workspace_id,),
                    )
                self._audit(
                    connection,
                    "logic_model_activated",
                    "logic_model",
                    logic_model_id,
                    {
                        "activation_id": activation_id,
                        "actor_id": actor_id,
                        "previous_logic_model_id": previous_id,
                        "reason": reason,
                        "impact": impact,
                        "reanalyzed_statements": len(prepared_analyses),
                        "queued_evidence": len(prepared_assessments),
                        "outcomes": {
                            value: sum(1 for record in manifest_records if record["outcome"] == value)
                            for value in {record["outcome"] for record in manifest_records}
                        },
                    },
                )
        except Exception as exc:
            with self.database.connection() as connection:
                connection.execute(
                    "UPDATE logic_model_activations SET status = 'failed', error_message = ? WHERE id = ?",
                    (str(exc), activation_id),
                )
                self._audit(
                    connection,
                    "logic_model_activation_failed",
                    "logic_model",
                    logic_model_id,
                    {"activation_id": activation_id, "phase": "commit", "error": str(exc)},
                )
            raise
        return self.logic_model(logic_model_id)

    @staticmethod
    def _analysis_mapping_baseline(
        connection: DatabaseConnection, analysis_id: str
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT am.element_id, el.stable_id, el.label, am.confidence, am.rationale
                FROM analysis_mappings am
                JOIN elements el ON el.id = am.element_id
                WHERE am.analysis_id = ? ORDER BY am.rank
                """,
                (analysis_id,),
            ).fetchall()
        ]

    @staticmethod
    def _assessment_mapping_baseline(
        connection: DatabaseConnection, assessment_id: str
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT eam.element_id, el.stable_id, el.label,
                       eam.confidence, eam.rationale
                FROM evidence_assessment_mappings eam
                JOIN elements el ON el.id = eam.element_id
                WHERE eam.assessment_id = ? ORDER BY eam.rank
                """,
                (assessment_id,),
            ).fetchall()
        ]

    @staticmethod
    def _merge_target_mappings(
        baseline: list[dict[str, Any]],
        result: AnalysisResult,
        target_by_id: dict[str, dict[str, Any]],
        target_by_stable: dict[str, dict[str, Any]],
        edited_stable_ids: set[str],
    ) -> list[MappingDecision]:
        result_by_stable = {
            target_by_id[item.element_id].get("stable_id"): item
            for item in result.mapping_decisions
            if item.element_id in target_by_id
        }
        merged: list[MappingDecision] = []
        represented: set[str] = set()
        for mapping in baseline:
            stable_id = mapping.get("stable_id")
            target = target_by_stable.get(stable_id)
            if not stable_id or not target:
                continue
            revised = result_by_stable.get(stable_id) if stable_id in edited_stable_ids else None
            merged.append(
                revised
                or MappingDecision(
                    element_id=target["id"],
                    confidence=float(mapping.get("confidence") or 0.0),
                    rationale=str(mapping.get("rationale") or ""),
                )
            )
            represented.add(stable_id)
        for decision in result.mapping_decisions:
            target = target_by_id.get(decision.element_id)
            stable_id = target.get("stable_id") if target else None
            if stable_id and stable_id not in represented:
                merged.append(decision)
                represented.add(stable_id)
        return merged

    @staticmethod
    def _mapping_records(
        decisions: list[MappingDecision], target_by_id: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        values = []
        for decision in decisions:
            target = target_by_id.get(decision.element_id)
            if not target:
                continue
            values.append(
                {
                    "element_id": decision.element_id,
                    "stable_id": target.get("stable_id"),
                    "label": target["label"],
                    "confidence": decision.confidence,
                    "rationale": decision.rationale,
                }
            )
        return values

    @staticmethod
    def _non_material_mapping_observations(
        baseline: list[dict[str, Any]], proposed: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        before = {item.get("stable_id"): item for item in baseline if item.get("stable_id")}
        after = {item.get("stable_id"): item for item in proposed if item.get("stable_id")}
        observations = []
        for stable_id in before.keys() & after.keys():
            old, new = before[stable_id], after[stable_id]
            old_confidence = float(old.get("confidence") or 0.0)
            new_confidence = float(new.get("confidence") or 0.0)
            delta = abs(new_confidence - old_confidence)
            rationale_changed = str(old.get("rationale") or "") != str(
                new.get("rationale") or ""
            )
            if delta or rationale_changed:
                observations.append(
                    {
                        "affected_node_stable_id": stable_id,
                        "confidence_delta_percentage_points": round(delta * 100, 1),
                        "rationale_changed": rationale_changed,
                        "outcome": "no_material_change",
                    }
                )
        return observations

    @staticmethod
    def _evidence_change_set(
        *,
        source_classification: str,
        proposed_classification: str,
        baseline: list[dict[str, Any]],
        proposed: list[dict[str, Any]],
        source_version: str | None,
        target_version: str,
        deleted_stable_ids: set[str],
    ) -> list[dict[str, Any]]:
        def entry(kind: str, **values: Any) -> dict[str, Any]:
            return {
                "type": kind,
                "source_logic_model_version": source_version,
                "target_logic_model_version": target_version,
            } | values

        changes: list[dict[str, Any]] = []
        if source_classification != proposed_classification:
            changes.append(
                entry(
                    "classification_changed",
                    previous_value=source_classification,
                    proposed_value=proposed_classification,
                    explanation=(
                        f"Classification changed from {source_classification} "
                        f"to {proposed_classification}."
                    ),
                )
            )
        before = {item.get("stable_id"): item for item in baseline if item.get("stable_id")}
        after = {item.get("stable_id"): item for item in proposed if item.get("stable_id")}
        removed = [before[key] for key in before.keys() - after.keys()]
        added = [after[key] for key in after.keys() - before.keys()]
        if len(removed) == 1 and len(added) == 1:
            old, new = removed[0], added[0]
            changes.append(
                entry(
                    "mapping_replaced",
                    affected_node_stable_id=old["stable_id"],
                    previous_value={"stable_id": old["stable_id"], "label": old["label"]},
                    proposed_value={"stable_id": new["stable_id"], "label": new["label"]},
                    previous_confidence=old.get("confidence"),
                    proposed_confidence=new.get("confidence"),
                    explanation=f'"{old["label"]}" was replaced by "{new["label"]}".',
                )
            )
        else:
            for old in removed:
                kind = (
                    "no_suitable_match"
                    if old["stable_id"] in deleted_stable_ids and not added
                    else "mapping_removed"
                )
                changes.append(
                    entry(
                        kind,
                        affected_node_stable_id=old["stable_id"],
                        previous_value={"stable_id": old["stable_id"], "label": old["label"]},
                        proposed_value=None,
                        previous_confidence=old.get("confidence"),
                        proposed_confidence=None,
                        explanation=(
                            f'No suitable current Logic Model match was found for "{old["label"]}".'
                            if kind == "no_suitable_match"
                            else f'The mapping to "{old["label"]}" was removed.'
                        ),
                    )
                )
            for new in added:
                changes.append(
                    entry(
                        "mapping_added",
                        affected_node_stable_id=new["stable_id"],
                        previous_value=None,
                        proposed_value={"stable_id": new["stable_id"], "label": new["label"]},
                        previous_confidence=None,
                        proposed_confidence=new.get("confidence"),
                        explanation=f'A mapping to "{new["label"]}" was added.',
                    )
                )
        for stable_id in before.keys() & after.keys():
            old, new = before[stable_id], after[stable_id]
            old_confidence = float(old.get("confidence") or 0.0)
            new_confidence = float(new.get("confidence") or 0.0)
            delta = abs(new_confidence - old_confidence)
            if delta + 1e-9 >= EVIDENCE_CONFIDENCE_REVIEW_THRESHOLD:
                changes.append(
                    entry(
                        "mapping_confidence_changed",
                        affected_node_stable_id=stable_id,
                        previous_value={"stable_id": stable_id, "label": old["label"]},
                        proposed_value={"stable_id": stable_id, "label": new["label"]},
                        previous_confidence=old_confidence,
                        proposed_confidence=new_confidence,
                        confidence_delta_percentage_points=round(delta * 100, 1),
                        explanation=(
                            f'Confidence for "{new["label"]}" changed from '
                            f"{round(old_confidence * 100)}% to {round(new_confidence * 100)}%."
                        ),
                    )
                )
        return changes

    def classification_agreement(self) -> dict[str, Any]:
        workspace_id = self._workspace_id()
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN initial_classification = approved_classification THEN 1 ELSE 0 END) AS matches,
                    COUNT(*) AS total
                FROM statements
                WHERE approved_classification IS NOT NULL AND workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
        matches = int(row["matches"] or 0)
        total = int(row["total"] or 0)
        return {
            "matches": matches,
            "total": total,
            "percentage": round(matches / total * 100, 1) if total else None,
        }

    def workspace_metrics(self) -> dict[str, Any]:
        """Return the established lifetime review measures for the active workspace."""
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scoring_events
                WHERE workspace_id = melvan_workspace_id()
                ORDER BY created_at, id
                """
            ).fetchall()
            completed_sessions = connection.execute(
                """
                SELECT COUNT(*) FROM review_sessions
                WHERE workspace_id = melvan_workspace_id() AND status = 'completed'
                """
            ).fetchone()[0]
        return {
            "scoring_policy_version": SCORING_POLICY_VERSION,
            "scored_reviews": len(rows),
            "completed_review_sessions": int(completed_sessions),
            "lifetime_scores": self._score_summary(rows),
        }

    def list_users(
        self,
        *,
        include_administrators: bool = False,
        include_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace_id = self._workspace_id()
        users = self.users.list_users(
            workspace_id,
            include_administrators=include_administrators,
            include_user_id=include_user_id,
        )
        with self.database.connection() as connection:
            statement_counts = {
                row["reporter_id"]: row["record_count"]
                for row in connection.execute(
                    """
                    SELECT reporter_id, COUNT(*) AS record_count
                    FROM statements
                    WHERE program_irrelevant_at IS NULL AND workspace_id = ?
                    GROUP BY reporter_id
                    """,
                    (workspace_id,),
                ).fetchall()
            }
            review_counts = {
                row["reviewer_id"]: row["record_count"]
                for row in connection.execute(
                    """
                    SELECT reviewer_id, COUNT(*) AS record_count
                    FROM (
                        SELECT reviewer_id, statement_id
                        FROM review_feedback
                        WHERE reviewer_id IS NOT NULL AND workspace_id = ?
                        UNION
                        SELECT accepted_by AS reviewer_id, statement_id
                        FROM evidence
                        WHERE accepted_by IS NOT NULL AND workspace_id = ?
                        UNION
                        SELECT reviewer_id, statement_id
                        FROM review_events
                        WHERE workspace_id = ?
                    )
                    GROUP BY reviewer_id
                    """,
                    (workspace_id, workspace_id, workspace_id),
                ).fetchall()
            }
        for user in users:
            user["statement_count"] = int(statement_counts.get(user["id"], 0))
            user["reviewed_statement_count"] = int(review_counts.get(user["id"], 0))
        return users

    def list_administrators(self) -> list[dict[str, Any]]:
        users = self.users.list_administrators()
        user_ids = {user["id"] for user in users}
        with self.database.connection() as connection:
            statement_counts = {
                row["reporter_id"]: int(row["record_count"])
                for row in connection.execute(
                    """
                    SELECT reporter_id, COUNT(*) AS record_count
                    FROM statements
                    WHERE program_irrelevant_at IS NULL
                    GROUP BY reporter_id
                    """
                ).fetchall()
                if row["reporter_id"] in user_ids
            }
            review_counts = {
                row["reviewer_id"]: int(row["record_count"])
                for row in connection.execute(
                    """
                    SELECT reviewer_id, COUNT(*) AS record_count
                    FROM (
                        SELECT reviewer_id, statement_id FROM review_feedback
                        WHERE reviewer_id IS NOT NULL
                        UNION
                        SELECT accepted_by AS reviewer_id, statement_id FROM evidence
                        WHERE accepted_by IS NOT NULL
                        UNION
                        SELECT reviewer_id, statement_id FROM review_events
                    )
                    GROUP BY reviewer_id
                    """
                ).fetchall()
                if row["reviewer_id"] in user_ids
            }
        for user in users:
            user["statement_count"] = statement_counts.get(user["id"], 0)
            user["reviewed_statement_count"] = review_counts.get(user["id"], 0)
        return users

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a credential-less local fixture user for service-level tests."""
        try:
            return self.users.create_user(
                payload,
                actor_id="user-admin",
                workspace_id=self._workspace_id(),
            )
        except IdentityError as exc:
            raise DomainError(str(exc), status=exc.status) from exc

    def update_user(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str,
        actor_is_owner: bool,
    ) -> dict[str, Any]:
        try:
            return self.users.update_user(
                user_id,
                payload,
                actor_id=actor_id,
                authorized_workspace_id=self._workspace_id(),
                actor_is_owner=actor_is_owner,
            )
        except IdentityError as exc:
            raise DomainError(str(exc), status=exc.status) from exc

    def set_user_status(
        self,
        user_id: str,
        status: str,
        *,
        actor_id: str,
        actor_is_owner: bool,
    ) -> dict[str, Any]:
        try:
            return self.users.set_status(
                user_id,
                status,
                authorized_workspace_id=self._workspace_id(),
                actor_is_owner=actor_is_owner,
                actor_id=actor_id,
            )
        except IdentityError as exc:
            raise DomainError(str(exc), status=exc.status) from exc

    def evidence_review_items(self, reviewer_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            current = connection.execute(
                """
                SELECT id FROM logic_models
                WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                LIMIT 1
                """
            ).fetchone()
            if not current:
                return []
            reviewer_clause = "AND s.reporter_id <> ?" if reviewer_id else ""
            parameters: list[Any] = [current["id"]]
            if reviewer_id:
                parameters.append(reviewer_id)
            rows = connection.execute(
                f"""
                SELECT ea.*, e.statement_id, e.analysis_id, e.accepted_at,
                       s.original_text, s.reporter_id, a.summary, a.classification,
                       a.logic_model_id AS accepted_logic_model_id,
                       a.mapping_rationale AS accepted_mapping_rationale
                FROM evidence_assessments ea
                JOIN evidence e ON e.id = ea.evidence_id
                JOIN statements s ON s.id = e.statement_id
                JOIN analyses a ON a.id = e.analysis_id
                WHERE ea.logic_model_id = ? AND ea.status = 'pending'
                  AND ea.workspace_id = melvan_workspace_id()
                  {reviewer_clause}
                ORDER BY e.accepted_at DESC, ea.id DESC
                """,
                parameters,
            ).fetchall()
            return [self._evidence_assessment_from_row(connection, row) for row in rows]

    def decide_evidence_assessment(
        self,
        assessment_id: str,
        payload: dict[str, Any],
        reviewer_id: str,
    ) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        action = {"confirm": "accept", "remap": "change"}.get(action, action)
        feedback = " ".join(str(payload.get("feedback") or "").split()) or None
        try:
            language_issue_types = self.learning.validate_language_feedback(
                payload.get("language_issue_types"), feedback
            )
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT ea.*, e.statement_id, e.analysis_id, e.accepted_at,
                       s.original_text, s.reporter_id, a.summary, a.classification,
                       a.result_direction,
                       a.logic_model_id AS accepted_logic_model_id,
                       a.mapping_rationale AS accepted_mapping_rationale
                FROM evidence_assessments ea
                JOIN evidence e ON e.id = ea.evidence_id
                JOIN statements s ON s.id = e.statement_id
                JOIN analyses a ON a.id = e.analysis_id
                WHERE ea.id = ?
                  AND ea.workspace_id = melvan_workspace_id()
                """,
                (assessment_id,),
            ).fetchone()
            current = connection.execute(
                """
                SELECT id FROM logic_models
                WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                LIMIT 1
                """
            ).fetchone()
        if not row:
            raise DomainError("Evidence review item not found.", status=404)
        if not current or row["logic_model_id"] != current["id"]:
            raise DomainError("This Evidence review is no longer for the current Logic Model.", status=409)
        if row["status"] != "pending":
            raise DomainError("This Evidence review item has already been decided.", status=409)
        if row["reporter_id"] == reviewer_id:
            raise DomainError("Reviewers cannot review Evidence from their own Statements.", status=403)
        expected_identity = f'{row["id"]}:{row["suggestion_version"]}'
        if payload.get("suggestion_identity") != expected_identity:
            raise DomainError(
                "This Evidence suggestion has changed. Reload Review before deciding it.",
                status=409,
            )

        if action == "revise":
            if not feedback:
                raise DomainError("Enter feedback for the revised mapping suggestion.")
            with self.database.connection() as connection:
                analysis = connection.execute(
                    "SELECT * FROM analyses WHERE id = ?", (row["analysis_id"],)
                ).fetchone()
                if row["source_assessment_id"]:
                    baseline = self._assessment_mapping_baseline(
                        connection, row["source_assessment_id"]
                    )
                    source = connection.execute(
                        "SELECT confirmed_classification, confirmed_result_direction "
                        "FROM evidence_assessments WHERE id = ?",
                        (row["source_assessment_id"],),
                    ).fetchone()
                    source_classification = (
                        source["confirmed_classification"]
                        if source and source["confirmed_classification"]
                        else row["classification"]
                    )
                else:
                    baseline = self._analysis_mapping_baseline(connection, row["analysis_id"])
                    source_classification = row["classification"]
                    source = None
            current_analysis = dict(analysis)
            current_analysis["outcome_change_types"] = json.loads(
                current_analysis["outcome_change_types"]
            )
            source_direction = (
                source["confirmed_result_direction"]
                if source and source["confirmed_result_direction"]
                else row["result_direction"]
            )
            current_analysis["classification"] = source_classification
            current_analysis["result_direction"] = (
                source_direction if source_classification == "Outcome" else None
            )
            try:
                result = self._apply_mapping_proposal_threshold(
                    self.model.analyze(
                        row["original_text"],
                        self._catalogue_for_matching(row["logic_model_id"]),
                        current_analysis=current_analysis,
                        reviewer_comments=feedback,
                    )
                )
            except (ModelError, ValueError, KeyError, TypeError) as exc:
                raise DomainError(f"The revised mapping suggestion failed: {exc}", status=502) from exc
            with self.database.connection() as connection:
                target_catalogue = self._catalogue_for_matching(row["logic_model_id"])
                target_by_id = {item["id"]: item for item in target_catalogue}
                target_by_stable = {
                    item["stable_id"]: item for item in target_catalogue if item.get("stable_id")
                }
                merged = self._merge_target_mappings(
                    baseline,
                    result,
                    target_by_id,
                    target_by_stable,
                    set(target_by_stable),
                )
                proposed = self._mapping_records(merged, target_by_id)
                source_model = connection.execute(
                    "SELECT version FROM logic_models WHERE id = ?",
                    (row["source_logic_model_id"],),
                ).fetchone()
                target_model = connection.execute(
                    "SELECT version FROM logic_models WHERE id = ?",
                    (row["logic_model_id"],),
                ).fetchone()
                deleted = {item["stable_id"] for item in baseline} - set(target_by_stable)
                change_set = self._evidence_change_set(
                    source_classification=source_classification,
                    proposed_classification=result.classification,
                    baseline=baseline,
                    proposed=proposed,
                    source_version=source_model["version"] if source_model else None,
                    target_version=target_model["version"] if target_model else "",
                    deleted_stable_ids=deleted,
                )
                connection.execute(
                    """
                    UPDATE evidence_assessments
                    SET suggested_mapping_ids = ?, suggested_mapping_details = ?,
                        mapping_rationale = ?,
                        suggested_classification = ?, suggested_result_direction = ?,
                        change_set_json = ?,
                        reviewer_feedback = ?, revision_count = revision_count + 1,
                        suggestion_version = suggestion_version + 1
                    WHERE id = ?
                    """,
                    (
                        json.dumps([item.element_id for item in merged]),
                        json.dumps(
                            [
                                {
                                    "element_id": item.element_id,
                                    "confidence": item.confidence,
                                    "rationale": item.rationale,
                                }
                                for item in merged
                            ]
                        ),
                        result.no_match_rationale,
                        result.classification,
                        result.result_direction if result.classification == "Outcome" else None,
                        json.dumps(change_set, ensure_ascii=True),
                        feedback,
                        assessment_id,
                    ),
                )
                updated = connection.execute(
                    """
                    SELECT ea.*, e.statement_id, e.analysis_id, e.accepted_at,
                           s.original_text, s.reporter_id, a.summary, a.classification,
                           a.result_direction,
                           a.logic_model_id AS accepted_logic_model_id,
                           a.mapping_rationale AS accepted_mapping_rationale
                    FROM evidence_assessments ea
                    JOIN evidence e ON e.id = ea.evidence_id
                    JOIN statements s ON s.id = e.statement_id
                    JOIN analyses a ON a.id = e.analysis_id
                    WHERE ea.id = ?
                    """,
                    (assessment_id,),
                ).fetchone()
                self._audit(
                    connection,
                    "evidence_mapping_revision_requested",
                    "evidence_assessment",
                    assessment_id,
                    {"reviewer_id": reviewer_id, "feedback": feedback},
                )
                return self._evidence_assessment_from_row(connection, updated)

        suggested_details = {
            item["element_id"]: item
            for item in json.loads(row["suggested_mapping_details"] or "[]")
        }
        suggested_ids = [
            element_id
            for element_id in json.loads(row["suggested_mapping_ids"] or "[]")
            if self._mapping_confidence_qualifies(
                suggested_details.get(element_id, {}).get("confidence")
            )
        ]
        suggested_classification = row["suggested_classification"] or row["classification"]
        suggested_direction = (
            (row["suggested_result_direction"] or row["result_direction"])
            if suggested_classification == "Outcome"
            else None
        )
        if action == "accept":
            mapping_ids = suggested_ids
            confirmed_classification = suggested_classification
            confirmed_direction = suggested_direction
            status = "approved"
            decision = "accepted"
        elif action == "change":
            mapping_ids = list(dict.fromkeys(payload.get("mapping_ids") or []))
            confirmed_classification = str(
                payload.get("classification") or suggested_classification
            )
            if confirmed_classification not in {"Activity", "Output", "Outcome", "Indicator"}:
                raise DomainError("Choose Activity, Output, Outcome, or Indicator.")
            confirmed_direction = (
                str(payload.get("result_direction") or suggested_direction or "")
                if confirmed_classification == "Outcome"
                else None
            )
            if confirmed_classification == "Outcome" and confirmed_direction not in {
                "Positive", "Negative"
            }:
                raise DomainError("Choose Positive or Negative for an Outcome.")
            if (
                mapping_ids == suggested_ids
                and confirmed_classification == suggested_classification
                and confirmed_direction == suggested_direction
                and not feedback
                and not language_issue_types
            ):
                raise DomainError("Make a change or add a comment before applying it.")
            status = "approved"
            decision = "changed"
        elif action == "not_relevant":
            mapping_ids = []
            confirmed_classification = suggested_classification
            confirmed_direction = suggested_direction
            status = "not_relevant"
            decision = "not_relevant"
        else:
            raise DomainError("Choose Accept, Change, Not relevant, or request revision.")
        if status == "approved" and confirmed_classification == "Outcome" and (
            confirmed_direction not in {"Positive", "Negative"}
        ):
            raise DomainError("Choose Positive or Negative for an Outcome.")
        self._validate_version_mapping_ids(mapping_ids, row["logic_model_id"])

        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM evidence_assessment_mappings WHERE assessment_id = ?",
                (assessment_id,),
            )
            for rank, element_id in enumerate(mapping_ids, start=1):
                detail = suggested_details.get(element_id, {})
                authoritative_change = action == "change" and element_id not in suggested_details
                connection.execute(
                    "INSERT INTO evidence_assessment_mappings "
                    "(assessment_id, element_id, rank, confidence, rationale) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        assessment_id,
                        element_id,
                        rank,
                        1.0 if authoritative_change else float(detail.get("confidence") or 1.0),
                        (
                            "Applied directly from authoritative Reviewer feedback."
                            if authoritative_change
                            else detail.get("rationale") or row["mapping_rationale"]
                        ),
                    ),
                )
            connection.execute(
                """
                UPDATE evidence_assessments
                SET status = ?, confirmed_classification = ?,
                    confirmed_result_direction = ?, reviewer_id = ?,
                    reviewer_feedback = ?, disposition_reason = ?, decided_at = ?
                WHERE id = ?
                """,
                (
                    status, confirmed_classification, confirmed_direction,
                    reviewer_id, feedback,
                    feedback if status == "not_relevant" else None,
                    now, assessment_id,
                ),
            )
            self._record_evidence_review_event(
                connection,
                assessment=row,
                reviewer_id=reviewer_id,
                decision=decision,
                comments=feedback,
                confirmed_classification=confirmed_classification,
                confirmed_result_direction=confirmed_direction,
                mapping_ids=mapping_ids,
                suggested_mapping_ids=suggested_ids,
                language_issue_types=language_issue_types,
            )
            self._audit(
                connection,
                "evidence_logic_model_assessed",
                "evidence_assessment",
                assessment_id,
                {
                    "reviewer_id": reviewer_id,
                    "status": status,
                    "mapping_ids": mapping_ids,
                    "confirmed_classification": confirmed_classification,
                    "confirmed_result_direction": confirmed_direction,
                    "suggestion_identity": expected_identity,
                },
            )
            self._complete_if_exhausted(connection, reviewer_id)
        return {"assessment_id": assessment_id, "status": status}

    def _record_evidence_review_event(
        self,
        connection: DatabaseConnection,
        *,
        assessment: DatabaseRow,
        reviewer_id: str,
        decision: str,
        comments: str | None,
        confirmed_classification: str,
        confirmed_result_direction: str | None,
        mapping_ids: list[str],
        suggested_mapping_ids: list[str],
        language_issue_types: list[str],
    ) -> str:
        session = self._open_review_session_row(connection, reviewer_id, create=True)
        if not session:
            raise DomainError("Review session could not be started.", status=409)
        event_id = self._new_id("review-event")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO review_events
                (id, session_id, statement_id, analysis_id, resulting_analysis_id,
                 reviewer_id, decision, comments, created_at, evidence_assessment_id)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                event_id, session["id"], assessment["statement_id"],
                assessment["analysis_id"], reviewer_id, decision, comments,
                now, assessment["id"],
            ),
        )
        if decision == "not_relevant":
            return event_id
        proposed_classification = (
            assessment["suggested_classification"] or assessment["classification"]
        )
        current_analysis = {
            "classification": proposed_classification,
            "result_direction": (
                assessment["suggested_result_direction"] or assessment["result_direction"]
            )
            if proposed_classification == "Outcome"
            else None,
        }
        proposed_direction = current_analysis["result_direction"]
        score = self._statement_score(
            decision,
            {
                "current_analysis": current_analysis,
                "current_mappings": [
                    {
                        "id": item["element_id"],
                        "confidence": item.get("confidence"),
                    }
                    for item in json.loads(
                        assessment["suggested_mapping_details"] or "[]"
                    )
                    if item["element_id"] in suggested_mapping_ids
                ],
                "selected_mapping_ids": (
                    mapping_ids if mapping_ids != suggested_mapping_ids else None
                ),
                "corrected_classification": (
                    confirmed_classification
                    if confirmed_classification != proposed_classification
                    else None
                ),
                "corrected_result_direction": (
                    confirmed_result_direction
                    if confirmed_classification == "Outcome"
                    and confirmed_result_direction != proposed_direction
                    else None
                ),
                "mapping_supplied": mapping_ids != suggested_mapping_ids,
                "comments": comments,
                "language_issue_types": language_issue_types,
            },
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO scoring_events
                (id, cycle_key, cycle_type, statement_id, evidence_assessment_id,
                 first_review_event_id, first_review_approval, correct_level,
                 correct_direction, correct_mapping, overall_hygiene,
                 source_signals_json, explanations_json, scoring_policy_version,
                 interpretation_model_version, interpretation_prompt_version, created_at)
            VALUES (?, ?, 'evidence_reassessment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                self._new_id("score"), f'evidence:{assessment["id"]}',
                assessment["statement_id"], assessment["id"], event_id,
                score["first_review_approval"], score["correct_level"],
                score["correct_direction"], score["correct_mapping"],
                score["overall_hygiene"], json.dumps(score["signals"], ensure_ascii=True),
                json.dumps(score["explanations"], ensure_ascii=True),
                SCORING_POLICY_VERSION,
                COMMENT_INTERPRETATION_VERSION if comments else None,
                now,
            ),
        )
        self.learning.record_evidence_review(
            connection,
            event_id=event_id,
            assessment=assessment,
            reviewer_id=reviewer_id,
            confirmed_classification=confirmed_classification,
            confirmed_result_direction=confirmed_result_direction,
            suggested_mapping_ids=suggested_mapping_ids,
            confirmed_mapping_ids=mapping_ids,
            language_issue_types=language_issue_types,
            comments=comments,
        )
        return event_id

    def health(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            connection.execute("SELECT 1").fetchone()
            fixture = connection.execute(
                "SELECT id, version FROM logic_models WHERE is_current = 1 LIMIT 1"
            ).fetchone()
        owner = self.users.owner()
        identity_status = (
            "bootstrap_required"
            if owner["password_state"] == "bootstrap_required"
            else "ready"
        )
        return {
            "app_version": __version__,
            "status": "ready" if identity_status == "ready" else "bootstrap_required",
            "database": "ready",
            "identity": identity_status,
            "logic_model": dict(fixture),
            "model": self.model.health_check(),
            "speech": self.speech.readiness(),
            "photo_text": self.photo_text.readiness(),
            "attachment_storage": self.attachments.readiness(),
        }

    def learning_policy(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            active = self.learning.active_policy(connection)
            active["available_versions"] = [
                row["version"]
                for row in connection.execute(
                    """
                    SELECT version FROM learning_policies
                    WHERE workspace_id = melvan_workspace_id()
                    ORDER BY created_at, version
                    """
                ).fetchall()
            ]
            return active

    def publish_learning_policy(
        self, payload: dict[str, Any], actor_id: str
    ) -> dict[str, Any]:
        config = payload.get("config") or {}
        if not isinstance(config, dict):
            raise DomainError("Learning-policy configuration must be an object.")
        normalized = {
            "limit": max(1, min(20, int(config.get("limit", 8)))),
            "rolling_window": max(2, min(12, int(config.get("rolling_window", 3)))),
            "minimum_events": max(1, min(1000, int(config.get("minimum_events", 20)))),
        }
        try:
            with self.database.connection() as connection:
                return self.learning.publish_policy(
                    connection,
                    version=str(payload.get("version") or ""),
                    enabled=payload.get("enabled") is not False,
                    config=normalized,
                    actor_id=actor_id,
                )
        except (TypeError, ValueError) as exc:
            raise DomainError(str(exc)) from exc

    def activate_learning_policy(self, version: str, actor_id: str) -> dict[str, Any]:
        try:
            with self.database.connection() as connection:
                return self.learning.activate_policy(
                    connection, version=version, actor_id=actor_id
                )
        except ValueError as exc:
            raise DomainError(str(exc), status=404) from exc

    def learning_examples(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, review_event_id, statement_id, dimension, signal_type,
                       original_confidence, truth_weight, surprise_score,
                       learning_priority, logic_model_id, stable_element_id,
                       policy_version, eligibility_status, exclusion_reason, created_at
                FROM learning_examples
                WHERE workspace_id = melvan_workspace_id()
                ORDER BY created_at DESC, id DESC LIMIT 200
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def export_evidence_pdf(self, actor_id: str) -> dict[str, Any]:
        records = self.evidence()
        attachment_keys: dict[str, str] = {}
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT statement_id, storage_key
                FROM statement_attachments
                WHERE workspace_id = melvan_workspace_id()
                """
            ).fetchall()
            attachment_keys = {
                str(row["statement_id"]): str(row["storage_key"]) for row in rows
            }
        for record in records:
            if not record.get("attachment"):
                continue
            storage_key = attachment_keys.get(str(record["id"]))
            if not storage_key:
                raise DomainError(
                    "An Evidence attachment is unavailable. The PDF was not created.",
                    status=503,
                )
            try:
                record["attachment_content"] = self.attachments.read(storage_key)
            except MediaError as exc:
                raise DomainError(
                    "An Evidence attachment is unavailable. The PDF was not created.",
                    status=503,
                ) from exc
        logic_model = self.logic_model()
        now = datetime.now(timezone.utc)
        user_names = {
            user["id"]: f'{user["first_name"]} {user["last_name"]}'.strip()
            for user in self.list_users()
        }
        content = build_evidence_pdf(
            records,
            logic_model_title=logic_model["title"],
            logic_model_version=logic_model["version"],
            generated_at=now.isoformat(timespec="seconds"),
            user_names=user_names,
        )
        with self.database.connection() as connection:
            self._audit(
                connection,
                "evidence_pdf_exported",
                "evidence_export",
                self._new_id("evidence-export"),
                {
                    "actor_id": actor_id,
                    "record_count": len(records),
                    "logic_model_id": logic_model["database_id"],
                    "logic_model_version": logic_model["version"],
                },
            )
        return {
            "filename": export_filename(now),
            "content_type": "application/pdf",
            "content": content,
            "record_count": len(records),
        }

    def set_learning_example_eligibility(
        self, example_id: str, payload: dict[str, Any], actor_id: str
    ) -> dict[str, Any]:
        if not isinstance(payload.get("eligible"), bool):
            raise DomainError("Choose whether the learning example is eligible.")
        try:
            with self.database.connection() as connection:
                return self.learning.set_example_eligibility(
                    connection,
                    example_id=example_id,
                    eligible=payload["eligible"],
                    reason=payload.get("reason"),
                    actor_id=actor_id,
                )
        except ValueError as exc:
            raise DomainError(str(exc), status=404 if "not found" in str(exc) else 400) from exc

    def _catalogue_for_matching(self, logic_model_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            if not logic_model_id:
                current = connection.execute(
                    """
                    SELECT id FROM logic_models
                    WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                    LIMIT 1
                    """
                ).fetchone()
                if not current:
                    return []
                logic_model_id = current["id"]
            rows = connection.execute(
                """
                SELECT * FROM elements
                WHERE workspace_id = melvan_workspace_id()
                  AND logic_model_id = ? AND status = 'published'
                ORDER BY CASE element_type WHEN 'Output' THEN 0 ELSE 1 END, display_id
                """,
                (logic_model_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _validate_mapping_ids(self, mapping_ids: list[str]) -> None:
        with self.database.connection() as connection:
            current = connection.execute(
                """
                SELECT id FROM logic_models
                WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                LIMIT 1
                """
            ).fetchone()
            placeholders = ",".join("?" for _ in mapping_ids)
            rows = connection.execute(
                f"""
                SELECT id FROM elements
                WHERE id IN ({placeholders})
                  AND workspace_id = melvan_workspace_id()
                  AND logic_model_id = ? AND status = 'published'
                """,
                [*mapping_ids, current["id"]],
            ).fetchall()
        found = {row["id"] for row in rows}
        if found != set(mapping_ids):
            raise DomainError("One or more selected Logic Model elements are not available.")

    def _validate_version_mapping_ids(self, mapping_ids: list[str], logic_model_id: str) -> None:
        if not mapping_ids:
            return
        with self.database.connection() as connection:
            placeholders = ",".join("?" for _ in mapping_ids)
            rows = connection.execute(
                f"SELECT id FROM elements WHERE id IN ({placeholders}) "
                "AND logic_model_id = ? AND status = 'published' "
                "AND workspace_id = melvan_workspace_id()",
                [*mapping_ids, logic_model_id],
            ).fetchall()
        if {row["id"] for row in rows} != set(mapping_ids):
            raise DomainError("One or more mappings do not belong to this Logic Model version.")

    def _review_context(
        self,
        statement_id: str,
        displayed_analysis_id: str,
        reviewer_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM statements
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (statement_id,),
            ).fetchone()
            if not row:
                raise DomainError("Statement not found.", status=404)
            if row["status"] != "awaiting_review":
                raise DomainError("This Statement is no longer awaiting review.", status=409)
            if row["program_irrelevant_at"]:
                raise DomainError("This Statement is no longer available for review.", status=409)
            if not displayed_analysis_id or row["active_analysis_id"] != displayed_analysis_id:
                raise DomainError("This analysis is no longer current. Reload Review and try again.", status=409)
            value = self._statement_from_row(connection, row)
            return value

    def _persist_analysis(
        self,
        connection: DatabaseConnection,
        statement_id: str,
        result: AnalysisResult,
        *,
        reviewer_instruction: str | None = None,
        logic_model_id: str | None = None,
        context_revision_id: str | None = None,
        learning_context: LearningContext | None = None,
    ) -> str:
        result = self._apply_mapping_proposal_threshold(result)
        if context_revision_id is None:
            context_row = connection.execute(
                """
                SELECT current_context_revision_id FROM statements
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (statement_id,),
            ).fetchone()
            context_revision_id = (
                context_row["current_context_revision_id"] if context_row else None
            )
        version = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 FROM analyses
            WHERE statement_id = ? AND workspace_id = melvan_workspace_id()
            """,
            (statement_id,),
        ).fetchone()[0]
        analysis_id = self._new_id("analysis")
        proposal_id = None
        mapping_ids = list(result.match_ids)

        if logic_model_id:
            logic_row = connection.execute(
                """
                SELECT id, version FROM logic_models
                WHERE id = ? AND workspace_id = melvan_workspace_id()
                """,
                (logic_model_id,),
            ).fetchone()
        else:
            logic_row = connection.execute(
                """
                SELECT id, version FROM logic_models
                WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                LIMIT 1
                """
            ).fetchone()
        if not logic_row:
            raise DomainError("Logic Model is not available.", status=503)
        connection.execute(
            """
            INSERT INTO analyses
                (id, statement_id, version, summary, classification,
                 classification_confidence, classification_rationale, outcome_category,
                 result_direction, outcome_change_types, match_status,
                 mapping_confidence, mapping_rationale,
                 proposed_element_id, model_version, prompt_version, logic_model_version,
                 logic_model_id, context_revision_id, reviewer_instruction,
                 learning_policy_version, learning_memory_enabled,
                 learning_snapshot_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                statement_id,
                version,
                result.summary,
                result.classification,
                result.classification_confidence,
                result.classification_rationale,
                result.outcome_category,
                result.result_direction,
                json.dumps(result.outcome_change_types),
                result.match_status,
                result.mapping_confidence,
                result.mapping_rationale,
                proposal_id,
                result.model_version,
                result.prompt_version,
                logic_row["version"],
                logic_row["id"],
                context_revision_id,
                reviewer_instruction,
                learning_context.policy_version if learning_context else None,
                int(learning_context.enabled) if learning_context else 0,
                learning_context.snapshot_id if learning_context else None,
                utc_now(),
            ),
        )
        for rank, element_id in enumerate(mapping_ids, start=1):
            decision = next(
                (item for item in result.mapping_decisions if item.element_id == element_id),
                None,
            )
            connection.execute(
                "INSERT INTO analysis_mappings "
                "(analysis_id, element_id, rank, confidence, rationale) VALUES (?, ?, ?, ?, ?)",
                (
                    analysis_id,
                    element_id,
                    rank,
                    decision.confidence if decision else None,
                    decision.rationale if decision else None,
                ),
            )
        if learning_context:
            self.learning.attach_analysis(connection, analysis_id, learning_context)
        return analysis_id

    @staticmethod
    def _apply_mapping_proposal_threshold(result: AnalysisResult) -> AnalysisResult:
        supported = [
            decision
            for decision in result.mapping_decisions
            if MelvanService._mapping_is_supported(decision.confidence)
        ]
        tentative = [
            decision
            for decision in result.mapping_decisions
            if MelvanService._mapping_is_tentative(decision.confidence)
        ]
        tentative.sort(key=lambda decision: decision.confidence, reverse=True)
        tentative = tentative[:MAX_TENTATIVE_MAPPINGS]
        qualifying = supported + tentative
        if qualifying == result.mapping_decisions and result.proposed_element is None:
            return result
        if qualifying:
            return replace(
                result,
                match_status="existing_match",
                mapping_decisions=qualifying,
                proposed_element=None,
                no_match_rationale="",
            )
        return replace(
            result,
            match_status="no_suitable_match",
            mapping_decisions=[],
            proposed_element=None,
            no_match_rationale=(
                result.no_match_rationale
                or "No existing Logic Model mapping reached the required 25% display threshold."
            ),
        )

    @staticmethod
    def _mapping_confidence_qualifies(confidence: Any) -> bool:
        try:
            return float(confidence) >= MAPPING_DISPLAY_CONFIDENCE_THRESHOLD
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _mapping_is_supported(confidence: Any) -> bool:
        try:
            return float(confidence) >= MAPPING_SUPPORTED_CONFIDENCE_THRESHOLD
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _mapping_is_tentative(confidence: Any) -> bool:
        try:
            value = float(confidence)
            return (
                MAPPING_DISPLAY_CONFIDENCE_THRESHOLD
                <= value
                < MAPPING_SUPPORTED_CONFIDENCE_THRESHOLD
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _apply_authoritative_result(
        result: AnalysisResult,
        corrected: str | None,
        corrected_direction: str | None,
    ) -> AnalysisResult:
        if not corrected and not corrected_direction:
            return result
        classification = corrected or result.classification
        if classification == "Outcome":
            category = result.outcome_category or "C"
            change_types = result.outcome_change_types or ["Capacity"]
        else:
            category = None
            change_types = []
        direction = corrected_direction or result.result_direction if classification == "Outcome" else None
        if classification == "Outcome" and direction is None:
            raise DomainError(
                "Choose a result direction when changing the classification to Outcome."
            )
        return replace(
            result,
            classification=classification,
            classification_confidence=1.0 if corrected else result.classification_confidence,
            classification_rationale=(
                "Applied directly from authoritative Reviewer feedback."
                if corrected
                else result.classification_rationale
            ),
            result_direction=direction,
            outcome_category=category,
            outcome_change_types=change_types,
        )

    def _statement_from_row(
        self, connection: DatabaseConnection, statement_row: DatabaseRow
    ) -> dict[str, Any]:
        statement = dict(statement_row)
        analysis = None
        mappings: list[dict[str, Any]] = []
        proposed_element = None
        if statement["active_analysis_id"]:
            analysis_row = connection.execute(
                "SELECT * FROM analyses WHERE id = ?", (statement["active_analysis_id"],)
            ).fetchone()
            if analysis_row:
                analysis = dict(analysis_row)
                analysis["outcome_change_types"] = json.loads(analysis["outcome_change_types"])
                mapping_rows = connection.execute(
                    """
                    SELECT e.*, am.rank, am.confidence, am.rationale FROM analysis_mappings am
                    JOIN elements e ON e.id = am.element_id
                    WHERE am.analysis_id = ?
                      AND e.logic_model_id IS NOT NULL
                    ORDER BY am.rank
                    """,
                    (analysis["id"],),
                ).fetchall()
                mappings = [dict(row) for row in mapping_rows]
                for mapping in mappings:
                    mapping["confidence_tier"] = (
                        "low"
                        if self._mapping_is_tentative(mapping.get("confidence"))
                        else "supported"
                    )
                if statement["status"] == "awaiting_review":
                    mappings = [
                        mapping
                        for mapping in mappings
                        if self._mapping_confidence_qualifies(mapping.get("confidence"))
                    ]
        statement["analysis"] = analysis
        statement["mappings"] = mappings
        statement["proposed_element"] = proposed_element
        context_row = connection.execute(
            "SELECT * FROM statement_context_revisions WHERE id = ?",
            (statement.get("current_context_revision_id"),),
        ).fetchone()
        statement["context"] = (
            {
                "id": context_row["id"],
                "version": context_row["version"],
                "text": context_row["context_text"],
                "created_by": context_row["created_by"],
                "created_at": context_row["created_at"],
            }
            if context_row
            else {"id": None, "version": 0, "text": "", "created_at": None}
        )
        statement["review_feedback"] = []
        feedback_rows = connection.execute(
            """
            SELECT analysis_id, corrected_classification, corrected_result_direction,
                   comments, mapping_ids, created_element_id, created_at
            FROM review_feedback
            WHERE statement_id = ?
            ORDER BY created_at
            """,
            (statement["id"],),
        ).fetchall()
        statement["review_feedback"] = [dict(item) for item in feedback_rows]
        attachment_row = connection.execute(
            """
            SELECT id, source, media_type, byte_size, width, height, checksum,
                   provider, provider_version, processing_ms, processing_result,
                   created_at
            FROM statement_attachments
            WHERE statement_id = ? AND workspace_id = melvan_workspace_id()
            """,
            (statement["id"],),
        ).fetchone()
        statement["attachment"] = (
            {
                **dict(attachment_row),
                "content_url": f"/api/attachments/{attachment_row['id']}",
            }
            if attachment_row
            else None
        )
        if statement["status"] == "evidence":
            evidence_row = connection.execute(
                "SELECT * FROM evidence WHERE statement_id = ?", (statement["id"],)
            ).fetchone()
            statement["evidence"] = dict(evidence_row) if evidence_row else None
            self._apply_current_evidence_assessment(connection, statement)
        return statement

    def _current_logic_model_id(self) -> str:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT id FROM logic_models
                WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
                LIMIT 1
                """
            ).fetchone()
        if not row:
            raise DomainError("Logic Model is not available.", status=503)
        return str(row["id"])

    @staticmethod
    def _validate_statement_context(value: Any) -> str:
        context = str(value or "")
        if len(context) > 10_000:
            raise DomainError("Context must be 10,000 characters or fewer.")
        return context

    @staticmethod
    def _decode_media(encoded: str, maximum: int, label: str) -> bytes:
        value = str(encoded or "")
        if not value:
            raise DomainError(f"No {label} was received.")
        if len(value) > ((maximum * 4) // 3) + 16:
            raise DomainError(f"The {label} is too large.", status=413)
        try:
            content = b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise DomainError(f"The {label} data is invalid.") from exc
        if not content:
            raise DomainError(f"No {label} was received.")
        if len(content) > maximum:
            raise DomainError(f"The {label} is too large.", status=413)
        return content

    @staticmethod
    def _validate_capture(value: dict[str, Any] | None) -> dict[str, Any]:
        capture = dict(value or {})
        method = str(capture.get("method") or "text").lower()
        if method not in {"text", "voice", "photo"}:
            raise DomainError("Choose Text, Voice, or Photo capture.")
        candidate = capture.get("candidate_text")
        if candidate is not None and len(str(candidate)) > 10_000:
            raise DomainError("The capture candidate must be 10,000 characters or fewer.")
        if method in {"voice", "photo"}:
            if not str(capture.get("provider") or "").strip():
                raise DomainError("Capture processing provenance is missing.")
            if not str(capture.get("provider_version") or "").strip():
                raise DomainError("Capture provider version is missing.")
        if method == "photo" and not str(capture.get("stage_id") or ""):
            raise DomainError("The staged photo has expired. Choose or take it again.")
        processing_ms = capture.get("processing_ms")
        if processing_ms is not None:
            try:
                processing_ms = max(0, int(processing_ms))
            except (TypeError, ValueError) as exc:
                raise DomainError("Capture processing time is invalid.") from exc
        return {
            "method": method,
            "stage_id": capture.get("stage_id"),
            "candidate_text": None if candidate is None else str(candidate),
            "edited": bool(capture.get("edited")),
            "provider": capture.get("provider"),
            "provider_version": capture.get("provider_version"),
            "processing_ms": processing_ms,
            "outcome": str(capture.get("outcome") or "success"),
        }

    def _staged_photo(
        self, stage_id: str, reporter_id: str, session_fingerprint: str
    ) -> DatabaseRow:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM media_staging
                WHERE id = ? AND reporter_id = ? AND session_fingerprint = ?
                  AND expires_at > ? AND workspace_id = melvan_workspace_id()
                """,
                (stage_id, reporter_id, session_fingerprint, utc_now()),
            ).fetchone()
        if not row:
            raise DomainError(
                "The staged photo has expired. Choose or take it again.",
                status=410,
            )
        return row

    def record_role_action(
        self, user_id: str, active_role: str, method: str, path: str
    ) -> None:
        with self.database.connection() as connection:
            self._audit(
                connection,
                "active_role_selected",
                "user",
                user_id,
                {"active_role": active_role, "method": method, "path": path},
            )

    def _backfill_evidence_assessments(self) -> None:
        now = utc_now()
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT e.id AS evidence_id, e.analysis_id, a.logic_model_id,
                       a.mapping_rationale, a.classification, a.result_direction,
                       e.accepted_at
                FROM evidence e
                JOIN analyses a ON a.id = e.analysis_id
                WHERE a.logic_model_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM evidence_assessments ea
                    WHERE ea.evidence_id = e.id AND ea.logic_model_id = a.logic_model_id
                  )
                """
            ).fetchall()
            for row in rows:
                mapping_rows = connection.execute(
                    """
                    SELECT am.element_id, am.confidence, am.rationale
                    FROM analysis_mappings am
                    JOIN elements el ON el.id = am.element_id
                    WHERE am.analysis_id = ? AND el.logic_model_id = ?
                    ORDER BY am.rank
                    """,
                    (row["analysis_id"], row["logic_model_id"]),
                ).fetchall()
                mapping_ids = [item["element_id"] for item in mapping_rows]
                assessment_id = self._new_id("assessment")
                connection.execute(
                    """
                    INSERT INTO evidence_assessments
                        (id, evidence_id, logic_model_id, status, suggested_mapping_ids,
                         mapping_rationale, suggested_classification,
                         confirmed_classification, suggested_result_direction,
                         confirmed_result_direction, accepted_analysis_id,
                         source_logic_model_id, reviewer_id, created_at, decided_at)
                    VALUES (?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, ?, ?,
                            'bootstrap-reviewer', ?, ?)
                    """,
                    (
                        assessment_id, row["evidence_id"], row["logic_model_id"],
                        json.dumps(mapping_ids), row["mapping_rationale"],
                        row["classification"], row["classification"],
                        row["result_direction"], row["result_direction"], row["analysis_id"],
                        row["logic_model_id"],
                        row["accepted_at"] or now, row["accepted_at"] or now,
                    ),
                )
                for rank, mapping in enumerate(mapping_rows, start=1):
                    connection.execute(
                        "INSERT INTO evidence_assessment_mappings "
                        "(assessment_id, element_id, rank, confidence, rationale) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            assessment_id,
                            mapping["element_id"],
                            rank,
                            mapping["confidence"],
                            mapping["rationale"] or "Approved by the bootstrap Reviewer.",
                        ),
                    )

    def _evidence_assessment_from_row(
        self, connection: DatabaseConnection, row: DatabaseRow
    ) -> dict[str, Any]:
        value = dict(row)
        suggested_ids = json.loads(value["suggested_mapping_ids"])
        value["suggested_mappings"] = self._element_records(connection, suggested_ids)
        details = {
            item["element_id"]: item
            for item in json.loads(value.get("suggested_mapping_details") or "[]")
        }
        for mapping in value["suggested_mappings"]:
            detail = details.get(mapping["id"], {})
            mapping["confidence"] = detail.get("confidence")
            mapping["rationale"] = detail.get("rationale") or ""
            mapping["confidence_tier"] = (
                "low"
                if self._mapping_is_tentative(mapping.get("confidence"))
                else "supported"
            )
        if value["status"] == "pending":
            value["suggested_mappings"] = [
                mapping
                for mapping in value["suggested_mappings"]
                if self._mapping_confidence_qualifies(mapping.get("confidence"))
            ]
        value.pop("suggested_mapping_ids", None)
        value.pop("suggested_mapping_details", None)
        value["change_set"] = json.loads(value.pop("change_set_json", "[]") or "[]")
        value["change_summary"] = [item["explanation"] for item in value["change_set"]]
        value["suggestion_identity"] = f'{value["id"]}:{value["suggestion_version"]}'
        if value.get("source_assessment_id"):
            accepted_rows = connection.execute(
                """
                SELECT el.*, eam.rank, eam.confidence, eam.rationale
                FROM evidence_assessment_mappings eam
                JOIN elements el ON el.id = eam.element_id
                WHERE eam.assessment_id = ? ORDER BY eam.rank
                """,
                (value["source_assessment_id"],),
            ).fetchall()
            source_assessment = connection.execute(
                "SELECT confirmed_classification, confirmed_result_direction, status "
                "FROM evidence_assessments WHERE id = ?",
                (value["source_assessment_id"],),
            ).fetchone()
            value["accepted_classification"] = (
                source_assessment["confirmed_classification"]
                if source_assessment and source_assessment["confirmed_classification"]
                else value["classification"]
            )
            value["accepted_result_direction"] = (
                source_assessment["confirmed_result_direction"]
                if source_assessment and value["accepted_classification"] == "Outcome"
                else value["result_direction"]
                if value["accepted_classification"] == "Outcome"
                else None
            )
        else:
            accepted_rows = connection.execute(
                """
                SELECT el.*, am.rank, am.confidence, am.rationale FROM analysis_mappings am
                JOIN elements el ON el.id = am.element_id
                WHERE am.analysis_id = ? ORDER BY am.rank
                """,
                (value["analysis_id"],),
            ).fetchall()
            value["accepted_classification"] = value["classification"]
            value["accepted_result_direction"] = (
                value["result_direction"] if value["classification"] == "Outcome" else None
            )
        value["accepted_mappings"] = [dict(item) for item in accepted_rows]
        target_rows = connection.execute(
            """
            SELECT * FROM elements WHERE logic_model_id = ? AND status = 'published'
            ORDER BY CASE element_type WHEN 'Output' THEN 0 ELSE 1 END,
                     CASE level WHEN 'output' THEN 0 WHEN 'enabling_output' THEN 1
                     WHEN 'short_term_outcome' THEN 2 WHEN 'medium_term_outcome' THEN 3
                     WHEN 'long_term_outcome' THEN 4 WHEN 'ultimate_goal' THEN 5 ELSE 6 END,
                     display_id
            """,
            (value["logic_model_id"],),
        ).fetchall()
        value["available_mappings"] = [dict(item) for item in target_rows]
        target = connection.execute(
            "SELECT id, title, version, sequence FROM logic_models WHERE id = ?",
            (value["logic_model_id"],),
        ).fetchone()
        accepted_model = connection.execute(
            "SELECT id, title, version, sequence FROM logic_models WHERE id = ?",
            (value.get("source_logic_model_id") or value["accepted_logic_model_id"],),
        ).fetchone()
        value["target_logic_model"] = dict(target) if target else None
        value["accepted_logic_model"] = dict(accepted_model) if accepted_model else None
        history = connection.execute(
            """
            SELECT ea.id, ea.status, ea.logic_model_id, ea.reviewer_id,
                   ea.reviewer_feedback, ea.decided_at, lm.version, lm.sequence
            FROM evidence_assessments ea
            JOIN logic_models lm ON lm.id = ea.logic_model_id
            WHERE ea.evidence_id = ? AND ea.id <> ?
            ORDER BY lm.sequence
            """,
            (value["evidence_id"], value["id"]),
        ).fetchall()
        value["prior_assessments"] = [dict(item) for item in history]
        return value

    @staticmethod
    def _element_records(
        connection: DatabaseConnection, element_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not element_ids:
            return []
        placeholders = ",".join("?" for _ in element_ids)
        rows = connection.execute(
            f"SELECT * FROM elements WHERE id IN ({placeholders})", element_ids
        ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[element_id] for element_id in element_ids if element_id in by_id]

    @staticmethod
    def _selectable_elements(connection: DatabaseConnection) -> list[dict[str, Any]]:
        current = connection.execute(
            """
            SELECT id FROM logic_models
            WHERE is_current = 1 AND workspace_id = melvan_workspace_id()
            LIMIT 1
            """
        ).fetchone()
        if not current:
            return []
        rows = connection.execute(
            """
            SELECT * FROM elements
            WHERE workspace_id = melvan_workspace_id()
              AND logic_model_id = ? AND status = 'published'
            ORDER BY CASE element_type WHEN 'Output' THEN 0 ELSE 1 END,
                     CASE level
                        WHEN 'output' THEN 0 WHEN 'enabling_output' THEN 1
                        WHEN 'short_term_outcome' THEN 2 WHEN 'medium_term_outcome' THEN 3
                        WHEN 'long_term_outcome' THEN 4 WHEN 'ultimate_goal' THEN 5 ELSE 6 END,
                     display_id
            """,
            (current["id"],),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _audit(
        connection: DatabaseConnection,
        event_type: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events
                (id, event_type, entity_type, entity_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                MelvanService._new_id("audit"),
                event_type,
                entity_type,
                entity_id,
                json.dumps(details, ensure_ascii=True),
                utc_now(),
            ),
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"
