from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from melvan.db import Database
from melvan.model import DemoModelAdapter, MappingDecision, ProposedElement
from melvan.service import DomainError, MelvanService


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "melvan.sqlite3")
        self.service = MelvanService(
            self.database,
            DemoModelAdapter(),
            FIXTURE,
            initial_admin_email="owner@example.org",
        )
        self.service.initialize()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_acceptable(self, statement: dict, element_id: str = "O1") -> dict:
        analysis_id = statement["analysis"]["id"]
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE analyses SET match_status = 'existing_match' WHERE id = ?",
                (analysis_id,),
            )
            connection.execute(
                "DELETE FROM analysis_mappings WHERE analysis_id = ?", (analysis_id,)
            )
            connection.execute(
                """
                INSERT INTO analysis_mappings
                    (analysis_id, element_id, rank, confidence, rationale)
                VALUES (?, ?, 1, 0.9, 'Test-approved Logic Model mapping.')
                """,
                (analysis_id, element_id),
            )
        return self.service.get_statement(statement["id"])

    def test_fixture_loads_first_class_outputs_and_outcomes(self) -> None:
        bootstrap = self.service.bootstrap()
        self.assertEqual(13, len(bootstrap["elements"]))
        mo4 = next(element for element in bootstrap["elements"] if element["id"] == "MO4")
        self.assertEqual("Outcome", mo4["element_type"])
        self.assertEqual(
            "More use of evidence-informed practices and program learning in community services",
            mo4["label"],
        )

    def test_admin_user_metrics_count_distinct_report_and_review_activity(self) -> None:
        reporter = self.service.create_user(
            {
                "first_name": "Rita",
                "last_name": "Reporter",
                "email": "rita@example.org",
                "roles": ["reporter"],
            }
        )
        reviewer = self.service.create_user(
            {
                "first_name": "Reva",
                "last_name": "Reviewer",
                "email": "reva@example.org",
                "roles": ["reviewer"],
            }
        )
        first = self.service.create_statement("A planning meeting was completed.", reporter["id"])
        revised = self.service.submit_feedback(
            first["id"],
            {"analysis_id": first["analysis"]["id"], "comments": "Summary is accurate."},
            reviewer["id"],
        )
        revised = self.make_acceptable(revised)
        self.service.accept_statement(
            first["id"], revised["analysis"]["id"], reviewer["id"]
        )
        second = self.service.create_statement("A second meeting was completed.", reporter["id"])
        second = self.make_acceptable(second)
        self.service.accept_statement(
            second["id"], second["analysis"]["id"], reviewer["id"]
        )
        users = {item["id"]: item for item in self.service.list_users()}
        self.assertEqual(2, users[reporter["id"]]["statement_count"])
        self.assertEqual(2, users[reviewer["id"]]["reviewed_statement_count"])

    def test_logic_model_view_contains_only_authoritative_fixture(self) -> None:
        logic_model = self.service.logic_model()
        self.assertEqual("logic-model-demo-v1", logic_model["id"])
        self.assertEqual(13, len(logic_model["elements"]))
        self.assertEqual(14, len(logic_model["relationships"]))

    def test_output_categories_and_outcome_category_removal_are_normalized(self) -> None:
        logic_model = self.service.logic_model()
        enabling = next(item for item in logic_model["elements"] if item["id"] == "O5")
        self.assertEqual("Output", enabling["type"])
        self.assertEqual("output", enabling["level"])
        self.assertEqual("Enabling outputs", enabling["group"])
        self.assertTrue(
            all(item["group"] is None for item in logic_model["elements"] if item["type"] == "Outcome")
        )

        draft = self.service.start_logic_model_draft("user-admin")
        outcome = next(item for item in draft["elements"] if item["type"] == "Outcome")
        outcome["group"] = "This must not persist"
        saved = self.service.save_logic_model_draft(draft, "user-admin")
        saved_outcome = next(
            item for item in saved["elements"] if item["stable_id"] == outcome["stable_id"]
        )
        self.assertIsNone(saved_outcome["group"])

    def test_statement_preserves_exact_text_and_survives_new_service(self) -> None:
        original = "  We held a planning meeting.\nSecond line remains.  "
        statement = self.service.create_statement(original)
        self.assertEqual(original, statement["original_text"])
        restarted = MelvanService(self.database, DemoModelAdapter(), FIXTURE)
        restarted.initialize()
        self.assertEqual(original, restarted.get_statement(statement["id"])["original_text"])

    def test_statement_context_is_separate_versioned_and_reanalyzed(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was completed.",
            "reporter-one",
            "The implementation team attended.",
        )
        self.assertEqual("The implementation team attended.", statement["context"]["text"])
        self.assertEqual(1, statement["context"]["version"])
        self.assertEqual(
            statement["context"]["id"], statement["analysis"]["context_revision_id"]
        )
        original = statement["original_text"]

        revised = self.service.update_statement_context(
            statement["id"], "The clinical implementation team attended.", "reporter-one"
        )
        self.assertEqual(original, revised["original_text"])
        self.assertEqual(2, revised["context"]["version"])
        self.assertEqual(2, revised["analysis"]["version"])
        self.assertEqual(
            revised["context"]["id"], revised["analysis"]["context_revision_id"]
        )
        with self.database.connection() as connection:
            self.assertEqual(
                2,
                connection.execute(
                    "SELECT COUNT(*) FROM statement_context_revisions WHERE statement_id = ?",
                    (statement["id"],),
                ).fetchone()[0],
            )

    def test_unchanged_context_is_a_no_op(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was completed.", "reporter-one", "Team meeting."
        )
        with self.database.connection() as connection:
            audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("No-op Context invoked analysis."),
        ):
            unchanged = self.service.update_statement_context(
                statement["id"], "Team meeting.", "reporter-one"
            )
        self.assertEqual(1, unchanged["context"]["version"])
        self.assertEqual(1, unchanged["analysis"]["version"])
        with self.database.connection() as connection:
            self.assertEqual(
                audit_before,
                connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
            )

    def test_model_receives_statement_context_as_a_separate_input(self) -> None:
        with mock.patch.object(
            self.service.model,
            "analyze",
            wraps=self.service.model.analyze,
        ) as analyze:
            self.service.create_statement(
                "A planning meeting was completed.",
                "reporter-one",
                "Observed during regional implementation.",
            )
        self.assertEqual(
            "Observed during regional implementation.",
            analyze.call_args.kwargs["statement_context"],
        )

    def test_context_limit_rejects_without_partial_statement(self) -> None:
        with self.assertRaisesRegex(DomainError, "Context must be 10,000"):
            self.service.create_statement(
                "A planning meeting was completed.", "reporter-one", "x" * 10_001
            )
        with self.database.connection() as connection:
            self.assertEqual(
                0, connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
            )

    def test_context_ownership_and_evidence_lock_are_enforced(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was completed.", "reporter-one"
        )
        with self.assertRaises(DomainError) as other_user:
            self.service.update_statement_context(
                statement["id"], "Not mine.", "reporter-two"
            )
        self.assertEqual(404, other_user.exception.status)
        accepted = self.service.accept_statement(
            statement["id"], statement["analysis"]["id"]
        )
        with self.assertRaises(DomainError) as locked:
            self.service.update_statement_context(
                accepted["id"], "Too late.", "reporter-one"
            )
        self.assertEqual(409, locked.exception.status)

    def test_reporter_history_is_owned_and_newest_first(self) -> None:
        first = self.service.create_statement("First meeting.", "reporter-one")
        second = self.service.create_statement("Second meeting.", "reporter-one")
        self.service.create_statement("Another person's meeting.", "reporter-two")
        history = self.service.reporter_statements("reporter-one")
        expected = sorted(
            [first, second], key=lambda item: (item["submitted_at"], item["id"]), reverse=True
        )
        self.assertEqual([item["id"] for item in expected], [item["id"] for item in history])
        self.assertTrue(all(item["reporter_id"] == "reporter-one" for item in history))

    def test_failed_context_reanalysis_leaves_no_active_analysis(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was completed.", "reporter-one"
        )
        with mock.patch.object(
            self.service.model, "analyze", side_effect=ValueError("model unavailable")
        ):
            with self.assertRaises(DomainError) as failure:
                self.service.update_statement_context(
                    statement["id"], "New circumstances.", "reporter-one"
                )
        self.assertEqual(502, failure.exception.status)
        failed = self.service.get_statement_for_reporter(statement["id"], "reporter-one")
        self.assertEqual("failed", failed["status"])
        self.assertIsNone(failed["active_analysis_id"])
        self.assertIsNone(failed["analysis"])
        self.assertEqual("New circumstances.", failed["context"]["text"])

    def test_context_reanalysis_uses_the_logic_model_current_at_start(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was completed.", "reporter-one"
        )
        self.assertEqual("1.0", statement["analysis"]["logic_model_version"])
        draft = self.service.start_logic_model_draft("user-admin")
        draft["title"] = "Current version for Context analysis"
        self.service.save_logic_model_draft(draft, "user-admin")
        committed = self.service.commit_logic_model_draft("user-admin", confirmed=True)
        revised = self.service.update_statement_context(
            statement["id"], "New implementation circumstances.", "reporter-one"
        )
        self.assertEqual(committed["database_id"], revised["analysis"]["logic_model_id"])
        self.assertEqual("2.0", revised["analysis"]["logic_model_version"])

    def test_existing_match_returns_full_element_record(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        self.assertEqual("Outcome", statement["analysis"]["classification"])
        self.assertEqual(["MO4"], [mapping["id"] for mapping in statement["mappings"]])
        self.assertIn("community services", statement["mappings"][0]["label"])
        self.assertEqual("Positive", statement["analysis"]["result_direction"])

    def test_mapping_proposal_threshold_filters_each_mapping_independently(self) -> None:
        text = "Community partners are using evidence-informed practices in service planning."
        base = self.service.model.analyze(text, self.service._catalogue_for_matching())
        result = replace(
            base,
            mapping_decisions=[
                MappingDecision("MO4", 0.91, "Direct practice-integration relationship."),
                MappingDecision("LO5", 0.50, "Exactly even confidence is supported."),
                MappingDecision("SO2", 0.51, "Slightly more likely than not."),
                MappingDecision("O1", 0.25, "Minimum visible tentative relationship."),
                MappingDecision("O2", 0.24, "Below the display floor."),
            ],
            no_match_rationale="",
            proposed_element=None,
        )
        with mock.patch.object(self.service.model, "analyze", return_value=result):
            statement = self.service.create_statement(text)
        self.assertEqual(
            ["MO4", "LO5", "SO2", "O1"],
            [item["id"] for item in statement["mappings"]],
        )
        self.assertEqual(
            [0.91, 0.50, 0.51, 0.25],
            [item["confidence"] for item in statement["mappings"]],
        )
        self.assertEqual(
            ["supported", "supported", "supported", "low"],
            [item["confidence_tier"] for item in statement["mappings"]],
        )
        self.assertEqual(
            [
                "Direct practice-integration relationship.",
                "Exactly even confidence is supported.",
                "Slightly more likely than not.",
                "Minimum visible tentative relationship.",
            ],
            [item["rationale"] for item in statement["mappings"]],
        )

    def test_all_mapping_proposals_below_quarter_become_no_match(self) -> None:
        text = "Community partners are using evidence-informed practices in service planning."
        base = self.service.model.analyze(text, self.service._catalogue_for_matching())
        result = replace(
            base,
            match_status="existing_match",
            mapping_decisions=[
                MappingDecision("MO4", 0.24, "Below the display floor."),
                MappingDecision("LO5", 0.10, "Very weak downstream relationship."),
            ],
            no_match_rationale="",
            proposed_element=None,
        )
        with mock.patch.object(self.service.model, "analyze", return_value=result):
            statement = self.service.create_statement(text)
        self.assertEqual([], statement["mappings"])
        self.assertEqual("no_suitable_match", statement["analysis"]["match_status"])
        self.assertIn("25% display threshold", statement["analysis"]["mapping_rationale"])

    def test_pending_mapping_at_half_is_shown_and_can_be_accepted(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-one",
        )
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO analysis_mappings "
                "(analysis_id, element_id, rank, confidence, rationale) "
                "VALUES (?, 'LO5', 2, 0.50, 'Legacy exactly-even proposal.')",
                (statement["analysis"]["id"],),
            )

        pending = self.service.get_statement(statement["id"])
        self.assertEqual(["MO4", "LO5"], [item["id"] for item in pending["mappings"]])
        accepted = self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        self.assertEqual(["MO4", "LO5"], [item["id"] for item in accepted["mappings"]])

    def test_tentative_mapping_candidates_are_bounded_to_three(self) -> None:
        text = "Community partners are using evidence-informed practices in service planning."
        base = self.service.model.analyze(text, self.service._catalogue_for_matching())
        result = replace(
            base,
            match_status="existing_match",
            mapping_decisions=[
                MappingDecision("O1", 0.49, "First tentative relationship."),
                MappingDecision("O2", 0.34, "Weakest tentative relationship."),
                MappingDecision("SO2", 0.39, "Third tentative relationship."),
                MappingDecision("MO4", 0.44, "Second tentative relationship."),
            ],
            no_match_rationale="",
            proposed_element=None,
        )
        with mock.patch.object(self.service.model, "analyze", return_value=result):
            statement = self.service.create_statement(text)
        self.assertEqual(
            ["O1", "MO4", "SO2"],
            [item["id"] for item in statement["mappings"]],
        )

    def test_rejecting_only_tentative_mapping_does_not_reduce_quality_score(self) -> None:
        text = "Community partners are using evidence-informed practices in service planning."
        base = self.service.model.analyze(text, self.service._catalogue_for_matching())
        result = replace(
            base,
            match_status="existing_match",
            mapping_decisions=[
                MappingDecision("MO4", 0.86, "Supported relationship."),
                MappingDecision("LO5", 0.42, "Tentative downstream relationship."),
            ],
            no_match_rationale="",
            proposed_element=None,
        )
        with mock.patch.object(self.service.model, "analyze", return_value=result):
            statement = self.service.create_statement(text, "reporter-one")
        self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "mapping_ids": ["MO4"],
            },
            "reviewer-one",
            accept_as_evidence=True,
        )
        with self.database.connection() as connection:
            score = connection.execute(
                "SELECT first_review_approval, correct_mapping, source_signals_json "
                "FROM scoring_events WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
            example = connection.execute(
                "SELECT signal_type, original_confidence FROM learning_examples "
                "WHERE statement_id = ? AND dimension = 'mapping' "
                "AND signal_type = 'unsupported'",
                (statement["id"],),
            ).fetchone()
        self.assertEqual(1, score["first_review_approval"])
        self.assertEqual(1, score["correct_mapping"])
        signals = json.loads(score["source_signals_json"])
        self.assertTrue(signals["only_tentative_mapping_rejections"])
        self.assertEqual(["LO5"], signals["tentative_mapping_rejections"])
        self.assertEqual("unsupported", example["signal_type"])
        self.assertEqual(0.42, example["original_confidence"])
        with self.database.connection() as connection:
            session_id = connection.execute(
                "SELECT session_id FROM review_events WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()["session_id"]
            diagnostics = self.service.learning.session_diagnostics(connection, session_id)
        self.assertEqual(1, diagnostics["tentative_mappings_presented"])
        self.assertEqual(0, diagnostics["tentative_mappings_accepted"])
        self.assertEqual(1, diagnostics["tentative_mappings_rejected"])

    def test_accepting_tentative_mapping_counts_as_correct_and_is_learned(self) -> None:
        text = "Community partners are using evidence-informed practices in service planning."
        base = self.service.model.analyze(text, self.service._catalogue_for_matching())
        result = replace(
            base,
            match_status="existing_match",
            mapping_decisions=[
                MappingDecision("MO4", 0.38, "Tentative relationship."),
            ],
            no_match_rationale="",
            proposed_element=None,
        )
        with mock.patch.object(self.service.model, "analyze", return_value=result):
            statement = self.service.create_statement(text, "reporter-one")
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        with self.database.connection() as connection:
            score = connection.execute(
                "SELECT first_review_approval, correct_mapping FROM scoring_events "
                "WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
            example = connection.execute(
                "SELECT signal_type, original_confidence FROM learning_examples "
                "WHERE statement_id = ? AND dimension = 'mapping'",
                (statement["id"],),
            ).fetchone()
        self.assertEqual(1, score["first_review_approval"])
        self.assertEqual(1, score["correct_mapping"])
        self.assertEqual("confirmed", example["signal_type"])
        self.assertEqual(0.38, example["original_confidence"])

    def test_historical_approved_mapping_is_not_removed_by_proposal_threshold(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-one",
        )
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE evidence_assessment_mappings SET confidence = 0.45"
            )

        evidence = self.service.get_statement(statement["id"])
        self.assertEqual(["MO4"], [item["id"] for item in evidence["mappings"]])
        self.assertEqual(0.45, evidence["mappings"][0]["confidence"])

    def test_reviewer_result_direction_is_authoritative(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        revised = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "corrected_result_direction": "Negative",
            },
        )
        self.assertEqual("Negative", revised["analysis"]["result_direction"])
        with self.database.connection() as connection:
            feedback = connection.execute(
                "SELECT corrected_result_direction FROM review_feedback"
            ).fetchone()
        self.assertEqual("Negative", feedback["corrected_result_direction"])

    def test_activity_has_no_result_direction(self) -> None:
        statement = self.service.create_statement("We held a planning meeting.")
        self.assertEqual("Activity", statement["analysis"]["classification"])
        self.assertIsNone(statement["analysis"]["result_direction"])

    def test_feedback_is_versioned_and_reviewer_mapping_is_authoritative(self) -> None:
        statement = self.service.create_statement("A computing upgrade was completed.")
        revised = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "corrected_classification": "Outcome",
                "mapping_ids": ["MO4", "LO5"],
                "comments": "This reflects practice integration and improved clinical precision.",
            },
        )
        self.assertEqual(2, revised["analysis"]["version"])
        self.assertEqual("Outcome", revised["analysis"]["classification"])
        self.assertEqual(1.0, revised["analysis"]["classification_confidence"])
        self.assertEqual(["MO4", "LO5"], [mapping["id"] for mapping in revised["mappings"]])
        self.assertEqual([1.0, 1.0], [mapping["confidence"] for mapping in revised["mappings"]])
        self.assertTrue(
            all(
                mapping["rationale"]
                == "Applied directly from authoritative Reviewer feedback."
                for mapping in revised["mappings"]
            )
        )
        with self.database.connection() as connection:
            versions = connection.execute(
                "SELECT COUNT(*) FROM analyses WHERE statement_id = ?", (statement["id"],)
            ).fetchone()[0]
        self.assertEqual(2, versions)

    def test_reviewer_change_can_atomically_accept_revised_statement_as_evidence(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-one",
        )
        accepted = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "mapping_ids": ["LO5"],
            },
            "reviewer-one",
            accept_as_evidence=True,
        )

        self.assertEqual("evidence", accepted["status"])
        self.assertEqual(2, accepted["analysis"]["version"])
        self.assertEqual(["LO5"], [mapping["id"] for mapping in accepted["mappings"]])
        self.assertEqual([], self.service.review_statements("reviewer-one"))
        self.assertEqual([statement["id"]], [item["id"] for item in self.service.evidence()])
        with self.database.connection() as connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM evidence WHERE statement_id = ?", (statement["id"],)
                ).fetchone()[0],
            )
            events = connection.execute(
                "SELECT decision FROM review_events WHERE statement_id = ?",
                (statement["id"],),
            ).fetchall()
            self.assertEqual(["changed"], [event["decision"] for event in events])
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM scoring_events WHERE statement_id = ?",
                    (statement["id"],),
                ).fetchone()[0],
            )

    def test_stale_analysis_cannot_be_modified_or_accepted(self) -> None:
        statement = self.service.create_statement("A computing upgrade was completed.")
        revised = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "comments": "Keep the wording concise.",
            },
        )
        with self.assertRaises(DomainError) as feedback_error:
            self.service.submit_feedback(
                statement["id"],
                {"analysis_id": statement["analysis"]["id"], "comments": "Stale edit."},
            )
        self.assertEqual(409, feedback_error.exception.status)
        with self.assertRaises(DomainError) as accept_error:
            self.service.accept_statement(statement["id"], statement["analysis"]["id"])
        self.assertEqual(409, accept_error.exception.status)
        self.assertEqual(2, revised["analysis"]["version"])

    def test_provider_proposal_is_not_persisted_as_a_logic_model_mapping(self) -> None:
        baseline = self.service.model.analyze(
            "We're making excellent progress on the machine learning system.",
            self.service._catalogue_for_matching(),
        )
        provider_result = replace(
            baseline,
            match_status="no_suitable_match",
            mapping_decisions=[],
            no_match_rationale="No approved Logic Model element is a defensible match.",
            proposed_element=ProposedElement(
                element_type="Outcome",
                label=(
                    "Increased positive perception and confidence among stakeholders "
                    "about the AI model following peer review"
                ),
                reason="Provider-generated suggestion.",
            ),
        )
        with mock.patch.object(self.service.model, "analyze", return_value=provider_result):
            statement = self.service.create_statement(
                "We're making excellent progress on the machine learning system."
            )

        self.assertEqual("no_suitable_match", statement["analysis"]["match_status"])
        self.assertEqual([], statement["mappings"])
        self.assertIsNone(statement["proposed_element"])
        with self.database.connection() as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM elements WHERE source = 'model'"
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_mappings WHERE analysis_id = ?",
                    (statement["analysis"]["id"],),
                ).fetchone()[0],
            )

    def test_historical_working_outcome_is_retained_for_audit_but_not_displayed(self) -> None:
        statement = self.service.create_statement("A planning meeting was completed.")
        analysis_id = statement["analysis"]["id"]
        proposal_id = "WP-edf24d018030"
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO elements
                    (id, display_id, logic_model_id, label, element_type, level,
                     group_name, source, status, created_at)
                VALUES (?, ?, NULL, ?, 'Outcome', 'working_outcome',
                        'MELvan proposal', 'model', 'provisional', ?)
                """,
                (
                    proposal_id,
                    proposal_id,
                    "Increased positive perception and confidence among stakeholders",
                    "2026-07-30T14:56:00+00:00",
                ),
            )
            connection.execute(
                "DELETE FROM analysis_mappings WHERE analysis_id = ?", (analysis_id,)
            )
            connection.execute(
                "INSERT INTO analysis_mappings "
                "(analysis_id, element_id, rank, confidence, rationale) "
                "VALUES (?, ?, 1, NULL, NULL)",
                (analysis_id, proposal_id),
            )
            connection.execute(
                "UPDATE analyses SET proposed_element_id = ? WHERE id = ?",
                (proposal_id, analysis_id),
            )

        retrieved = self.service.get_statement(statement["id"])
        self.assertEqual([], retrieved["mappings"])
        self.assertIsNone(retrieved["proposed_element"])
        with self.database.connection() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM elements WHERE id = ?", (proposal_id,)
                ).fetchone()
            )
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_mappings WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()[0],
            )

    def test_reviewer_cannot_create_a_logic_model_element(self) -> None:
        statement = self.service.create_statement("Community belonging improved significantly.")
        with self.assertRaisesRegex(DomainError, "Only Administrators"):
            self.service.submit_feedback(
                statement["id"],
                {
                    "analysis_id": statement["analysis"]["id"],
                    "new_element": {
                        "element_type": "Outcome",
                        "label": "Improved community belonging",
                    },
                },
            )

    def test_reviewer_cannot_create_a_new_output(self) -> None:
        statement = self.service.create_statement("A computing upgrade was completed.")
        with self.assertRaisesRegex(DomainError, "Only Administrators"):
            self.service.submit_feedback(
                statement["id"],
                {
                    "analysis_id": statement["analysis"]["id"],
                    "new_element": {
                        "element_type": "Output",
                        "label": "New computing package completed",
                    },
                },
            )

    def test_acceptance_moves_statement_to_evidence_and_updates_metric(self) -> None:
        first = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        first = self.make_acceptable(first)
        accepted_first = self.service.accept_statement(first["id"], first["analysis"]["id"])
        self.assertEqual("evidence", accepted_first["status"])
        self.assertEqual([], self.service.review_statements())
        self.assertEqual(1, len(self.service.evidence()))
        self.assertEqual({"matches": 1, "total": 1, "percentage": 100.0}, self.service.classification_agreement())

        second = self.service.create_statement("A computing upgrade was completed.")
        corrected = self.service.submit_feedback(
            second["id"],
            {
                "analysis_id": second["analysis"]["id"],
                "corrected_classification": "Activity",
            },
        )
        corrected = self.make_acceptable(corrected)
        self.service.accept_statement(second["id"], corrected["analysis"]["id"])
        self.assertEqual({"matches": 1, "total": 2, "percentage": 50.0}, self.service.classification_agreement())

    def test_workspace_metrics_reuse_lifetime_review_scoring(self) -> None:
        empty = self.service.workspace_metrics()
        self.assertEqual(0, empty["scored_reviews"])
        self.assertEqual(
            {"statement": 0, "evidence_reassessment": 0}, empty["review_cycles"]
        )
        self.assertEqual(0, empty["current_evidence"])
        self.assertEqual(0, empty["completed_review_sessions"])
        self.assertTrue(
            all(score["percentage"] is None for score in empty["lifetime_scores"].values())
        )

        statement = self.service.create_statement(
            "Care providers are using PMH-aligned practices in treatment planning."
        )
        statement = self.make_acceptable(statement)
        self.service.accept_statement(statement["id"], statement["analysis"]["id"])
        metrics = self.service.workspace_metrics()

        self.assertEqual(1, metrics["scored_reviews"])
        self.assertEqual(
            {"statement": 1, "evidence_reassessment": 0}, metrics["review_cycles"]
        )
        self.assertEqual(1, metrics["current_evidence"])
        self.assertEqual(
            {
                "first_review_approval",
                "correct_level",
                "correct_direction",
                "correct_mapping",
                "overall_hygiene",
            },
            set(metrics["lifetime_scores"]),
        )
        self.assertEqual(
            {"matches": 1, "total": 1, "percentage": 100.0},
            metrics["lifetime_scores"]["first_review_approval"],
        )

    def test_no_match_cannot_be_accepted_without_an_approved_mapping(self) -> None:
        statement = self.service.create_statement("Mosquito prevalence decreased.")
        self.assertIsNone(statement["proposed_element"])
        with self.assertRaisesRegex(DomainError, "approved Logic Model mapping"):
            self.service.accept_statement(statement["id"], statement["analysis"]["id"])

    def test_no_match_classification_change_remains_pending(self) -> None:
        statement = self.service.create_statement("Mosquito prevalence decreased.")
        revised = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "corrected_classification": "Activity",
            },
            "reviewer-one",
            accept_as_evidence=False,
        )
        self.assertEqual("awaiting_review", revised["status"])
        self.assertEqual("Activity", revised["analysis"]["classification"])
        self.assertEqual([], revised["mappings"])
        with self.assertRaisesRegex(DomainError, "approved Logic Model mapping"):
            self.service.accept_statement(
                revised["id"], revised["analysis"]["id"], "reviewer-one"
            )

    def test_review_queue_includes_self_authored_statements_and_is_newest_first(self) -> None:
        own = self.service.create_statement("My planning meeting was held.", "reviewer-one")
        older = self.service.create_statement("An older planning meeting was held.", "reporter-one")
        newer = self.service.create_statement("A newer planning meeting was held.", "reporter-two")
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE statements SET submitted_at = ? WHERE id = ?",
                ("2026-07-18T10:00:00+00:00", older["id"]),
            )
            connection.execute(
                "UPDATE statements SET submitted_at = ? WHERE id = ?",
                ("2026-07-18T11:00:00+00:00", newer["id"]),
            )
        queue = self.service.review_statements("reviewer-one")
        self.assertEqual(
            [own["id"], newer["id"], older["id"]],
            [item["id"] for item in queue],
        )
        own = self.make_acceptable(own)
        accepted = self.service.accept_statement(
            own["id"], own["analysis"]["id"], "reviewer-one"
        )
        self.assertEqual("evidence", accepted["status"])
        with self.database.connection() as connection:
            event = connection.execute(
                """
                SELECT reviewer_id, is_self_review FROM review_events
                WHERE statement_id = ?
                """,
                (own["id"],),
            ).fetchone()
            evidence = connection.execute(
                "SELECT accepted_by FROM evidence WHERE statement_id = ?",
                (own["id"],),
            ).fetchone()
        self.assertEqual("reviewer-one", event["reviewer_id"])
        self.assertEqual(1, event["is_self_review"])
        self.assertEqual("reviewer-one", evidence["accepted_by"])

    def test_owner_administrator_with_reporter_and_reviewer_roles_can_self_review(self) -> None:
        owner = self.service.users.owner()
        self.service.update_user(
            owner["id"],
            {"roles": ["owner", "administrator", "reporter", "reviewer"]},
            actor_id=owner["id"],
            actor_is_owner=True,
        )
        statement = self.service.create_statement(
            "The Owner facilitated a program planning session.",
            owner["id"],
        )

        queue = self.service.review_statements(owner["id"])
        self.assertIn(statement["id"], [item["id"] for item in queue])
        statement = self.make_acceptable(statement)
        accepted = self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], owner["id"]
        )

        self.assertEqual("evidence", accepted["status"])
        with self.database.connection() as connection:
            event = connection.execute(
                "SELECT is_self_review FROM review_events WHERE statement_id = ?",
                (statement["id"],),
            ).fetchone()
        self.assertEqual(1, event["is_self_review"])

    def test_review_session_resumes_and_end_leaves_untouched_statements(self) -> None:
        statement = self.service.create_statement("A planning meeting was held.", "reporter-one")
        first = self.service.open_review_session("reviewer-one")
        resumed = self.service.open_review_session("reviewer-one")
        self.assertEqual(first["session"]["id"], resumed["session"]["id"])

        report = self.service.end_review_session("reviewer-one")
        self.assertEqual("ended", report["completion_reason"])
        self.assertEqual(0, report["counts"]["reviewed"])
        self.assertEqual("awaiting_review", self.service.get_statement(statement["id"])["status"])
        later = self.service.open_review_session("reviewer-one")
        self.assertNotEqual(first["session"]["id"], later["session"]["id"])

    def test_mapping_only_change_scores_the_agreed_five_dimensions(self) -> None:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-one",
        )
        revised = self.service.submit_feedback(
            statement["id"],
            {
                "analysis_id": statement["analysis"]["id"],
                "mapping_ids": ["LO5"],
            },
            "reviewer-one",
        )
        self.service.accept_statement(
            statement["id"], revised["analysis"]["id"], "reviewer-one"
        )
        workspace = self.service.review_workspace("reviewer-one", start=False)
        report = workspace["report"]
        expected = {
            "first_review_approval": 0,
            "correct_level": 1,
            "correct_direction": 1,
            "correct_mapping": 0,
            "overall_hygiene": 1,
        }
        for dimension, matches in expected.items():
            self.assertEqual(matches, report["session_scores"][dimension]["matches"])
            self.assertEqual(1, report["session_scores"][dimension]["total"])
        with self.database.connection() as connection:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM scoring_events WHERE statement_id = ?",
                    (statement["id"],),
                ).fetchone()[0],
            )

    def test_comment_scoring_distinguishes_confirmation_from_hygiene_feedback(self) -> None:
        confirmed = self.service.create_statement(
            "A planning meeting was held.", "reporter-one"
        )
        self.service.submit_feedback(
            confirmed["id"],
            {
                "analysis_id": confirmed["analysis"]["id"],
                "comments": "The summary and rationale are accurate.",
            },
            "reviewer-one",
        )
        defect = self.service.create_statement(
            "A second planning meeting was held.", "reporter-two"
        )
        self.service.submit_feedback(
            defect["id"],
            {
                "analysis_id": defect["analysis"]["id"],
                "comments": "The summary is inaccurate and the rationale invents details.",
            },
            "reviewer-one",
        )
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT statement_id, correct_level, correct_mapping, overall_hygiene, "
                "source_signals_json, scoring_policy_version, interpretation_model_version, "
                "interpretation_prompt_version "
                "FROM scoring_events ORDER BY created_at, id"
            ).fetchall()
        scores = {row["statement_id"]: dict(row) for row in rows}
        self.assertEqual(1, scores[confirmed["id"]]["overall_hygiene"])
        self.assertEqual(0, scores[defect["id"]]["overall_hygiene"])
        self.assertEqual(1, scores[defect["id"]]["correct_level"])
        self.assertEqual(1, scores[defect["id"]]["correct_mapping"])
        signals = json.loads(scores[defect["id"]]["source_signals_json"])
        self.assertTrue(signals["applicability"]["overall_hygiene"])
        self.assertEqual("review-scoring-v2", scores[defect["id"]]["scoring_policy_version"])
        self.assertEqual(
            "deterministic-review-feedback-v1",
            scores[defect["id"]]["interpretation_model_version"],
        )
        self.assertIsNone(scores[defect["id"]]["interpretation_prompt_version"])

    def test_output_acceptance_has_no_direction_score(self) -> None:
        statement = self.service.create_statement(
            "A computing upgrade was completed.", "reporter-one"
        )
        self.assertEqual("Output", statement["analysis"]["classification"])
        self.assertIsNone(statement["analysis"]["result_direction"])
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        report = self.service.review_workspace("reviewer-one", start=False)["report"]
        self.assertEqual(0, report["session_scores"]["correct_direction"]["total"])
        self.assertIsNone(report["session_scores"]["correct_direction"]["percentage"])

    def test_program_irrelevant_is_audited_hidden_and_never_scored(self) -> None:
        statement = self.service.create_statement(
            "A planning meeting was held.", "reporter-one"
        )
        self.service.mark_statement_irrelevant(
            statement["id"], statement["analysis"]["id"], "reviewer-one", "Outside scope."
        )
        self.assertEqual([], self.service.review_statements("reviewer-one"))
        self.assertEqual([], self.service.reporter_statements("reporter-one"))
        self.assertEqual([], self.service.evidence())
        report = self.service.review_workspace("reviewer-one", start=False)["report"]
        self.assertEqual(1, report["counts"]["not_relevant"])
        self.assertEqual(0, report["session_scores"]["first_review_approval"]["total"])
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT program_irrelevant_by, program_irrelevant_analysis_id, "
                "program_irrelevant_reason FROM statements WHERE id = ?",
                (statement["id"],),
            ).fetchone()
            self.assertEqual("reviewer-one", row["program_irrelevant_by"])
            self.assertEqual(statement["analysis"]["id"], row["program_irrelevant_analysis_id"])
            self.assertEqual("Outside scope.", row["program_irrelevant_reason"])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scoring_events").fetchone()[0])

    def test_completed_review_report_survives_service_restart(self) -> None:
        statement = self.service.create_statement("A planning meeting was held.", "reporter-one")
        statement = self.make_acceptable(statement)
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        original = self.service.review_workspace("reviewer-one", start=False)["report"]
        restarted = MelvanService(self.database, DemoModelAdapter(), FIXTURE)
        restarted.initialize()
        persisted = restarted.review_workspace("reviewer-one", start=False)["report"]
        self.assertEqual(original["session_id"], persisted["session_id"])
        self.assertEqual(original["lifetime_scores"], persisted["lifetime_scores"])

    def test_logic_model_draft_commit_and_restore(self) -> None:
        evidence_statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        evidence_statement = self.make_acceptable(evidence_statement)
        self.service.accept_statement(
            evidence_statement["id"], evidence_statement["analysis"]["id"]
        )
        pending = self.service.create_statement("We held a planning meeting.")
        original_model = self.service.logic_model()

        draft = self.service.start_logic_model_draft("user-admin")
        first_output = next(item for item in draft["elements"] if item["type"] == "Output")
        first_output["label"] = "Updated capacity building package completed"
        saved = self.service.save_logic_model_draft(draft, "user-admin")
        summary = self.service.logic_model_commit_summary()
        self.assertTrue(summary["impact"]["requires_assessment"])
        committed = self.service.commit_logic_model_draft("user-admin", confirmed=True)
        self.assertTrue(committed["is_current"])
        self.assertEqual("2.0", committed["version"])
        self.assertEqual(2, len(self.service.logic_model_versions()))
        committed_output = next(
            item for item in committed["elements"] if item["stable_id"] == first_output["stable_id"]
        )
        self.assertEqual(2, committed_output["revision"])
        self.assertIsNone(self.service.logic_model_draft())

        preview = self.service.logic_model(original_model["database_id"])
        self.assertFalse(preview["is_current"])
        self.assertEqual(committed["database_id"], self.service.logic_model()["database_id"])
        restored = self.service.activate_logic_model(
            original_model["database_id"], "user-admin", reason="restore"
        )
        self.assertTrue(restored["is_current"])
        self.assertEqual(original_model["database_id"], restored["database_id"])
        self.assertEqual("awaiting_review", self.service.get_statement(pending["id"])["status"])

    def test_name_and_recap_only_commit_never_runs_analysis_or_queues_review(self) -> None:
        evidence_statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        self.service.accept_statement(evidence_statement["id"], evidence_statement["analysis"]["id"])
        pending = self.service.create_statement("We held a planning meeting.")
        pending_version = pending["analysis"]["version"]

        draft = self.service.start_logic_model_draft("user-admin")
        draft["title"] = "Renamed Community Logic Model"
        draft["recap"]["risks"]["text"] = "Funding may be delayed."
        self.service.save_logic_model_draft(draft, "user-admin")
        summary = self.service.logic_model_commit_summary()
        self.assertTrue(summary["impact"]["metadata_only"])
        self.assertFalse(summary["impact"]["requires_assessment"])

        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("Metadata-only commit invoked analysis."),
        ):
            committed = self.service.commit_logic_model_draft("user-admin", confirmed=True)
        self.assertEqual("Renamed Community Logic Model", committed["title"])
        self.assertEqual("Funding may be delayed.", committed["recap"]["risks"]["text"])
        self.assertEqual(2, committed["recap"]["risks"]["revision"])
        self.assertEqual([], self.service.evidence_review_items())
        self.assertEqual(pending_version, self.service.get_statement(pending["id"])["analysis"]["version"])

    def test_formatting_only_element_edit_never_runs_analysis_or_queues_review(self) -> None:
        evidence_statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning."
        )
        self.service.accept_statement(evidence_statement["id"], evidence_statement["analysis"]["id"])
        draft = self.service.start_logic_model_draft("user-admin")
        mo4 = next(item for item in draft["elements"] if item["stable_id"] == "MO4")
        mo4["label"] = f'  {mo4["label"].upper()}!  '
        self.service.save_logic_model_draft(draft, "user-admin")
        summary = self.service.logic_model_commit_summary()
        self.assertEqual(1, summary["stylistic_changes"])
        self.assertTrue(summary["impact"]["metadata_only"])
        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("Formatting-only edit invoked analysis."),
        ):
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        self.assertEqual([], self.service.evidence_review_items())

    def _accepted_mo4_evidence(self) -> tuple[dict, dict, str]:
        statement = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-one",
        )
        self.assertEqual(["MO4"], [item["stable_id"] for item in statement["mappings"]])
        self.service.accept_statement(
            statement["id"], statement["analysis"]["id"], "reviewer-one"
        )
        with self.database.connection() as connection:
            assessment = connection.execute(
                """
                SELECT ea.* FROM evidence_assessments ea
                JOIN evidence e ON e.id = ea.evidence_id
                WHERE e.statement_id = ?
                """,
                (statement["id"],),
            ).fetchone()
            connection.execute(
                "UPDATE evidence_assessment_mappings SET confidence = 0.80 WHERE assessment_id = ?",
                (assessment["id"],),
            )
        return statement, self.service.logic_model(), assessment["id"]

    def _commit_mo4_edit_with_confidence(self, confidence: float) -> dict:
        draft = self.service.start_logic_model_draft("user-admin")
        mo4 = next(item for item in draft["elements"] if item["stable_id"] == "MO4")
        mo4["label"] = f'{mo4["label"]} in participating services'
        self.service.save_logic_model_draft(draft, "user-admin")
        demo = DemoModelAdapter()

        def reassess(statement, elements, **kwargs):
            target = next(item for item in elements if item.get("stable_id") == "MO4")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[
                    MappingDecision(target["id"], confidence, "Updated-node confidence.")
                ],
                match_status="existing_match",
                no_match_rationale="",
                proposed_element=None,
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=reassess):
            return self.service.commit_logic_model_draft("user-admin", confirmed=True)

    def test_evidence_confidence_threshold_is_25_percentage_points(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.56)
        self.assertEqual([], self.service.evidence_review_items("reviewer-two"))
        with self.database.connection() as connection:
            activation = connection.execute(
                "SELECT impact_manifest_json FROM logic_model_activations "
                "WHERE status = 'completed' ORDER BY activated_at DESC LIMIT 1"
            ).fetchone()
        outcomes = json.loads(activation["impact_manifest_json"])["records"]
        excluded = next(item for item in outcomes if item["outcome"] == "excluded")
        self.assertEqual("no_material_change", excluded["reason"])
        self.assertEqual(24.0, excluded["observations"][0]["confidence_delta_percentage_points"])

        second = ServiceTestCase(methodName="runTest")
        second.setUp()
        try:
            second._accepted_mo4_evidence()
            second._commit_mo4_edit_with_confidence(0.55)
            queued = second.service.evidence_review_items("reviewer-two")
            self.assertEqual(1, len(queued))
            confidence_change = next(
                item for item in queued[0]["change_set"]
                if item["type"] == "mapping_confidence_changed"
            )
            self.assertEqual(25.0, confidence_change["confidence_delta_percentage_points"])
        finally:
            second.tearDown()

    def test_classification_change_queues_evidence_reassessment(self) -> None:
        self._accepted_mo4_evidence()
        draft = self.service.start_logic_model_draft("user-admin")
        mo4 = next(item for item in draft["elements"] if item["stable_id"] == "MO4")
        mo4["label"] = f'{mo4["label"]} in participating services'
        self.service.save_logic_model_draft(draft, "user-admin")
        demo = DemoModelAdapter()

        def changed_classification(statement, elements, **kwargs):
            target = next(item for item in elements if item.get("stable_id") == "MO4")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification="Output",
                result_direction=None,
                outcome_category=None,
                outcome_change_types=[],
                mapping_decisions=[MappingDecision(target["id"], 0.80, "Same mapping.")],
            )

        with mock.patch.object(
            self.service.model, "analyze", side_effect=changed_classification
        ):
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        item = self.service.evidence_review_items("reviewer-two")[0]
        change = next(
            value for value in item["change_set"]
            if value["type"] == "classification_changed"
        )
        self.assertEqual("Outcome", change["previous_value"])
        self.assertEqual("Output", change["proposed_value"])

    def test_edited_node_can_add_mapping_without_removing_persisting_mapping(self) -> None:
        self._accepted_mo4_evidence()
        draft = self.service.start_logic_model_draft("user-admin")
        so2 = next(item for item in draft["elements"] if item["stable_id"] == "SO2")
        so2["label"] = f'{so2["label"]} in participating services'
        self.service.save_logic_model_draft(draft, "user-admin")
        demo = DemoModelAdapter()

        def add_mapping(statement, elements, **kwargs):
            target = next(item for item in elements if item.get("stable_id") == "SO2")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[MappingDecision(target["id"], 0.72, "Additional match.")],
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=add_mapping):
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        item = self.service.evidence_review_items("reviewer-two")[0]
        self.assertEqual(
            {"MO4", "SO2"},
            {mapping["stable_id"] for mapping in item["suggested_mappings"]},
        )
        self.assertIn("mapping_added", {value["type"] for value in item["change_set"]})

    def test_logic_model_update_adds_half_confidence_as_supported_mapping(self) -> None:
        self._accepted_mo4_evidence()
        draft = self.service.start_logic_model_draft("user-admin")
        so2 = next(item for item in draft["elements"] if item["stable_id"] == "SO2")
        so2["label"] = f'{so2["label"]} in participating services'
        self.service.save_logic_model_draft(draft, "user-admin")
        demo = DemoModelAdapter()

        def weak_addition(statement, elements, **kwargs):
            target = next(item for item in elements if item.get("stable_id") == "SO2")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[
                    MappingDecision(target["id"], 0.50, "Exactly even confidence.")
                ],
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=weak_addition):
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        review_items = self.service.evidence_review_items("reviewer-two")
        self.assertEqual(1, len(review_items))
        self.assertIn(
            "SO2",
            {value["stable_id"] for value in review_items[0]["suggested_mappings"]},
        )
        current = self.service.evidence()[0]
        self.assertEqual(["MO4"], [value["stable_id"] for value in current["mappings"]])

    def test_evidence_suggestion_at_half_is_shown_and_accepted(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        with self.database.connection() as connection:
            details = json.loads(
                connection.execute(
                    "SELECT suggested_mapping_details FROM evidence_assessments WHERE id = ?",
                    (item["id"],),
                ).fetchone()["suggested_mapping_details"]
            )
            details[0]["confidence"] = 0.50
            connection.execute(
                "UPDATE evidence_assessments SET suggested_mapping_details = ? WHERE id = ?",
                (json.dumps(details), item["id"]),
            )

        pending = self.service.evidence_review_items("reviewer-two")[0]
        self.assertEqual(1, len(pending["suggested_mappings"]))
        self.assertEqual("supported", pending["suggested_mappings"][0]["confidence_tier"])
        self.service.decide_evidence_assessment(
            pending["id"],
            {"action": "accept", "suggestion_identity": pending["suggestion_identity"]},
            "reviewer-two",
        )
        self.assertEqual(1, len(self.service.evidence()[0]["mappings"]))

    def test_evidence_reassessment_accept_is_scored_and_stale_identity_is_rejected(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        with self.assertRaises(DomainError) as stale:
            self.service.decide_evidence_assessment(
                item["id"], {"action": "accept", "suggestion_identity": "stale"}, "reviewer-two"
            )
        self.assertEqual(409, stale.exception.status)
        result = self.service.decide_evidence_assessment(
            item["id"],
            {"action": "accept", "suggestion_identity": item["suggestion_identity"]},
            "reviewer-two",
        )
        self.assertEqual("approved", result["status"])
        with self.database.connection() as connection:
            score = connection.execute(
                "SELECT cycle_type, first_review_approval FROM scoring_events "
                "WHERE evidence_assessment_id = ?",
                (item["id"],),
            ).fetchone()
        self.assertEqual("evidence_reassessment", score["cycle_type"])
        self.assertEqual(1, score["first_review_approval"])
        current_evidence = self.service.evidence()[0]
        self.assertEqual("2.0", current_evidence["analysis"]["logic_model_version"])
        self.assertEqual(0.55, current_evidence["mappings"][0]["confidence"])
        metrics = self.service.workspace_metrics()
        self.assertEqual(2, metrics["scored_reviews"])
        self.assertEqual(
            {"statement": 1, "evidence_reassessment": 1}, metrics["review_cycles"]
        )
        self.assertEqual(1, metrics["current_evidence"])

    def test_evidence_reassessment_change_is_authoritative_and_scored(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        replacement = next(
            value for value in item["available_mappings"] if value["stable_id"] == "SO2"
        )
        result = self.service.decide_evidence_assessment(
            item["id"],
            {
                "action": "change",
                "suggestion_identity": item["suggestion_identity"],
                "classification": "Outcome",
                "mapping_ids": [replacement["id"]],
                "feedback": "This is the authoritative current mapping.",
            },
            "reviewer-two",
        )
        self.assertEqual("approved", result["status"])
        current = self.service.evidence()[0]
        self.assertEqual(["SO2"], [value["stable_id"] for value in current["mappings"]])
        self.assertEqual(1.0, current["mappings"][0]["confidence"])
        with self.database.connection() as connection:
            score = connection.execute(
                "SELECT first_review_approval, correct_mapping FROM scoring_events "
                "WHERE evidence_assessment_id = ?",
                (item["id"],),
            ).fetchone()
        self.assertEqual(0, score["first_review_approval"])
        self.assertEqual(0, score["correct_mapping"])

    def test_evidence_direction_change_is_versioned_persistent_and_scored(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        self.assertEqual("Positive", item["accepted_result_direction"])
        self.assertEqual("Positive", item["suggested_result_direction"])

        self.service.decide_evidence_assessment(
            item["id"],
            {
                "action": "change",
                "suggestion_identity": item["suggestion_identity"],
                "classification": "Outcome",
                "result_direction": "Negative",
                "mapping_ids": [value["id"] for value in item["suggested_mappings"]],
            },
            "reviewer-two",
        )
        current = self.service.evidence()[0]
        self.assertEqual("Negative", current["analysis"]["result_direction"])
        with self.database.connection() as connection:
            assessment = connection.execute(
                "SELECT confirmed_result_direction FROM evidence_assessments WHERE id = ?",
                (item["id"],),
            ).fetchone()
            score = connection.execute(
                "SELECT first_review_approval, correct_level, correct_direction, "
                "correct_mapping FROM scoring_events WHERE evidence_assessment_id = ?",
                (item["id"],),
            ).fetchone()
        self.assertEqual("Negative", assessment["confirmed_result_direction"])
        self.assertEqual(0, score["first_review_approval"])
        self.assertEqual(1, score["correct_level"])
        self.assertEqual(0, score["correct_direction"])
        self.assertEqual(1, score["correct_mapping"])

        restarted = MelvanService(self.database, DemoModelAdapter(), FIXTURE)
        restarted.initialize()
        self.assertEqual(
            "Negative", restarted.evidence()[0]["analysis"]["result_direction"]
        )

    def test_revised_evidence_suggestion_advances_identity_and_remains_pending(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        demo = DemoModelAdapter()

        def revised(statement, elements, **kwargs):
            target = next(value for value in elements if value.get("stable_id") == "MO4")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[MappingDecision(target["id"], 0.50, "Revised suggestion.")],
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=revised):
            updated = self.service.decide_evidence_assessment(
                item["id"],
                {
                    "action": "revise",
                    "suggestion_identity": item["suggestion_identity"],
                    "feedback": "Reconsider the relationship to the edited node.",
                },
                "reviewer-two",
            )
        self.assertEqual("pending", updated["status"])
        self.assertEqual(2, updated["suggestion_version"])
        self.assertNotEqual(item["suggestion_identity"], updated["suggestion_identity"])
        with self.assertRaises(DomainError) as stale:
            self.service.decide_evidence_assessment(
                item["id"],
                {"action": "accept", "suggestion_identity": item["suggestion_identity"]},
                "reviewer-two",
            )
        self.assertEqual(409, stale.exception.status)

    def test_activation_manifest_and_pending_reassessment_survive_restart(self) -> None:
        self._accepted_mo4_evidence()
        committed = self._commit_mo4_edit_with_confidence(0.55)
        before = self.service.evidence_review_items("reviewer-two")[0]
        restarted = MelvanService(self.database, DemoModelAdapter(), FIXTURE)
        restarted.initialize()
        after = restarted.evidence_review_items("reviewer-two")[0]
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["change_set"], after["change_set"])
        activation = restarted.logic_model(committed["database_id"])["activation"]
        self.assertEqual("completed", activation["status"])
        self.assertEqual(
            before["id"],
            next(
                value["assessment_id"] for value in activation["manifest"]["records"]
                if value.get("outcome") == "new"
            ),
        )

    def test_version_specific_not_relevant_hides_then_restores_evidence_without_score(self) -> None:
        statement, original, _ = self._accepted_mo4_evidence()
        target = self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        self.service.decide_evidence_assessment(
            item["id"],
            {
                "action": "not_relevant",
                "suggestion_identity": item["suggestion_identity"],
                "feedback": "No longer applicable to the current program model.",
            },
            "reviewer-two",
        )
        self.assertEqual([], self.service.evidence())
        with self.database.connection() as connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM scoring_events WHERE evidence_assessment_id = ?",
                    (item["id"],),
                ).fetchone()[0],
            )
        self.service.activate_logic_model(
            original["database_id"], "user-admin", reason="restore"
        )
        self.assertEqual([statement["id"]], [value["id"] for value in self.service.evidence()])
        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("Approved target assessment should be reused."),
        ):
            self.service.activate_logic_model(
                target["database_id"], "user-admin", reason="restore"
            )
        self.assertEqual([], self.service.evidence())

    def test_evidence_created_after_version_change_is_assessed_on_roll_forward(self) -> None:
        self._accepted_mo4_evidence()
        target = self._commit_mo4_edit_with_confidence(0.55)
        queued = self.service.evidence_review_items("reviewer-two")[0]
        self.service.decide_evidence_assessment(
            queued["id"],
            {"action": "accept", "suggestion_identity": queued["suggestion_identity"]},
            "reviewer-two",
        )
        original = self.service.logic_model_versions()[-1]
        self.service.activate_logic_model(original["id"], "user-admin", reason="restore")
        later = self.service.create_statement(
            "Community partners are using evidence-informed practices in service planning.",
            "reporter-later",
        )
        self.service.accept_statement(later["id"], later["analysis"]["id"], "reviewer-one")
        demo = DemoModelAdapter()

        def reassess(statement, elements, **kwargs):
            target_element = next(item for item in elements if item.get("stable_id") == "MO4")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[MappingDecision(target_element["id"], 0.55, "Roll-forward result.")],
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=reassess):
            self.service.activate_logic_model(target["database_id"], "user-admin", reason="restore")
        items = self.service.evidence_review_items("reviewer-two")
        self.assertEqual([later["id"]], [item["statement_id"] for item in items])

    def test_unfinished_assessment_resumes_on_roll_forward(self) -> None:
        _, original, _ = self._accepted_mo4_evidence()
        target = self._commit_mo4_edit_with_confidence(0.55)
        pending = self.service.evidence_review_items("reviewer-two")[0]
        self.service.activate_logic_model(
            original["database_id"], "user-admin", reason="restore"
        )
        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("Unfinished exact assessment should resume."),
        ):
            restored = self.service.activate_logic_model(
                target["database_id"], "user-admin", reason="restore"
            )
        resumed = next(
            value for value in restored["activation"]["manifest"]["records"]
            if value.get("outcome") == "resumed"
        )
        self.assertEqual(pending["id"], resumed["assessment_id"])
        self.assertEqual(
            [pending["id"]],
            [value["id"] for value in self.service.evidence_review_items("reviewer-two")],
        )

    def test_latest_activation_manifest_wins_when_timestamps_match(self) -> None:
        model = self.service.logic_model()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO logic_model_activations "
                "(id, logic_model_id, actor_id, reason, activated_at, status, "
                "impact_manifest_json, completed_at) "
                "VALUES ('activation-first', ?, 'user-admin', 'restore', ?, "
                "'completed', '{\"marker\": \"first\"}', ?)",
                (model["database_id"], "2026-07-19T12:00:00+00:00", "2026-07-19T12:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO logic_model_activations "
                "(id, logic_model_id, actor_id, reason, activated_at, status, "
                "impact_manifest_json, completed_at) "
                "VALUES ('activation-second', ?, 'user-admin', 'restore', ?, "
                "'completed', '{\"marker\": \"second\"}', ?)",
                (model["database_id"], "2026-07-19T12:00:00+00:00", "2026-07-19T12:00:00+00:00"),
            )
        latest = self.service.logic_model()
        self.assertEqual("activation-second", latest["activation"]["id"])
        self.assertEqual("second", latest["activation"]["manifest"]["marker"])

    def test_reviewer_cannot_reassess_own_evidence(self) -> None:
        self._accepted_mo4_evidence()
        self._commit_mo4_edit_with_confidence(0.55)
        self.assertEqual([], self.service.evidence_review_items("reporter-one"))
        item = self.service.evidence_review_items("reviewer-two")[0]
        with self.assertRaises(DomainError) as denied:
            self.service.decide_evidence_assessment(
                item["id"],
                {"action": "accept", "suggestion_identity": item["suggestion_identity"]},
                "reporter-one",
            )
        self.assertEqual(403, denied.exception.status)

    def test_one_to_many_mapping_is_preserved_when_one_node_is_edited(self) -> None:
        _, _, assessment_id = self._accepted_mo4_evidence()
        with self.database.connection() as connection:
            second = connection.execute(
                "SELECT id FROM elements WHERE stable_id = 'SO2' AND logic_model_id IS NOT NULL"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO evidence_assessment_mappings
                    (assessment_id, element_id, rank, confidence, rationale)
                VALUES (?, ?, 2, 0.70, 'Second approved mapping.')
                """,
                (assessment_id, second["id"]),
            )
        self._commit_mo4_edit_with_confidence(0.55)
        item = self.service.evidence_review_items("reviewer-two")[0]
        self.assertEqual(
            {"MO4", "SO2"},
            {mapping["stable_id"] for mapping in item["suggested_mappings"]},
        )
        so2 = next(mapping for mapping in item["suggested_mappings"] if mapping["stable_id"] == "SO2")
        self.assertEqual(0.70, so2["confidence"])

    def test_deleted_mapping_records_explicit_no_suitable_match(self) -> None:
        self._accepted_mo4_evidence()
        draft = self.service.start_logic_model_draft("user-admin")
        draft["elements"] = [item for item in draft["elements"] if item["stable_id"] != "MO4"]
        draft["confirmed_deletions"] = ["MO4"]
        self.service.save_logic_model_draft(draft, "user-admin")
        demo = DemoModelAdapter()

        def no_match(statement, elements, **kwargs):
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[],
                match_status="no_suitable_match",
                no_match_rationale="No suitable current Logic Model match.",
                proposed_element=None,
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=no_match):
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        item = self.service.evidence_review_items("reviewer-two")[0]
        self.assertEqual([], item["suggested_mappings"])
        self.assertIn("no_suitable_match", {change["type"] for change in item["change_set"]})
        self.service.decide_evidence_assessment(
            item["id"],
            {"action": "accept", "suggestion_identity": item["suggestion_identity"]},
            "reviewer-two",
        )

    def test_failed_activation_is_retryable_and_current_pointer_is_atomic(self) -> None:
        self._accepted_mo4_evidence()
        draft = self.service.start_logic_model_draft("user-admin")
        mo4 = next(item for item in draft["elements"] if item["stable_id"] == "MO4")
        mo4["label"] = f'{mo4["label"]} in participating services'
        self.service.save_logic_model_draft(draft, "user-admin")
        original_id = self.service.logic_model()["database_id"]
        with mock.patch.object(self.service.model, "analyze", side_effect=ValueError("interrupted")):
            with self.assertRaises(DomainError):
                self.service.commit_logic_model_draft("user-admin", confirmed=True)
        self.assertEqual(original_id, self.service.logic_model()["database_id"])
        with self.database.connection() as connection:
            failed = connection.execute(
                "SELECT * FROM logic_model_activations WHERE status = 'failed'"
            ).fetchone()
        self.assertIsNotNone(failed)
        failed_manifest = json.loads(failed["impact_manifest_json"])
        self.assertEqual("failed", failed_manifest["outcome"])
        self.assertEqual("preparation_failed", failed_manifest["reason"])
        demo = DemoModelAdapter()

        def retry(statement, elements, **kwargs):
            target = next(item for item in elements if item.get("stable_id") == "MO4")
            result = demo.analyze(statement, elements, **kwargs)
            return replace(
                result,
                classification=kwargs["current_analysis"]["classification"],
                mapping_decisions=[MappingDecision(target["id"], 0.55, "Retry result.")],
                match_status="existing_match",
                no_match_rationale="",
                proposed_element=None,
            )

        with mock.patch.object(self.service.model, "analyze", side_effect=retry):
            restored = self.service.activate_logic_model(
                failed["logic_model_id"],
                "user-admin",
                reason="commit",
                activation_id=failed["id"],
            )
        self.assertEqual(failed["logic_model_id"], restored["database_id"])
        with self.database.connection() as connection:
            attempts = connection.execute(
                "SELECT status, impact_manifest_json, COUNT(*) AS total "
                "FROM logic_model_activations WHERE id = ?",
                (failed["id"],),
            ).fetchone()
        self.assertEqual("completed", attempts["status"])
        self.assertEqual(1, attempts["total"])
        self.assertTrue(json.loads(attempts["impact_manifest_json"])["retry"])
        with self.database.connection() as connection:
            audit_types = {
                row["event_type"]
                for row in connection.execute(
                    "SELECT event_type FROM audit_events WHERE entity_id = ?",
                    (failed["logic_model_id"],),
                ).fetchall()
            }
        self.assertIn("logic_model_activation_failed", audit_types)
        self.assertIn("logic_model_activation_retried", audit_types)
        self.assertIn("logic_model_activation_prepared", audit_types)

    def test_activating_current_logic_model_is_recorded_no_op(self) -> None:
        current = self.service.logic_model()
        with mock.patch.object(
            self.service.model,
            "analyze",
            side_effect=AssertionError("No-op activation invoked analysis."),
        ):
            result = self.service.activate_logic_model(
                current["database_id"],
                "user-admin",
                reason="restore",
                activation_id="activation-no-op",
            )
        self.assertEqual(current["database_id"], result["database_id"])
        self.assertEqual("no_op", result["activation"]["status"])
        self.assertEqual("target_already_current", result["activation"]["manifest"]["reason"])
        with self.database.connection() as connection:
            self.assertEqual(
                0,
                connection.execute("SELECT COUNT(*) FROM evidence_assessments").fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute("SELECT COUNT(*) FROM scoring_events").fetchone()[0],
            )

    def test_legacy_recap_lists_normalize_to_single_text_components(self) -> None:
        normalized = self.service._normalize_logic_model_snapshot(
            {
                "title": "Legacy",
                "recap": {
                    "risks": [
                        {"revision": 1, "text": "1. First risk"},
                        {"revision": 2, "text": "2. Second risk"},
                    ]
                },
                "elements": [],
            }
        )
        self.assertEqual("recap-risks", normalized["recap"]["risks"]["stable_id"])
        self.assertEqual(2, normalized["recap"]["risks"]["revision"])
        self.assertEqual(
            "1. First risk\n2. Second risk",
            normalized["recap"]["risks"]["text"],
        )
        self.assertEqual("", normalized["recap"]["people"]["text"])

    def test_draft_allows_incomplete_work_and_requires_confirmed_deletions(self) -> None:
        draft = self.service.start_logic_model_draft("user-admin")
        removed = draft["elements"].pop(0)
        draft["title"] = ""
        with self.assertRaises(DomainError) as deletion_error:
            self.service.save_logic_model_draft(draft, "user-admin")
        self.assertIn("Confirm every Logic Model element deletion", str(deletion_error.exception))

        draft["confirmed_deletions"] = [removed["stable_id"]]
        saved = self.service.save_logic_model_draft(draft, "user-admin")
        self.assertEqual("", saved["title"])
        with self.assertRaises(DomainError) as commit_error:
            self.service.commit_logic_model_draft("user-admin", confirmed=True)
        self.assertIn("Enter a Logic Model title", str(commit_error.exception))
        restarted = MelvanService(self.database, DemoModelAdapter(), FIXTURE)
        restarted.initialize()
        self.assertIsNotNone(restarted.logic_model_draft())


if __name__ == "__main__":
    unittest.main()
