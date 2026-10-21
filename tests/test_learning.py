from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from melvan.db import Database
from melvan.model import DemoModelAdapter, MappingDecision
from melvan.service import DomainError, MelvanService


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class LearningMemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "melvan.sqlite3")
        self.model = DemoModelAdapter()
        self.service = MelvanService(
            self.database,
            self.model,
            FIXTURE,
            initial_admin_email="owner@example.org",
        )
        self.service.initialize()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _create_with_mapping(self, confidence: float, element_id: str = "MO4"):
        base = self.model.analyze(
            "Care providers are using the new practice.",
            self.service._catalogue_for_matching(),
        )
        result = replace(
            base,
            classification="Outcome",
            classification_confidence=confidence,
            result_direction="Positive",
            outcome_category="B",
            outcome_change_types=["Practice"],
            match_status="existing_match",
            mapping_decisions=[
                MappingDecision(element_id, confidence, "Test mapping rationale.")
            ],
            proposed_element=None,
            no_match_rationale="",
        )
        with mock.patch.object(self.model, "analyze", return_value=result):
            return self.service.create_statement(
                "Care providers are using the new practice.", "reporter-one"
            )

    def test_acceptance_creates_immutable_truth_and_positive_language_examples(self) -> None:
        statement = self._create_with_mapping(0.9)
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_examples WHERE statement_id = ? ORDER BY dimension",
                (statement["id"],),
            ).fetchall()
            language = connection.execute(
                "SELECT * FROM language_feedback WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
        self.assertEqual(
            {
                "classification",
                "direction",
                "outcome_category",
                "outcome_change_types",
                "mapping",
                "language",
            },
            {row["dimension"] for row in rows},
        )
        self.assertTrue(all(row["truth_weight"] == 1.0 for row in rows))
        mapping = next(row for row in rows if row["dimension"] == "mapping")
        self.assertEqual(0.9, mapping["original_confidence"])
        self.assertAlmostEqual(0.1, mapping["surprise_score"])
        self.assertEqual("positive", language["example_status"])

    def test_surprise_prioritizes_boundary_confirmation_and_confident_error(self) -> None:
        accepted_51 = self._create_with_mapping(0.51)
        self.service.accept_statement(
            accepted_51["id"], accepted_51["analysis"]["id"], "reviewer-one"
        )
        accepted_90 = self._create_with_mapping(0.9)
        self.service.accept_statement(
            accepted_90["id"], accepted_90["analysis"]["id"], "reviewer-one"
        )
        wrong_51 = self._create_with_mapping(0.51)
        wrong_90 = self._create_with_mapping(0.9)
        for statement in (wrong_51, wrong_90):
            with mock.patch.object(
                self.model,
                "analyze",
                return_value=self.model.analyze(
                    statement["original_text"], self.service._catalogue_for_matching()
                ),
            ):
                self.service.submit_feedback(
                    statement["id"],
                    {"analysis_id": statement["analysis"]["id"], "mapping_ids": ["LO5"]},
                    "reviewer-one",
                    accept_as_evidence=True,
                )
        with self.database.connection() as connection:
            mapping_rows = connection.execute(
                """
                SELECT statement_id, signal_type, original_confidence,
                       surprise_score, learning_priority
                FROM learning_examples
                WHERE dimension = 'mapping' AND stable_element_id = 'MO4'
                """
            ).fetchall()
        by_statement = {row["statement_id"]: row for row in mapping_rows}
        self.assertGreater(
            by_statement[accepted_51["id"]]["learning_priority"],
            by_statement[accepted_90["id"]]["learning_priority"],
        )
        self.assertEqual("unsupported", by_statement[wrong_90["id"]]["signal_type"])
        self.assertGreater(
            by_statement[wrong_90["id"]]["learning_priority"],
            by_statement[wrong_51["id"]]["learning_priority"],
        )

    def test_reviewer_added_mapping_is_an_omission_with_maximum_surprise(self) -> None:
        statement = self._create_with_mapping(0.8, "MO4")
        with mock.patch.object(
            self.model,
            "analyze",
            return_value=self.model.analyze(
                statement["original_text"], self.service._catalogue_for_matching()
            ),
        ):
            self.service.submit_feedback(
                statement["id"],
                {
                    "analysis_id": statement["analysis"]["id"],
                    "mapping_ids": ["MO4", "LO5"],
                },
                "reviewer-one",
                accept_as_evidence=True,
            )
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM learning_examples
                WHERE statement_id = ? AND dimension = 'mapping'
                  AND stable_element_id = 'LO5'
                """,
                (statement["id"],),
            ).fetchone()
        self.assertEqual("omitted", row["signal_type"])
        self.assertIsNone(row["original_confidence"])
        self.assertEqual(1.0, row["surprise_score"])

    def test_new_analysis_receives_bounded_auditable_precedents(self) -> None:
        precedent = self._create_with_mapping(0.75)
        self.service.accept_statement(
            precedent["id"], precedent["analysis"]["id"], "reviewer-one"
        )
        original = self.model.analyze
        captured: list[dict] = []

        def spy(*args, **kwargs):
            captured.extend(kwargs.get("approved_examples") or [])
            return original(*args, **kwargs)

        with mock.patch.object(self.model, "analyze", side_effect=spy):
            later = self.service.create_statement(
                "Care providers are using this practice more often.", "reporter-two"
            )
        self.assertTrue(captured)
        self.assertLessEqual(len(captured), 8)
        self.assertTrue(all("reviewer_verified_truth" in item for item in captured))
        with self.database.connection() as connection:
            snapshot = connection.execute(
                "SELECT * FROM learning_snapshots WHERE analysis_id = ?",
                (later["analysis"]["id"],),
            ).fetchone()
            links = connection.execute(
                "SELECT * FROM analysis_learning_examples WHERE analysis_id = ?",
                (later["analysis"]["id"],),
            ).fetchall()
        self.assertTrue(snapshot["memory_enabled"])
        self.assertEqual(len(captured), len(links))

    def test_structured_language_feedback_is_validated_and_scored(self) -> None:
        statement = self._create_with_mapping(0.8)
        with self.assertRaisesRegex(DomainError, "Describe the other language issue"):
            self.service.submit_feedback(
                statement["id"],
                {
                    "analysis_id": statement["analysis"]["id"],
                    "language_issue_types": ["other_language_issue"],
                },
                "reviewer-one",
            )
        with mock.patch.object(
            self.model,
            "analyze",
            return_value=self.model.analyze(
                statement["original_text"], self.service._catalogue_for_matching()
            ),
        ):
            self.service.submit_feedback(
                statement["id"],
                {
                    "analysis_id": statement["analysis"]["id"],
                    "language_issue_types": [
                        "unsupported_or_invented_detail",
                        "overstated_causal_claim",
                    ],
                    "comments": "Remove the unsupported causal claim.",
                },
                "reviewer-one",
                accept_as_evidence=True,
            )
        with self.database.connection() as connection:
            feedback = connection.execute(
                "SELECT * FROM language_feedback WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
            score = connection.execute(
                "SELECT overall_hygiene FROM scoring_events WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
        self.assertEqual(
            ["unsupported_or_invented_detail", "overstated_causal_claim"],
            json.loads(feedback["issue_types_json"]),
        )
        self.assertEqual("paired", feedback["example_status"])
        self.assertEqual(0, score["overall_hygiene"])

    def test_policy_can_be_disabled_and_rolled_back_without_deleting_examples(self) -> None:
        precedent = self._create_with_mapping(0.75)
        self.service.accept_statement(
            precedent["id"], precedent["analysis"]["id"], "reviewer-one"
        )
        self.service.publish_learning_policy(
            {
                "version": "reviewer-memory-disabled",
                "enabled": False,
                "config": {"limit": 4, "rolling_window": 3, "minimum_events": 20},
            },
            "admin-default",
        )
        later = self.service.create_statement("A similar practice is now in use.", "reporter-two")
        self.assertFalse(bool(later["analysis"]["learning_memory_enabled"]))
        self.assertEqual("reviewer-memory-disabled", later["analysis"]["learning_policy_version"])
        self.service.activate_learning_policy("reviewer-memory-v1", "admin-default")
        with self.database.connection() as connection:
            self.assertGreater(
                connection.execute("SELECT COUNT(*) FROM learning_examples").fetchone()[0], 0
            )

    def test_excluded_example_is_audited_and_not_retrieved(self) -> None:
        precedent = self._create_with_mapping(0.75)
        self.service.accept_statement(
            precedent["id"], precedent["analysis"]["id"], "reviewer-one"
        )
        example = self.service.learning_examples()[0]
        excluded = self.service.set_learning_example_eligibility(
            example["id"],
            {"eligible": False, "reason": "Superseded program interpretation."},
            "admin-default",
        )
        self.assertEqual("excluded", excluded["eligibility_status"])
        later = self.service.create_statement(
            "Care providers are using the new practice again.", "reporter-two"
        )
        with self.database.connection() as connection:
            retrieved = {
                row["learning_example_id"]
                for row in connection.execute(
                    "SELECT learning_example_id FROM analysis_learning_examples WHERE analysis_id = ?",
                    (later["analysis"]["id"],),
                ).fetchall()
            }
            audit = connection.execute(
                """
                SELECT 1 FROM audit_events
                WHERE event_type = 'learning_example_eligibility_changed'
                  AND entity_id = ?
                """,
                (example["id"],),
            ).fetchone()
        self.assertNotIn(example["id"], retrieved)
        self.assertIsNotNone(audit)

    def test_completed_session_score_history_is_immutable_and_chronological(self) -> None:
        first = self._create_with_mapping(0.75)
        self.service.accept_statement(first["id"], first["analysis"]["id"], "reviewer-one")
        first_report = self.service.review_workspace("reviewer-one", start=False)["report"]
        second = self._create_with_mapping(0.75)
        self.service.accept_statement(second["id"], second["analysis"]["id"], "reviewer-one")
        second_report = self.service.review_workspace("reviewer-one", start=False)["report"]
        self.assertEqual(1, len(first_report["session_trend"]))
        self.assertEqual(2, len(second_report["session_trend"]))
        self.assertEqual(
            first_report["session_scores"], second_report["session_trend"][0]["scores"]
        )
        with self.database.connection() as connection:
            self.assertEqual(
                2,
                connection.execute(
                    "SELECT COUNT(*) FROM review_session_score_snapshots"
                ).fetchone()[0],
            )


if __name__ == "__main__":
    unittest.main()
