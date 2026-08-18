from __future__ import annotations

import base64
import io
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from melvan.db import Database
from melvan.model import DemoModelAdapter
from melvan.pdf_export import build_evidence_pdf
from melvan.service import MelvanService


FIXTURE = Path(__file__).resolve().parents[1] / "src" / "melvan" / "fixtures" / "logic_model.json"


def evidence_record(
    identifier: str,
    statement: str,
    classification: str,
    level: str | None,
    *,
    accepted_at: str = "2026-07-19T12:00:00+00:00",
) -> dict:
    mappings = [] if level is None else [{
        "id": f"element-{identifier}",
        "display_id": identifier,
        "label": f"Mapped element for {identifier}",
        "level": level,
        "confidence": 1.0,
    }]
    return {
        "id": f"statement-{identifier}",
        "original_text": statement,
        "analysis": {
            "classification": classification,
            "classification_confidence": 0.91,
            "summary": f"Summary for {statement}",
            "result_direction": "Positive" if classification == "Outcome" else None,
            "outcome_change_types": ["Use", "Behaviour"] if classification == "Outcome" else [],
            "classification_rationale": f"Rationale for {identifier}",
            "logic_model_version": "3.0",
        },
        "mappings": mappings,
        "context": {"text": f"Context for {identifier}"},
        "evidence": {
            "accepted_by": "reviewer-one",
            "accepted_at": accepted_at,
        },
    }


class EvidencePDFTestCase(unittest.TestCase):
    def test_empty_export_is_a_valid_informative_pdf(self) -> None:
        content = build_evidence_pdf(
            [],
            logic_model_title="Demo Logic Model",
            logic_model_version="3.0",
            generated_at="2026-07-19T12:00:00+00:00",
        )
        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"No Evidence available", content)
        self.assertIn(b"Page 1 of 1", content)

    def test_export_is_ungrouped_newest_first_and_excludes_confidence(self) -> None:
        records = [
            evidence_record(
                "OLD",
                "Older evidence",
                "Outcome",
                "short_term_outcome",
                accepted_at="2026-07-18T12:00:00+00:00",
            ),
            evidence_record(
                "NEW",
                "Newest evidence",
                "Outcome",
                "long_term_outcome",
                accepted_at="2026-07-20T12:00:00+00:00",
            ),
        ]
        content = build_evidence_pdf(
            records,
            logic_model_title="Demo Logic Model",
            logic_model_version="3.0",
            generated_at="2026-07-19T12:00:00+00:00",
            user_names={"reviewer-one": "Reva Reviewer"},
        )
        self.assertLess(content.index(b"Newest evidence"), content.index(b"Older evidence"))
        self.assertNotIn(b"Long-term Outcomes", content)
        self.assertNotIn(b"Short-term Outcomes", content)
        self.assertNotIn(b"confidence", content.lower())
        self.assertIn(b"Reva Reviewer", content)
        self.assertIn(b"CLASSIFICATION", content)
        self.assertIn(b"Outcome | Positive | Category: Use, Behaviour", content)
        self.assertIn(b"CLASSIFICATION RATIONALE", content)

    def test_export_embeds_attachment_as_grayscale_image(self) -> None:
        image = Image.new("RGB", (120, 60), "#d5241e")
        source = io.BytesIO()
        image.save(source, format="JPEG")
        record = evidence_record(
            "PHOTO",
            "Photographic evidence",
            "Output",
            "output",
        )
        record["attachment_content"] = source.getvalue()

        content = build_evidence_pdf(
            [record],
            logic_model_title="Demo Logic Model",
            logic_model_version="3.0",
            generated_at="2026-07-19T12:00:00+00:00",
        )

        self.assertIn(b"ATTACHMENT", content)
        self.assertIn(b"/Subtype /Image", content)
        self.assertIn(b"/ColorSpace /DeviceGray", content)
        self.assertIn(b"/Im1 Do", content)

    def test_export_uses_only_grayscale_drawing_colors(self) -> None:
        content = build_evidence_pdf(
            [evidence_record("BW", "Black and white evidence", "Output", "output")],
            logic_model_title="Demo Logic Model",
            logic_model_version="3.0",
            generated_at="2026-07-19T12:00:00+00:00",
        )
        colors = re.findall(
            rb"([01]\.[0-9]{3}) ([01]\.[0-9]{3}) ([01]\.[0-9]{3}) (?:rg|RG)",
            content,
        )
        self.assertTrue(colors)
        self.assertTrue(all(red == green == blue for red, green, blue in colors))

    def test_long_export_paginates_with_numbered_footers(self) -> None:
        records = [
            evidence_record(
                f"S{index}",
                "A detailed accepted Statement " + ("with sufficient wording to wrap across the page " * 4),
                "Outcome",
                "short_term_outcome",
            )
            for index in range(24)
        ]
        content = build_evidence_pdf(
            records,
            logic_model_title="Demo Logic Model",
            logic_model_version="3.0",
            generated_at="2026-07-19T12:00:00+00:00",
        )
        page_count = len(re.findall(rb"/Type /Page\b", content))
        self.assertGreater(page_count, 1)
        self.assertIn(f"Page 1 of {page_count}".encode(), content)
        self.assertIn(f"Page {page_count} of {page_count}".encode(), content)

    def test_service_export_is_audited_and_contains_accepted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database = Database(Path(tempdir) / "melvan.sqlite3")
            service = MelvanService(
                database,
                DemoModelAdapter(),
                FIXTURE,
                initial_admin_email="owner@example.org",
            )
            service.initialize()
            statement = service.create_statement(
                "Care providers increased use of the new practice.", "reporter-one"
            )
            service.accept_statement(
                statement["id"], statement["analysis"]["id"], "reviewer-one"
            )
            exported = service.export_evidence_pdf("admin-default")
            self.assertEqual(1, exported["record_count"])
            self.assertIn(b"Care providers increased use", exported["content"])
            with database.connection() as connection:
                audit = connection.execute(
                    "SELECT details_json FROM audit_events WHERE event_type = 'evidence_pdf_exported'"
                ).fetchone()
            self.assertIsNotNone(audit)

    def test_service_export_loads_and_embeds_evidence_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database = Database(Path(tempdir) / "melvan.sqlite3")
            service = MelvanService(
                database,
                DemoModelAdapter(),
                FIXTURE,
                initial_admin_email="owner@example.org",
            )
            service.initialize()
            image = Image.new("RGB", (80, 40), "white")
            source = io.BytesIO()
            image.save(source, format="PNG")
            staged = service.stage_photo(
                base64.b64encode(source.getvalue()).decode("ascii"),
                "library",
                "reporter-one",
                "session-one",
            )
            statement = service.create_statement(
                "This photo records a completed program workshop.",
                "reporter-one",
                capture={
                    "method": "photo",
                    "stage_id": staged["stage_id"],
                    "candidate_text": staged["candidate_text"],
                    "edited": True,
                    "provider": staged["provider"],
                    "provider_version": staged["provider_version"],
                    "processing_ms": staged["processing_ms"],
                    "outcome": staged["outcome"],
                },
                session_fingerprint="session-one",
            )
            service.accept_statement(
                statement["id"], statement["analysis"]["id"], "reviewer-one"
            )

            exported = service.export_evidence_pdf("admin-default")

            self.assertIn(b"/Subtype /Image", exported["content"])
            self.assertIn(b"/ColorSpace /DeviceGray", exported["content"])


if __name__ == "__main__":
    unittest.main()
