from __future__ import annotations

import json
import unittest
import sys
from types import SimpleNamespace
from pathlib import Path

from melvan.config import Settings
from melvan.model import (
    AzureFoundryModelAdapter,
    DemoModelAdapter,
    ModelError,
    OpenAIModelAdapter,
    validate_result,
)


ELEMENTS = [
    {
        "id": "O1",
        "element_type": "Output",
        "level": "output",
        "label": "Capacity building completed",
    },
    {
        "id": "O2",
        "element_type": "Output",
        "level": "output",
        "label": "Implementation guidance published",
    },
]


def valid_payload() -> dict:
    return {
        "summary": "Capacity building was completed.",
        "classification": "Output",
        "classification_confidence": 0.9,
        "classification_rationale": "A completed deliverable.",
        "result_direction": "Positive",
        "outcome_category": None,
        "outcome_change_types": [],
        "match_status": "existing_match",
        "mapping_decisions": [
            {"element_id": "O1", "confidence": 0.85, "rationale": "Direct fit."}
        ],
        "no_match_rationale": "",
        "proposed_element": None,
    }


class ModelValidationTestCase(unittest.TestCase):
    @staticmethod
    def foundry_settings() -> Settings:
        return Settings(
            project_root=Path("."),
            database_path=Path("test.sqlite3"),
            host="127.0.0.1",
            port=8765,
            model_mode="foundry",
            model_name="test-gpt-5-mini",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            auth_refresh_command=None,
            azure_tenant_id="test-tenant",
        )

    def test_foundry_mode_requires_explicit_tenant(self) -> None:
        settings = Settings(
            project_root=Path("."),
            database_path=Path("test.sqlite3"),
            host="127.0.0.1",
            port=8765,
            model_mode="foundry",
            model_name="test-gpt-5-mini",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            auth_refresh_command=None,
            azure_tenant_id=None,
        )
        with self.assertRaisesRegex(ModelError, "AZURE_TENANT_ID is required"):
            AzureFoundryModelAdapter(settings)._build_credential()

    def test_demo_mode_does_not_silently_ignore_refresh_auth(self) -> None:
        with self.assertRaisesRegex(ModelError, "demo mode"):
            DemoModelAdapter().refresh_auth()

    def test_model_contract_makes_context_second_order(self) -> None:
        instructions = OpenAIModelAdapter._instructions()
        self.assertIn("statement as the primary source", instructions)
        self.assertIn("second-order supporting information", instructions)
        self.assertIn("must not override a clear Statement", instructions)
        self.assertIn("at least 0.25", instructions)
        self.assertIn("0.50 or more are supported", instructions)
        self.assertIn("no more than three tentative", instructions)

    def test_foundry_connectivity_check_completes_real_model_request(self) -> None:
        calls = []

        class Responses:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(status="completed")

        adapter = AzureFoundryModelAdapter(self.foundry_settings())
        adapter._client = SimpleNamespace(responses=Responses())
        adapter.verify_connectivity()
        self.assertEqual("test-gpt-5-mini", calls[0]["model"])
        self.assertEqual("Respond with OK.", calls[0]["input"])

    def test_foundry_connectivity_failure_is_actionable(self) -> None:
        class Responses:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("deployment unavailable")

        adapter = AzureFoundryModelAdapter(self.foundry_settings())
        adapter._client = SimpleNamespace(responses=Responses())
        with self.assertRaisesRegex(ModelError, "deployment connectivity check failed"):
            adapter.verify_connectivity()

    def test_foundry_analysis_uses_bounded_fast_response_contract(self) -> None:
        calls = []

        class Responses:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    status="completed",
                    output_text=json.dumps(valid_payload()),
                )

        adapter = AzureFoundryModelAdapter(self.foundry_settings())
        adapter._client = SimpleNamespace(responses=Responses())
        result = adapter.analyze("Capacity building was completed.", ELEMENTS)
        self.assertEqual("Output", result.classification)
        request = calls[0]
        self.assertEqual({"effort": "minimal"}, request["reasoning"])
        self.assertEqual(800, request["max_output_tokens"])
        self.assertEqual("low", request["text"]["verbosity"])

    def test_schema_enumerates_canonical_outcome_change_types(self) -> None:
        schema = OpenAIModelAdapter._schema()
        change_types = schema["properties"]["outcome_change_types"]["items"]["enum"]
        self.assertIn("Attitude", change_types)
        self.assertIn("Practice", change_types)
        self.assertNotIn("Satisfaction", change_types)
        mapping = schema["properties"]["mapping_decisions"]["items"]
        self.assertEqual(["element_id", "confidence", "rationale"], mapping["required"])

    def test_valid_existing_match(self) -> None:
        result = validate_result(valid_payload(), ELEMENTS, "gpt-5-mini")
        self.assertEqual(["O1"], result.match_ids)
        self.assertEqual(0.85, result.mapping_decisions[0].confidence)
        self.assertEqual("Direct fit.", result.mapping_decisions[0].rationale)

    def test_each_mapping_retains_independent_confidence_and_rationale(self) -> None:
        payload = valid_payload()
        payload["mapping_decisions"].append(
            {
                "element_id": "O2",
                "confidence": 0.42,
                "rationale": "The Statement only indirectly supports this relationship.",
            }
        )
        result = validate_result(payload, ELEMENTS, "gpt-5-mini")
        self.assertEqual([0.85, 0.42], [item.confidence for item in result.mapping_decisions])
        self.assertEqual(
            ["Direct fit.", "The Statement only indirectly supports this relationship."],
            [item.rationale for item in result.mapping_decisions],
        )

    def test_rejects_unknown_mapping(self) -> None:
        payload = valid_payload()
        payload["mapping_decisions"][0]["element_id"] = "UNKNOWN"
        with self.assertRaises(ModelError):
            validate_result(payload, ELEMENTS, "gpt-5-mini")

    def test_rejects_proposal_when_existing_match_exists(self) -> None:
        payload = valid_payload()
        payload["proposed_element"] = {
            "element_type": "Output",
            "label": "A new output",
            "reason": "Low confidence",
        }
        with self.assertRaises(ModelError):
            validate_result(payload, ELEMENTS, "gpt-5-mini")

    def test_outcome_requires_category_and_change_type(self) -> None:
        payload = valid_payload()
        payload.update({"classification": "Outcome", "outcome_category": None})
        with self.assertRaises(ModelError):
            validate_result(payload, ELEMENTS, "gpt-5-mini")

    def test_only_outcomes_retain_and_require_result_direction(self) -> None:
        payload = valid_payload()
        output = validate_result(payload, ELEMENTS, "gpt-5-mini")
        self.assertIsNone(output.result_direction)

        payload.update(
            {
                "classification": "Outcome",
                "result_direction": None,
                "outcome_category": "C",
                "outcome_change_types": ["Attitude"],
            }
        )
        with self.assertRaisesRegex(ModelError, "result direction"):
            validate_result(payload, ELEMENTS, "gpt-5-mini")
        payload["result_direction"] = "Negative"
        result = validate_result(payload, ELEMENTS, "gpt-5-mini")
        self.assertEqual("Negative", result.result_direction)

    def test_activity_transport_direction_is_not_persisted(self) -> None:
        payload = valid_payload()
        payload.update(
            {
                "classification": "Activity",
                "result_direction": "Positive",
                "match_status": "no_suitable_match",
                "mapping_decisions": [],
                "no_match_rationale": "No suitable Output match.",
            }
        )
        result = validate_result(payload, ELEMENTS, "gpt-5-mini")
        self.assertIsNone(result.result_direction)

    def test_result_direction_schema_is_never_nullable(self) -> None:
        schema = OpenAIModelAdapter._schema()
        direction = schema["properties"]["result_direction"]
        self.assertEqual("string", direction["type"])
        self.assertEqual(["Positive", "Negative"], direction["enum"])

    def test_model_contract_prohibits_logic_model_proposals(self) -> None:
        schema = OpenAIModelAdapter._schema()
        self.assertEqual("null", schema["properties"]["proposed_element"]["type"])

        payload = valid_payload()
        payload.update(
            {
                "match_status": "no_suitable_match",
                "mapping_decisions": [],
                "no_match_rationale": "No approved Output match.",
                "proposed_element": {
                    "element_type": "Output",
                    "label": "A new output",
                    "reason": "No current match.",
                },
            }
        )
        with self.assertRaisesRegex(ModelError, "cannot propose or create"):
            validate_result(payload, ELEMENTS, "gpt-5-mini")

    def test_refresh_command_can_supply_in_memory_token(self) -> None:
        command = f'''{sys.executable} -c "print('{{\\\"access_token\\\": \\\"refreshed-token\\\"}}')"'''
        settings = Settings(
            project_root=Path("."),
            database_path=Path("test.sqlite3"),
            host="127.0.0.1",
            port=8765,
            model_mode="openai",
            model_name="gpt-5-mini",
            openai_api_key=None,
            openai_base_url="https://api.openai.com/v1",
            auth_refresh_command=command,
        )
        adapter = OpenAIModelAdapter(settings)
        adapter.refresh_auth()
        self.assertEqual("Bearer refreshed-token", adapter._headers()["Authorization"])


if __name__ == "__main__":
    unittest.main()
