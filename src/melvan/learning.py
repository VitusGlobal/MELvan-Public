"""Reviewer-informed learning ledger and deterministic precedent retrieval."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .db import DatabaseAdapter, DatabaseConnection, DatabaseRow


LEARNING_POLICY_VERSION = "reviewer-memory-v1"
LANGUAGE_ISSUE_TYPES = {
    "inaccurate_summary",
    "unsupported_or_invented_detail",
    "overstated_causal_claim",
    "inappropriate_terminology_or_tone",
    "unclear_or_unnecessarily_technical_language",
    "other_language_issue",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    }


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class LearningContext:
    snapshot_id: str
    policy_version: str
    enabled: bool
    examples: list[dict[str, Any]]
    ranked: list[dict[str, Any]]
    error: str | None = None


class LearningMemory:
    """Repository boundary for append-only learning records."""

    def __init__(self, database: DatabaseAdapter):
        self.database = database

    @staticmethod
    def active_policy(connection: DatabaseConnection) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM learning_policies WHERE is_active = 1 "
            "AND workspace_id = melvan_workspace_id() LIMIT 1"
        ).fetchone()
        if not row:
            return {
                "version": LEARNING_POLICY_VERSION,
                "enabled": False,
                "config": {"limit": 8, "rolling_window": 3, "minimum_events": 20},
            }
        return {
            "version": row["version"],
            "enabled": bool(row["enabled"]),
            "config": json.loads(row["config_json"]),
        }

    def publish_policy(
        self,
        connection: DatabaseConnection,
        *,
        version: str,
        enabled: bool,
        config: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        normalized = str(version or "").strip()
        if not normalized:
            raise ValueError("Enter a learning-policy version.")
        if connection.execute(
            "SELECT 1 FROM learning_policies WHERE version = ? "
            "AND workspace_id = melvan_workspace_id()", (normalized,)
        ).fetchone():
            raise ValueError("That learning-policy version already exists.")
        connection.execute(
            "UPDATE learning_policies SET is_active = 0 WHERE is_active = 1 "
            "AND workspace_id = melvan_workspace_id()"
        )
        connection.execute(
            """
            INSERT INTO learning_policies
                (version, enabled, is_active, config_json, created_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (normalized, int(enabled), json.dumps(config), _now()),
        )
        self._audit(
            connection,
            "learning_policy_published",
            "learning_policy",
            normalized,
            {"actor_id": actor_id, "enabled": enabled, "config": config},
        )
        return self.active_policy(connection)

    def activate_policy(
        self, connection: DatabaseConnection, *, version: str, actor_id: str
    ) -> dict[str, Any]:
        if not connection.execute(
            "SELECT 1 FROM learning_policies WHERE version = ? "
            "AND workspace_id = melvan_workspace_id()", (version,)
        ).fetchone():
            raise ValueError("Learning policy not found.")
        previous = self.active_policy(connection)["version"]
        connection.execute(
            "UPDATE learning_policies SET is_active = 0 WHERE is_active = 1 "
            "AND workspace_id = melvan_workspace_id()"
        )
        connection.execute(
            "UPDATE learning_policies SET is_active = 1 WHERE version = ? "
            "AND workspace_id = melvan_workspace_id()", (version,)
        )
        self._audit(
            connection,
            "learning_policy_activated",
            "learning_policy",
            version,
            {"actor_id": actor_id, "previous_version": previous},
        )
        return self.active_policy(connection)

    def set_example_eligibility(
        self,
        connection: DatabaseConnection,
        *,
        example_id: str,
        eligible: bool,
        reason: str | None,
        actor_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM learning_examples WHERE id = ? "
            "AND workspace_id = melvan_workspace_id()", (example_id,)
        ).fetchone()
        if not row:
            raise ValueError("Learning example not found.")
        normalized_reason = str(reason or "").strip() or None
        if not eligible and not normalized_reason:
            raise ValueError("Give a reason for excluding the learning example.")
        status = "eligible" if eligible else "excluded"
        connection.execute(
            """
            UPDATE learning_examples
            SET eligibility_status = ?, exclusion_reason = ?
            WHERE id = ? AND workspace_id = melvan_workspace_id()
            """,
            (status, None if eligible else normalized_reason, example_id),
        )
        self._audit(
            connection,
            "learning_example_eligibility_changed",
            "learning_example",
            example_id,
            {
                "actor_id": actor_id,
                "eligibility_status": status,
                "reason": normalized_reason,
            },
        )
        result = dict(row)
        result["eligibility_status"] = status
        result["exclusion_reason"] = None if eligible else normalized_reason
        return result

    @staticmethod
    def validate_language_feedback(issue_types: Any, comments: str | None) -> list[str]:
        values = list(dict.fromkeys(str(value) for value in (issue_types or [])))
        invalid = set(values) - LANGUAGE_ISSUE_TYPES
        if invalid:
            raise ValueError("Choose valid Language feedback options.")
        if "other_language_issue" in values and not str(comments or "").strip():
            raise ValueError("Describe the other language issue in the comments field.")
        return values

    def prepare_context(self, statement_id: str, statement: str, logic_model_id: str) -> LearningContext:
        snapshot_id = _id("learning-snapshot")
        now = _now()
        try:
            with self.database.connection() as connection:
                policy = connection.execute(
                    "SELECT * FROM learning_policies WHERE is_active = 1 "
                    "AND workspace_id = melvan_workspace_id() LIMIT 1"
                ).fetchone()
                if not policy:
                    raise RuntimeError("No active learning policy is configured.")
                config = json.loads(policy["config_json"])
                enabled = bool(policy["enabled"])
                current_nodes = {
                    row["stable_id"] or row["id"]
                    for row in connection.execute(
                        "SELECT id, stable_id FROM elements WHERE logic_model_id = ? "
                        "AND workspace_id = melvan_workspace_id()",
                        (logic_model_id,),
                    ).fetchall()
                }
                rows = connection.execute(
                    """
                    SELECT * FROM learning_examples
                    WHERE eligibility_status = 'eligible'
                      AND workspace_id = melvan_workspace_id()
                    ORDER BY created_at, id
                    """
                ).fetchall()
                eligible = [
                    row for row in rows
                    if row["dimension"] != "mapping"
                    or not row["stable_element_id"]
                    or row["stable_element_id"] in current_nodes
                ]
                ranked: list[dict[str, Any]] = []
                if enabled:
                    for row in eligible:
                        similarity = _similarity(statement, row["source_text"])
                        ranking_score = round(
                            similarity * 0.72 + float(row["learning_priority"]) * 0.28,
                            6,
                        )
                        ranked.append(
                            {
                                "row": row,
                                "similarity": similarity,
                                "ranking_score": ranking_score,
                            }
                        )
                    ranked.sort(
                        key=lambda item: (
                            -item["ranking_score"],
                            -float(item["row"]["surprise_score"]),
                            item["row"]["id"],
                        )
                    )
                    limit = int(config.get("limit", 8))
                    selected: list[dict[str, Any]] = []
                    source_counts: dict[str, int] = {}
                    for item in ranked:
                        source = item["row"]["statement_id"]
                        if source_counts.get(source, 0) >= 2:
                            continue
                        selected.append(item)
                        source_counts[source] = source_counts.get(source, 0) + 1
                        if len(selected) >= limit:
                            break
                else:
                    selected = []
                example_ids = [item["row"]["id"] for item in selected]
                connection.execute(
                    """
                    INSERT INTO learning_snapshots
                        (id, statement_id, policy_version, memory_enabled,
                         eligible_example_ids_json, retrieved_example_ids_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        statement_id,
                        policy["version"],
                        int(enabled),
                        json.dumps([row["id"] for row in eligible]),
                        json.dumps(example_ids),
                        now,
                    ),
                )
                examples = [self._prompt_example(item["row"]) for item in selected]
                return LearningContext(
                    snapshot_id,
                    policy["version"],
                    enabled,
                    examples,
                    [
                        {
                            "id": item["row"]["id"],
                            "similarity": item["similarity"],
                            "ranking_score": item["ranking_score"],
                        }
                        for item in selected
                    ],
                )
        except Exception as exc:
            with self.database.connection() as connection:
                policy = connection.execute(
                    "SELECT version FROM learning_policies WHERE is_active = 1 "
                    "AND workspace_id = melvan_workspace_id() LIMIT 1"
                ).fetchone()
                version = policy["version"] if policy else LEARNING_POLICY_VERSION
                connection.execute(
                    """
                    INSERT INTO learning_snapshots
                        (id, statement_id, policy_version, memory_enabled,
                         eligible_example_ids_json, retrieved_example_ids_json,
                         retrieval_error, created_at)
                    VALUES (?, ?, ?, 0, '[]', '[]', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        statement_id = excluded.statement_id,
                        policy_version = excluded.policy_version,
                        memory_enabled = excluded.memory_enabled,
                        eligible_example_ids_json = excluded.eligible_example_ids_json,
                        retrieved_example_ids_json = excluded.retrieved_example_ids_json,
                        retrieval_error = excluded.retrieval_error,
                        created_at = excluded.created_at
                    """,
                    (snapshot_id, statement_id, version, str(exc), now),
                )
                self._audit(
                    connection,
                    "learning_retrieval_failed",
                    "learning_snapshot",
                    snapshot_id,
                    {"error": str(exc), "fallback": "no_memory"},
                )
            return LearningContext(snapshot_id, version, False, [], [], str(exc))

    @staticmethod
    def _prompt_example(row: DatabaseRow) -> dict[str, Any]:
        return {
            "example_id": row["id"],
            "source_statement": row["source_text"],
            "dimension": row["dimension"],
            "review_signal": row["signal_type"],
            "model_proposed": json.loads(row["original_value_json"] or "null"),
            "reviewer_verified_truth": json.loads(row["final_value_json"] or "null"),
            "negative_counterexample": row["signal_type"] in {"corrected", "unsupported"},
        }

    def attach_analysis(
        self, connection: DatabaseConnection, analysis_id: str, context: LearningContext
    ) -> None:
        connection.execute(
            "UPDATE learning_snapshots SET analysis_id = ? WHERE id = ?",
            (analysis_id, context.snapshot_id),
        )
        for rank, item in enumerate(context.ranked, start=1):
            connection.execute(
                """
                INSERT INTO analysis_learning_examples
                    (analysis_id, learning_example_id, rank, similarity,
                     ranking_score, inclusion_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    item["id"],
                    rank,
                    item["similarity"],
                    item["ranking_score"],
                    "Eligible under the active policy and selected by deterministic relevance, surprise, and diversity ranking.",
                ),
            )
        self._audit(
            connection,
            "learning_examples_retrieved",
            "analysis",
            analysis_id,
            {
                "snapshot_id": context.snapshot_id,
                "policy_version": context.policy_version,
                "example_ids": [item["id"] for item in context.ranked],
                "memory_enabled": context.enabled,
            },
        )

    def record_statement_review(
        self,
        connection: DatabaseConnection,
        *,
        event_id: str,
        statement_id: str,
        source_analysis_id: str,
        resulting_analysis_id: str | None,
        reviewer_id: str,
        language_issue_types: list[str],
        comments: str | None,
    ) -> None:
        source = connection.execute(
            "SELECT * FROM analyses WHERE id = ?", (source_analysis_id,)
        ).fetchone()
        final = connection.execute(
            "SELECT * FROM analyses WHERE id = ?",
            (resulting_analysis_id or source_analysis_id,),
        ).fetchone()
        statement = connection.execute(
            "SELECT original_text FROM statements WHERE id = ?", (statement_id,)
        ).fetchone()
        if not source or not final or not statement:
            return
        self._add_comparison_example(
            connection,
            event_id=event_id,
            statement_id=statement_id,
            source_analysis_id=source_analysis_id,
            resulting_analysis_id=resulting_analysis_id,
            reviewer_id=reviewer_id,
            source_text=statement["original_text"],
            logic_model_id=final["logic_model_id"],
            dimension="classification",
            example_key="classification",
            original=source["classification"],
            final=final["classification"],
            confidence=float(source["classification_confidence"]),
        )
        if source["classification"] == "Outcome" or final["classification"] == "Outcome":
            self._add_comparison_example(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=source_analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                source_text=statement["original_text"],
                logic_model_id=final["logic_model_id"],
                dimension="direction",
                example_key="direction",
                original=source["result_direction"],
                final=final["result_direction"],
                confidence=float(source["classification_confidence"]),
            )
            self._add_comparison_example(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=source_analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                source_text=statement["original_text"],
                logic_model_id=final["logic_model_id"],
                dimension="outcome_category",
                example_key="outcome_category",
                original=source["outcome_category"],
                final=final["outcome_category"],
                confidence=float(source["classification_confidence"]),
            )
            self._add_comparison_example(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=source_analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                source_text=statement["original_text"],
                logic_model_id=final["logic_model_id"],
                dimension="outcome_change_types",
                example_key="outcome_change_types",
                original=json.loads(source["outcome_change_types"] or "[]"),
                final=json.loads(final["outcome_change_types"] or "[]"),
                confidence=float(source["classification_confidence"]),
            )
        source_mappings = self._mappings(connection, source_analysis_id)
        final_mappings = self._mappings(connection, final["id"])
        all_stable_ids = sorted(set(source_mappings) | set(final_mappings))
        if not all_stable_ids:
            self._add_comparison_example(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=source_analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                source_text=statement["original_text"],
                logic_model_id=final["logic_model_id"],
                dimension="mapping",
                example_key="mapping:no_match",
                original="no_suitable_match",
                final="no_suitable_match",
                confidence=float(source["mapping_confidence"]),
            )
        for stable_id in all_stable_ids:
            before, after = source_mappings.get(stable_id), final_mappings.get(stable_id)
            if before and after:
                signal = "confirmed"
                confidence = before["confidence"]
            elif before:
                signal = "unsupported"
                confidence = before["confidence"]
            else:
                signal = "omitted"
                confidence = None
            self._insert_example(
                connection,
                event_id=event_id,
                statement_id=statement_id,
                source_analysis_id=source_analysis_id,
                resulting_analysis_id=resulting_analysis_id,
                reviewer_id=reviewer_id,
                source_text=statement["original_text"],
                logic_model_id=final["logic_model_id"],
                stable_element_id=stable_id,
                dimension="mapping",
                example_key=f"mapping:{stable_id}",
                signal_type=signal,
                original=before,
                final=after,
                original_confidence=confidence,
            )
        status = "positive" if not language_issue_types else "paired"
        connection.execute(
            """
            INSERT OR IGNORE INTO language_feedback
                (id, review_event_id, statement_id, issue_types_json, comments,
                 original_summary, final_summary, example_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _id("language"), event_id, statement_id,
                json.dumps(language_issue_types), comments, source["summary"],
                final["summary"], status, _now(),
            ),
        )
        self._insert_example(
            connection,
            event_id=event_id,
            statement_id=statement_id,
            source_analysis_id=source_analysis_id,
            resulting_analysis_id=resulting_analysis_id,
            reviewer_id=reviewer_id,
            source_text=statement["original_text"],
            logic_model_id=final["logic_model_id"],
            stable_element_id=None,
            dimension="language",
            example_key="language",
            signal_type="language_defect" if language_issue_types else "confirmed",
            original={"summary": source["summary"], "issues": language_issue_types},
            final={"summary": final["summary"], "preferred_wording": comments},
            original_confidence=None,
        )

    def record_evidence_review(
        self,
        connection: DatabaseConnection,
        *,
        event_id: str,
        assessment: DatabaseRow,
        reviewer_id: str,
        confirmed_classification: str,
        confirmed_result_direction: str | None,
        suggested_mapping_ids: list[str],
        confirmed_mapping_ids: list[str],
        language_issue_types: list[str],
        comments: str | None,
    ) -> None:
        source_analysis = connection.execute(
            "SELECT * FROM analyses WHERE id = ?", (assessment["analysis_id"],)
        ).fetchone()
        if not source_analysis:
            return
        statement_text = assessment["original_text"]
        suggested_classification = (
            assessment["suggested_classification"] or assessment["classification"]
        )
        self._add_comparison_example(
            connection,
            event_id=event_id,
            statement_id=assessment["statement_id"],
            source_analysis_id=assessment["analysis_id"],
            resulting_analysis_id=None,
            reviewer_id=reviewer_id,
            source_text=statement_text,
            logic_model_id=assessment["logic_model_id"],
            dimension="classification",
            example_key="classification",
            original=suggested_classification,
            final=confirmed_classification,
            confidence=float(source_analysis["classification_confidence"]),
            evidence_assessment_id=assessment["id"],
        )
        suggested_direction = (
            assessment["suggested_result_direction"] or assessment["result_direction"]
        )
        if suggested_classification == "Outcome" or confirmed_classification == "Outcome":
            self._add_comparison_example(
                connection,
                event_id=event_id,
                statement_id=assessment["statement_id"],
                source_analysis_id=assessment["analysis_id"],
                resulting_analysis_id=None,
                reviewer_id=reviewer_id,
                source_text=statement_text,
                logic_model_id=assessment["logic_model_id"],
                dimension="direction",
                example_key="direction",
                original=suggested_direction,
                final=confirmed_result_direction,
                confidence=float(source_analysis["classification_confidence"]),
                evidence_assessment_id=assessment["id"],
            )
        details = {
            item["element_id"]: item
            for item in json.loads(assessment["suggested_mapping_details"] or "[]")
        }
        all_ids = sorted(set(suggested_mapping_ids) | set(confirmed_mapping_ids))
        for element_id in all_ids:
            element = connection.execute(
                "SELECT COALESCE(stable_id, id) AS stable_id, label FROM elements WHERE id = ?",
                (element_id,),
            ).fetchone()
            if not element:
                continue
            before = element_id in suggested_mapping_ids
            after = element_id in confirmed_mapping_ids
            signal = "confirmed" if before and after else "unsupported" if before else "omitted"
            detail = details.get(element_id, {})
            original_value = (
                {"stable_element_id": element["stable_id"], "label": element["label"]}
                if before else None
            )
            final_value = (
                {"stable_element_id": element["stable_id"], "label": element["label"]}
                if after else None
            )
            self._insert_example(
                connection,
                event_id=event_id,
                statement_id=assessment["statement_id"],
                source_analysis_id=assessment["analysis_id"],
                resulting_analysis_id=None,
                reviewer_id=reviewer_id,
                source_text=statement_text,
                logic_model_id=assessment["logic_model_id"],
                stable_element_id=element["stable_id"],
                dimension="mapping",
                example_key=f"mapping:{element['stable_id']}",
                signal_type=signal,
                original=original_value,
                final=final_value,
                original_confidence=detail.get("confidence") if before else None,
                evidence_assessment_id=assessment["id"],
            )
        if language_issue_types:
            status = "paired" if comments else "negative_only"
            connection.execute(
                """
                INSERT OR IGNORE INTO language_feedback
                    (id, review_event_id, statement_id, issue_types_json, comments,
                     original_summary, final_summary, example_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("language"), event_id, assessment["statement_id"],
                    json.dumps(language_issue_types), comments,
                    source_analysis["summary"], comments if comments else None,
                    status, _now(),
                ),
            )
            self._insert_example(
                connection,
                event_id=event_id,
                statement_id=assessment["statement_id"],
                source_analysis_id=assessment["analysis_id"],
                resulting_analysis_id=None,
                reviewer_id=reviewer_id,
                source_text=statement_text,
                logic_model_id=assessment["logic_model_id"],
                stable_element_id=None,
                dimension="language",
                example_key="language",
                signal_type="language_defect",
                original={"summary": source_analysis["summary"], "issues": language_issue_types},
                final={"preferred_wording": comments} if comments else None,
                original_confidence=None,
                evidence_assessment_id=assessment["id"],
            )

    @staticmethod
    def _mappings(connection: DatabaseConnection, analysis_id: str) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT COALESCE(e.stable_id, e.id) AS stable_id, e.label,
                   am.confidence, am.rationale
            FROM analysis_mappings am JOIN elements e ON e.id = am.element_id
            WHERE am.analysis_id = ?
            """,
            (analysis_id,),
        ).fetchall()
        return {
            row["stable_id"]: {
                "stable_element_id": row["stable_id"],
                "label": row["label"],
                "confidence": row["confidence"],
                "rationale": row["rationale"],
            }
            for row in rows
        }

    def _add_comparison_example(self, connection: DatabaseConnection, **values: Any) -> None:
        original, final = values.pop("original"), values.pop("final")
        confidence = values.pop("confidence")
        signal = "confirmed" if original == final else "corrected"
        self._insert_example(
            connection,
            signal_type=signal,
            original=original,
            final=final,
            original_confidence=confidence,
            stable_element_id=None,
            **values,
        )

    def _insert_example(
        self,
        connection: DatabaseConnection,
        *,
        event_id: str,
        statement_id: str,
        source_analysis_id: str,
        resulting_analysis_id: str | None,
        reviewer_id: str,
        source_text: str,
        logic_model_id: str | None,
        stable_element_id: str | None,
        dimension: str,
        example_key: str,
        signal_type: str,
        original: Any,
        final: Any,
        original_confidence: float | None,
        evidence_assessment_id: str | None = None,
    ) -> None:
        confidence = max(0.0, min(1.0, float(original_confidence or 0.0)))
        if signal_type == "confirmed":
            surprise = 1.0 - confidence if original_confidence is not None else 0.2
        elif signal_type in {"corrected", "unsupported"}:
            surprise = confidence
        else:
            surprise = 1.0
        signal_boost = {
            "omitted": 1.0,
            "language_defect": 0.95,
            "unsupported": 0.9,
            "corrected": 0.9,
            "confirmed": 0.35,
        }.get(signal_type, 0.5)
        priority = round(signal_boost * 0.65 + surprise * 0.35, 6)
        example_id = _id("learning")
        active_policy = self.active_policy(connection)
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO learning_examples
                (id, review_event_id, statement_id, source_analysis_id,
                 resulting_analysis_id, evidence_assessment_id,
                 example_key, dimension, signal_type,
                 original_value_json, final_value_json, original_confidence,
                 truth_weight, surprise_score, learning_priority, source_text,
                 logic_model_id, stable_element_id, reviewer_id, policy_version,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                example_id, event_id, statement_id, source_analysis_id,
                resulting_analysis_id, evidence_assessment_id,
                example_key, dimension, signal_type,
                json.dumps(original, ensure_ascii=True),
                json.dumps(final, ensure_ascii=True),
                original_confidence, surprise, priority, source_text,
                logic_model_id, stable_element_id, reviewer_id,
                active_policy["version"], _now(),
            ),
        )
        if inserted.rowcount == 0:
            return
        self._audit(
            connection,
            "learning_example_created",
            "learning_example",
            example_id,
            {
                "review_event_id": event_id,
                "dimension": dimension,
                "signal_type": signal_type,
                "surprise_score": surprise,
                "learning_priority": priority,
            },
        )

    @staticmethod
    def session_diagnostics(connection: DatabaseConnection, session_id: str) -> dict[str, int]:
        rows = connection.execute(
            """
            SELECT le.signal_type, le.original_confidence
            FROM learning_examples le
            JOIN review_events re ON re.id = le.review_event_id
            WHERE re.session_id = ?
              AND le.workspace_id = melvan_workspace_id()
              AND re.workspace_id = melvan_workspace_id()
            """,
            (session_id,),
        ).fetchall()
        tentative_rows = [
            row
            for row in rows
            if row["original_confidence"] is not None
            and 0.25 <= row["original_confidence"] < 0.50
            and row["signal_type"] in {"confirmed", "unsupported"}
        ]
        return {
            "omissions": sum(row["signal_type"] == "omitted" for row in rows),
            "unsupported_mappings": sum(row["signal_type"] == "unsupported" for row in rows),
            "high_confidence_errors": sum(
                row["signal_type"] in {"corrected", "unsupported"}
                and row["original_confidence"] is not None
                and row["original_confidence"] >= 0.8
                for row in rows
            ),
            "tentative_mappings_presented": len(tentative_rows),
            "tentative_mappings_accepted": sum(
                row["signal_type"] == "confirmed" for row in tentative_rows
            ),
            "tentative_mappings_rejected": sum(
                row["signal_type"] == "unsupported" for row in tentative_rows
            ),
        }

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
            (_id("audit"), event_type, entity_type, entity_id, json.dumps(details), _now()),
        )
