"""Replaceable model adapters for Statement analysis."""

from __future__ import annotations

import json
import base64
import math
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Settings


CLASSIFICATIONS = {"Activity", "Output", "Outcome", "Indicator"}
OUTCOME_CHANGE_TYPES = {
    "A": {"State", "Condition", "Wellbeing", "System performance", "Health result"},
    "B": {
        "Behaviour",
        "Practice",
        "Use",
        "Adoption",
        "Collaboration",
        "Integration",
        "Performance",
    },
    "C": {
        "Capacity",
        "Knowledge",
        "Skill",
        "Confidence",
        "Attitude",
        "Motivation",
        "Readiness",
        "Resources",
        "Ability",
    },
}
ALL_OUTCOME_CHANGE_TYPES = sorted(
    {change_type for values in OUTCOME_CHANGE_TYPES.values() for change_type in values}
)
PROMPT_VERSION = "statement-analysis-v4"


class ModelError(RuntimeError):
    """A visible, recoverable model integration failure."""


@dataclass(frozen=True)
class ProposedElement:
    element_type: str
    label: str
    reason: str


@dataclass(frozen=True)
class MappingDecision:
    element_id: str
    confidence: float
    rationale: str


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    classification: str
    classification_confidence: float
    classification_rationale: str
    result_direction: str | None
    outcome_category: str | None
    outcome_change_types: list[str]
    match_status: str
    mapping_decisions: list[MappingDecision]
    no_match_rationale: str
    proposed_element: ProposedElement | None
    model_version: str
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value

    @property
    def match_ids(self) -> list[str]:
        return [decision.element_id for decision in self.mapping_decisions]

    @property
    def mapping_confidence(self) -> float:
        if not self.mapping_decisions:
            return 0.0
        return sum(item.confidence for item in self.mapping_decisions) / len(self.mapping_decisions)

    @property
    def mapping_rationale(self) -> str:
        if not self.mapping_decisions:
            return self.no_match_rationale
        return " ".join(item.rationale for item in self.mapping_decisions)


class ModelAdapter:
    mode = "unknown"

    def analyze(
        self,
        statement: str,
        elements: list[dict[str, Any]],
        *,
        statement_context: str | None = None,
        current_analysis: dict[str, Any] | None = None,
        reviewer_comments: str | None = None,
        rejected_proposal: str | None = None,
        approved_examples: list[dict[str, Any]] | None = None,
    ) -> AnalysisResult:
        raise NotImplementedError

    def refresh_auth(self) -> None:
        return None

    def verify_connectivity(self) -> None:
        self.health_check()

    def health_check(self) -> dict[str, str]:
        raise NotImplementedError

    def structure_logic_model(
        self,
        source_name: str,
        extracted_text: str,
        *,
        image_bytes: bytes | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def extract_photo_text(
        self, image_bytes: bytes, media_type: str
    ) -> dict[str, str]:
        raise NotImplementedError


class DemoModelAdapter(ModelAdapter):
    """Deterministic local adapter used only when demo mode is explicit."""

    mode = "demo"
    model_version = "melvan-demo-classifier-v1"

    def refresh_auth(self) -> None:
        raise ModelError(
            "Credential refresh was requested, but MELvan is running in demo mode. "
            "Use MELVAN_MODEL_MODE=foundry for Azure GPT-5 mini analysis."
        )

    def analyze(
        self,
        statement: str,
        elements: list[dict[str, Any]],
        *,
        statement_context: str | None = None,
        current_analysis: dict[str, Any] | None = None,
        reviewer_comments: str | None = None,
        rejected_proposal: str | None = None,
        approved_examples: list[dict[str, Any]] | None = None,
    ) -> AnalysisResult:
        supporting_context = str(statement_context or "").strip()
        source = reviewer_comments.strip() if reviewer_comments else " ".join(
            part for part in (statement, statement, supporting_context) if part
        )
        classification, classification_confidence, classification_rationale = self._classify(
            source, current_analysis
        )
        summary = self._summarize(statement, reviewer_comments)
        category, change_types = self._outcome_details(source, classification)
        result_direction = self._result_direction(source, classification)
        matches = self._match(
            source,
            classification,
            elements,
        )

        if matches:
            match_status = "existing_match"
            mapping_decisions = [
                MappingDecision(
                    element_id=item[1]["id"],
                    confidence=item[0],
                    rationale=(
                        "The Statement has substantive semantic overlap with this specific "
                        "Logic Model element."
                    ),
                )
                for item in matches
            ]
            no_match_rationale = ""
        else:
            match_status = "no_suitable_match"
            mapping_decisions = []
            no_match_rationale = "No existing element has enough substantive overlap for a defensible placement."

        return AnalysisResult(
            summary=summary,
            classification=classification,
            classification_confidence=classification_confidence,
            classification_rationale=classification_rationale,
            result_direction=result_direction,
            outcome_category=category,
            outcome_change_types=change_types,
            match_status=match_status,
            mapping_decisions=mapping_decisions,
            no_match_rationale=no_match_rationale,
            proposed_element=None,
            model_version=self.model_version,
        )

    def extract_photo_text(
        self, image_bytes: bytes, media_type: str
    ) -> dict[str, str]:
        return {
            "text": "",
            "provider": "melvan-demo",
            "provider_version": "photo-text-v1",
        }

    def health_check(self) -> dict[str, str]:
        return {"mode": self.mode, "model": self.model_version, "status": "ready"}

    def structure_logic_model(
        self,
        source_name: str,
        extracted_text: str,
        *,
        image_bytes: bytes | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        if not extracted_text.strip():
            raise ModelError("Image Logic Model ingestion requires live GPT-5 mini mode.")
        lines = [" ".join(line.split()) for line in extracted_text.splitlines() if line.strip()]
        title = lines[0] if lines else Path(source_name).stem
        elements: list[dict[str, Any]] = []
        counters = {"Output": 0, "Outcome": 0}
        current_type: str | None = None
        pattern = re.compile(r"^(O\d+|SO\d+|MO\d+|LO\d+|UG\d+)\s*[:\-–—]?\s*(.+)$", re.I)
        for line in lines:
            lowered = line.lower().rstrip(":")
            if lowered in {"output", "outputs", "enabling outputs"}:
                current_type = "Output"
                continue
            if lowered in {"outcome", "outcomes", "short-term outcomes", "medium-term outcomes", "long-term outcomes", "ultimate goal"}:
                current_type = "Outcome"
                continue
            match = pattern.match(line)
            if match:
                display_id, label = match.groups()
                display_id = display_id.upper()
                element_type, level = _logic_element_type_level(display_id)
            elif current_type and len(line) >= 12:
                counters[current_type] += 1
                display_id = f"O{counters[current_type]}" if current_type == "Output" else f"SO{counters[current_type]}"
                label = line
                element_type = current_type
                level = "output" if current_type == "Output" else "short_term_outcome"
            else:
                continue
            elements.append(
                {
                    "display_id": display_id,
                    "label": label.strip(),
                    "element_type": element_type,
                    "level": level,
                    "group": None,
                }
            )
        return validate_logic_model_structure({"title": title, "elements": elements}, source_name)

    @staticmethod
    def _classify(
        text: str, current_analysis: dict[str, Any] | None
    ) -> tuple[str, float, str]:
        lowered = text.lower()
        if re.search(r"\b(rate|ratio|percentage|percent|number of|proportion|indicator|measured)\b", lowered):
            return "Indicator", 0.86, "The Statement expresses a neutral measure or quantity."
        if re.search(
            r"\b(improved|increased|decreased|reduced|greater|better|change[sd]?|capacity|confidence|adopted|using|use of|integrated)\b",
            lowered,
        ):
            return "Outcome", 0.82, "The Statement describes a change in condition, behaviour, or capacity."
        if re.search(
            r"\b(completed|published|produced|created|developed|implemented|hired|delivered|established|launched)\b",
            lowered,
        ):
            return "Output", 0.81, "The Statement describes a completed deliverable or service."
        if re.search(
            r"\b(held|conducted|organized|organised|met|worked|trained|planned|reviewed|facilitated)\b",
            lowered,
        ):
            return "Activity", 0.78, "The Statement describes work performed by the program."
        if current_analysis:
            return (
                current_analysis["classification"],
                0.61,
                "The feedback does not establish a different RBM level, so the prior level is retained.",
            )
        return "Activity", 0.54, "The Statement describes program work but does not state a completed result."

    @staticmethod
    def _summarize(statement: str, reviewer_comments: str | None) -> str:
        cleaned = " ".join(statement.strip().split())
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        if reviewer_comments:
            comment = " ".join(reviewer_comments.strip().split())
            if re.search(r"\b(summary|rewrite|wording)\b", comment.lower()):
                return cleaned
        return cleaned

    @staticmethod
    def _outcome_details(text: str, classification: str) -> tuple[str | None, list[str]]:
        if classification != "Outcome":
            return None, []
        lowered = text.lower()
        if re.search(r"\b(use|using|adopt|practice|collaborat|integrat|behavio|performance)\w*", lowered):
            return "B", ["Practice"]
        if re.search(r"\b(capacity|knowledge|skill|confidence|readiness|ability|resource)\w*", lowered):
            return "C", ["Capacity"]
        return "A", ["Condition"]

    @staticmethod
    def _result_direction(text: str, classification: str) -> str | None:
        if classification != "Outcome":
            return None
        if re.search(
            r"\b(not impressed|dissatisfied|failed|failure|worse|negative|poor|resistance|concerned)\b",
            text.lower(),
        ):
            return "Negative"
        return "Positive"

    @classmethod
    def _match(
        cls, text: str, classification: str, elements: Iterable[dict[str, Any]]
    ) -> list[tuple[float, dict[str, Any]]]:
        source_tokens = cls._tokens(text)
        preferred_type = "Output" if classification in {"Activity", "Output"} else "Outcome"
        scored: list[tuple[float, dict[str, Any]]] = []
        for element in elements:
            if element["element_type"] != preferred_type and classification != "Indicator":
                continue
            target_tokens = cls._tokens(element["label"])
            overlap = len(source_tokens & target_tokens)
            if not overlap:
                continue
            score = overlap / math.sqrt(max(1, len(source_tokens) * len(target_tokens)))
            scored.append((score, element))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or scored[0][0] < 0.12:
            return []
        best_score = scored[0][0]
        return [
            (min(0.92, 0.48 + score), element)
            for score, element in scored[:2]
            if score >= max(0.12, best_score * 0.78)
        ]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stopwords = {
            "a",
            "an",
            "and",
            "are",
            "at",
            "for",
            "from",
            "in",
            "into",
            "of",
            "on",
            "the",
            "to",
            "with",
            "more",
            "project",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) > 2 and token not in stopwords
        }
        expansions = {
            "clinicians": {"care", "providers"},
            "clinician": {"care", "provider"},
            "doctors": {"care", "providers"},
            "training": {"capacity", "building"},
            "trained": {"capacity", "building"},
            "used": {"use", "incorporation"},
            "using": {"use", "incorporation"},
            "adopted": {"adoption", "incorporation"},
            "patient": {"care", "health"},
            "patients": {"care", "health"},
        }
        for token in list(tokens):
            tokens.update(expansions.get(token, set()))
        return tokens

    @staticmethod
    def _proposal_label(summary: str, element_type: str) -> str:
        text = summary.rstrip(".")
        if element_type == "Output":
            return text[0].upper() + text[1:] if text else "New program output"
        if re.match(r"(?i)improved|increased|reduced|greater|better", text):
            return text[0].upper() + text[1:]
        return f"Improved result associated with: {text}"


class OpenAIModelAdapter(ModelAdapter):
    mode = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.openai_api_key

    def refresh_auth(self) -> None:
        command = self.settings.auth_refresh_command
        if not command:
            return
        try:
            completed = subprocess.run(
                shlex.split(command),
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ModelError(f"Credential refresh failed: {exc}") from exc
        output = completed.stdout.strip()
        if output:
            token = output.splitlines()[-1].strip()
            try:
                value = json.loads(output)
                if isinstance(value, dict):
                    token = str(
                        value.get("access_token")
                        or value.get("accessToken")
                        or value.get("token")
                        or ""
                    ).strip()
            except json.JSONDecodeError:
                pass
            if token:
                self.api_key = token

    def health_check(self) -> dict[str, str]:
        if not self.api_key:
            raise ModelError(
                "OPENAI_API_KEY is not configured. Set it in the environment or .env before using live mode."
            )
        request = urllib.request.Request(
            f"{self.settings.openai_base_url}/models/{self.settings.model_name}",
            headers=self._headers(),
            method="GET",
        )
        self._send(request)
        return {"mode": self.mode, "model": self.settings.model_name, "status": "ready"}

    def analyze(
        self,
        statement: str,
        elements: list[dict[str, Any]],
        *,
        statement_context: str | None = None,
        current_analysis: dict[str, Any] | None = None,
        reviewer_comments: str | None = None,
        rejected_proposal: str | None = None,
        approved_examples: list[dict[str, Any]] | None = None,
    ) -> AnalysisResult:
        if not self.api_key:
            raise ModelError("OPENAI_API_KEY is not configured for live GPT-5 mini analysis.")

        catalogue = [
            {
                "id": element["id"],
                "type": element["element_type"],
                "level": element["level"],
                "label": element["label"],
            }
            for element in elements
        ]
        context = {
            "statement": statement,
            "statement_context": statement_context,
            "existing_elements": catalogue,
            "current_analysis": current_analysis,
            "reviewer_comments": reviewer_comments,
            "rejected_proposal": rejected_proposal,
            "approved_examples": approved_examples or [],
        }
        body = {
            "model": self.settings.model_name,
            "store": False,
            "max_output_tokens": 800,
            "reasoning": {"effort": "minimal"},
            "instructions": self._instructions(),
            "input": json.dumps(context, ensure_ascii=True),
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "melvan_statement_analysis",
                    "strict": True,
                    "schema": self._schema(),
                }
            },
        }
        request = urllib.request.Request(
            f"{self.settings.openai_base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        response = self._send(request)
        raw_text = self._response_text(response)
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ModelError("The model returned content that was not valid JSON.") from exc
        return validate_result(payload, elements, self.settings.model_name)

    def structure_logic_model(
        self,
        source_name: str,
        extracted_text: str,
        *,
        image_bytes: bytes | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ModelError("OPENAI_API_KEY is not configured for Logic Model ingestion.")
        body = self._logic_model_request(
            source_name, extracted_text, image_bytes=image_bytes, media_type=media_type
        )
        request = urllib.request.Request(
            f"{self.settings.openai_base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        payload = json.loads(self._response_text(self._send(request)))
        return validate_logic_model_structure(payload, source_name)

    def extract_photo_text(
        self, image_bytes: bytes, media_type: str
    ) -> dict[str, str]:
        if not self.api_key:
            raise ModelError("OPENAI_API_KEY is not configured for photo text extraction.")
        request = urllib.request.Request(
            f"{self.settings.openai_base_url}/responses",
            data=json.dumps(
                self._photo_text_request(image_bytes, media_type)
            ).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        payload = json.loads(self._response_text(self._send(request)))
        return {
            "text": str(payload.get("text") or "").strip(),
            "provider": "openai",
            "provider_version": self.settings.model_name,
        }

    def _photo_text_request(
        self, image_bytes: bytes, media_type: str
    ) -> dict[str, Any]:
        data_url = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        return {
            "model": self.settings.model_name,
            "store": False,
            "max_output_tokens": 2000,
            "reasoning": {"effort": "minimal"},
            "instructions": (
                "Transcribe only readable text visible in the supplied photo. "
                "Preserve wording and reading order. Do not describe, interpret, "
                "classify, identify people, or infer any visual attribute. Return an "
                "empty string when no readable text is present."
            ),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Transcribe the readable text in this photo.",
                        },
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "melvan_photo_text",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            },
        }

    def _logic_model_request(
        self,
        source_name: str,
        extracted_text: str,
        *,
        image_bytes: bytes | None,
        media_type: str | None,
    ) -> dict[str, Any]:
        prompt = (
            f"Source filename: {source_name}\n"
            "Extract the Logic Model title and every distinct Output and Outcome. Preserve "
            "the source wording. Use source notation such as O1, SO1, MO4, LO5, or UG1 when "
            "present; otherwise assign concise unique notation. Do not assess framework quality.\n\n"
            f"Extracted text:\n{extracted_text[:100000]}"
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        if image_bytes and media_type:
            data_url = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            content.append({"type": "input_image", "image_url": data_url})
        return {
            "model": self.settings.model_name,
            "store": False,
            "max_output_tokens": 4000,
            "reasoning": {"effort": "minimal"},
            "instructions": "You extract structured Logic Models for MELvan. Return only the required schema.",
            "input": [{"role": "user", "content": content}],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "melvan_logic_model",
                    "strict": True,
                    "schema": _logic_model_schema(),
                },
            },
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _instructions() -> str:
        return (
            "You are MELvan, a purpose-bound Results-Based Management classifier. "
            "Classify the Statement as Activity, Output, Outcome, or Indicator. "
            "An Activity is work that is planned, underway, or performed. An Output is a "
            "completed product, service, or deliverable directly produced by program work. "
            "An Outcome is an observed change in state, condition, behaviour, practice, use, "
            "capacity, knowledge, or a related result. An Indicator is a measure used to track "
            "an Output or Outcome. Intentions, efforts, requests, plans, and scheduled work, "
            "including language such as seeking to do something, are not Outcomes unless the "
            "Statement also reports that a change has actually occurred. "
            "Always set result_direction to Positive or Negative for an Outcome. The transport "
            "requires a value, but MELvan ignores it for Activity, Output, and Indicator. "
            "Positive means the reported result supports the intended program direction; "
            "Negative means it reports resistance, dissatisfaction, deterioration, failure, "
            "an adverse effect, or movement away from the intended direction. Direction does "
            "not determine Logic Model mapping. "
            "For Outcomes, use only these exact category and change-type values: "
            "A: State, Condition, Wellbeing, System performance, Health result; "
            "B: Behaviour, Practice, Use, Adoption, Collaboration, Integration, Performance; "
            "C: Capacity, Knowledge, Skill, Confidence, Attitude, Motivation, Readiness, "
            "Resources, Ability. A change type must belong to the selected category. "
            "For Activity, Output, or Indicator, set outcome_category to null and "
            "outcome_change_types to an empty array. "
            "Search the supplied existing "
            "Outputs and Outcomes first using semantic and causal fit. Use one or more existing "
            "IDs whenever defensible. For every selected element, return an independent confidence "
            "score and a brief rationale specific to that relationship; do not assign one aggregate "
            "confidence to multiple mappings. Return a mapping decision only when its confidence is "
            "at least 0.25. Scores from 0.25 up to but not including 0.50 are tentative hypotheses; "
            "scores of 0.50 or more are supported mappings. Return no more than three tentative "
            "mapping decisions. Return no_suitable_match when no suitable existing Logic Model "
            "element exists. Never propose or invent an Output, Outcome, Working Outcome, or "
            "Logic Model ID; only an Administrator can change the Logic Model. Reviewer comments "
            "are prioritized instructions. Do not invent "
            "facts, framework IDs, or uses outside MELvan's stated purpose."
            " approved_examples are separately labelled, Reviewer-verified case precedents. "
            "Use relevant examples as guidance, including negative counterexamples, but assess "
            "the new Statement independently against its wording and the current Logic Model. "
            "Never copy a precedent's confidence or propose an unavailable element."
            " Treat statement as the primary source for classification, summary, and mapping. "
            "Treat statement_context as separately labelled, second-order supporting information: "
            "it may resolve ambiguity or add useful circumstances, but it must not override a clear "
            "Statement or be quoted or presented as part of the immutable Statement."
        )

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "classification": {"type": "string", "enum": sorted(CLASSIFICATIONS)},
                "classification_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "classification_rationale": {"type": "string"},
                "result_direction": {
                    "type": "string",
                    "enum": ["Positive", "Negative"],
                },
                "outcome_category": {"type": ["string", "null"], "enum": ["A", "B", "C", None]},
                "outcome_change_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ALL_OUTCOME_CHANGE_TYPES},
                },
                "match_status": {
                    "type": "string",
                    "enum": ["existing_match", "no_suitable_match"],
                },
                "mapping_decisions": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "element_id": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "rationale": {"type": "string"},
                        },
                        "required": ["element_id", "confidence", "rationale"],
                    },
                },
                "no_match_rationale": {"type": "string"},
                "proposed_element": {"type": "null"},
            },
            "required": [
                "summary",
                "classification",
                "classification_confidence",
                "classification_rationale",
                "result_direction",
                "outcome_category",
                "outcome_change_types",
                "match_status",
                "mapping_decisions",
                "no_match_rationale",
                "proposed_element",
            ],
        }

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body).get("error", {}).get("message", body)
            except json.JSONDecodeError:
                detail = body
            raise ModelError(f"Model endpoint returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelError(f"Could not reach or read the model endpoint: {exc}") from exc

    @staticmethod
    def _response_text(response: dict[str, Any]) -> str:
        for output in response.get("output", []):
            if output.get("type") != "message":
                continue
            for content in output.get("content", []):
                if content.get("type") == "refusal":
                    raise ModelError(f"The model refused the Statement: {content.get('refusal', '')}")
                if content.get("type") == "output_text":
                    return content.get("text", "")
        raise ModelError("The model response did not contain structured output text.")


class AzureFoundryModelAdapter(OpenAIModelAdapter):
    """MELvan-owned adapter for the shared Azure Foundry infrastructure."""

    mode = "foundry"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = None
        self._credential: Any = None
        self._project: Any = None
        self._client: Any = None
        self._token: Any = None

    def refresh_auth(self) -> None:
        credential = self._build_credential()
        print(
            "Checking Azure credentials "
            f"for tenant {self.settings.azure_tenant_id} using {self._auth_description()}...",
            flush=True,
        )
        try:
            token = credential.get_token(self.settings.azure_token_scope)
        except Exception as exc:  # Azure Identity exposes several credential-specific failures.
            raise ModelError(f"Azure credential acquisition failed: {exc}") from exc
        self._credential = credential
        self._token = token
        expires_at = datetime.fromtimestamp(token.expires_on, timezone.utc).isoformat()
        print(f"Azure credentials ready until {expires_at}.", flush=True)

    def health_check(self) -> dict[str, str]:
        self._ensure_client()
        return {"mode": self.mode, "model": self.settings.model_name, "status": "ready"}

    def verify_connectivity(self) -> None:
        """Prove that the configured deployment can complete a real request."""
        self._ensure_client()
        try:
            response = self._client.responses.create(
                model=self.settings.model_name,
                store=False,
                input="Respond with OK.",
                max_output_tokens=128,
            )
        except Exception as exc:
            raise ModelError(
                "Azure Foundry deployment connectivity check failed: "
                f"{exc}"
            ) from exc
        if getattr(response, "status", None) != "completed":
            detail = getattr(response, "incomplete_details", None) or getattr(
                response, "status", "unknown"
            )
            raise ModelError(
                "Azure Foundry deployment connectivity check did not complete: "
                f"{detail}"
            )
        print(
            f"Azure Foundry deployment ready: {self.settings.model_name}.",
            flush=True,
        )

    def analyze(
        self,
        statement: str,
        elements: list[dict[str, Any]],
        *,
        statement_context: str | None = None,
        current_analysis: dict[str, Any] | None = None,
        reviewer_comments: str | None = None,
        rejected_proposal: str | None = None,
        approved_examples: list[dict[str, Any]] | None = None,
    ) -> AnalysisResult:
        self._ensure_client()
        catalogue = [
            {
                "id": element["id"],
                "type": element["element_type"],
                "level": element["level"],
                "label": element["label"],
            }
            for element in elements
        ]
        context = {
            "statement": statement,
            "statement_context": statement_context,
            "existing_elements": catalogue,
            "current_analysis": current_analysis,
            "reviewer_comments": reviewer_comments,
            "rejected_proposal": rejected_proposal,
            "approved_examples": approved_examples or [],
        }
        try:
            response = self._client.responses.create(
                model=self.settings.model_name,
                store=False,
                max_output_tokens=800,
                reasoning={"effort": "minimal"},
                instructions=self._instructions(),
                input=json.dumps(context, ensure_ascii=True),
                text={
                    "verbosity": "low",
                    "format": {
                        "type": "json_schema",
                        "name": "melvan_statement_analysis",
                        "strict": True,
                        "schema": self._schema(),
                    }
                },
            )
        except Exception as exc:
            raise ModelError(f"Azure Foundry model request failed: {exc}") from exc
        if getattr(response, "status", None) != "completed":
            detail = getattr(response, "incomplete_details", None) or getattr(
                response, "status", "unknown"
            )
            raise ModelError(f"Azure Foundry did not complete the analysis: {detail}")
        raw_text = getattr(response, "output_text", "")
        if not raw_text:
            raise ModelError("Azure Foundry returned no structured analysis text.")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ModelError("Azure Foundry returned content that was not valid JSON.") from exc
        return validate_result(payload, elements, self.settings.model_name)

    def structure_logic_model(
        self,
        source_name: str,
        extracted_text: str,
        *,
        image_bytes: bytes | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_client()
        request = self._logic_model_request(
            source_name, extracted_text, image_bytes=image_bytes, media_type=media_type
        )
        request.pop("model", None)
        try:
            response = self._client.responses.create(model=self.settings.model_name, **request)
        except Exception as exc:
            raise ModelError(f"Azure Foundry Logic Model ingestion failed: {exc}") from exc
        if getattr(response, "status", None) != "completed" or not getattr(response, "output_text", ""):
            raise ModelError("Azure Foundry did not return a complete Logic Model extraction.")
        try:
            payload = json.loads(response.output_text)
        except json.JSONDecodeError as exc:
            raise ModelError("Azure Foundry returned invalid Logic Model JSON.") from exc
        return validate_logic_model_structure(payload, source_name)

    def extract_photo_text(
        self, image_bytes: bytes, media_type: str
    ) -> dict[str, str]:
        self._ensure_client()
        request = self._photo_text_request(image_bytes, media_type)
        request.pop("model", None)
        try:
            response = self._client.responses.create(
                model=self.settings.model_name, **request
            )
        except Exception as exc:
            raise ModelError(f"Azure Foundry photo text extraction failed: {exc}") from exc
        if getattr(response, "status", None) != "completed":
            raise ModelError("Azure Foundry did not complete photo text extraction.")
        try:
            payload = json.loads(getattr(response, "output_text", ""))
        except json.JSONDecodeError as exc:
            raise ModelError("Azure Foundry returned invalid photo text JSON.") from exc
        return {
            "text": str(payload.get("text") or "").strip(),
            "provider": "azure-foundry",
            "provider_version": self.settings.model_name,
        }

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        if self._credential is None:
            self.refresh_auth()
        try:
            from azure.ai.projects import AIProjectClient
        except ImportError as exc:
            raise ModelError(
                "Azure Foundry dependencies are missing. Install MELvan with its foundry extra."
            ) from exc
        self._project = AIProjectClient(
            endpoint=self.settings.foundry_project_endpoint,
            credential=self._credential,
        )
        self._client = self._project.get_openai_client()

    def _build_credential(self) -> Any:
        tenant = self.settings.azure_tenant_id
        if self.settings.azure_auth_mode != "managed_identity" and not tenant:
            raise ModelError(
                "AZURE_TENANT_ID is required for Azure Foundry mode. Set it to the tenant "
                "that contains the shared MELvan development deployment."
            )
        try:
            from azure.identity import (
                AzureCliCredential,
                AzureDeveloperCliCredential,
                ChainedTokenCredential,
                DeviceCodeCredential,
                InteractiveBrowserCredential,
                ManagedIdentityCredential,
                TokenCachePersistenceOptions,
            )
        except ImportError as exc:
            raise ModelError(
                "Azure Identity is not installed. Install MELvan with its foundry extra."
            ) from exc

        if self.settings.azure_auth_mode == "managed_identity":
            return ManagedIdentityCredential(
                client_id=self.settings.azure_managed_identity_client_id
            )

        credentials = []
        if shutil.which("azd"):
            credentials.append(AzureDeveloperCliCredential(tenant_id=tenant, process_timeout=30))
        if shutil.which("az"):
            credentials.append(AzureCliCredential(tenant_id=tenant, process_timeout=30))
        cache = TokenCachePersistenceOptions(
            name=self.settings.auth_cache_name,
            allow_unencrypted_storage=False,
        )
        if self.settings.azure_auth_mode in {"device_code", "auto"}:
            credentials.append(
                DeviceCodeCredential(
                    tenant_id=tenant,
                    timeout=self.settings.auth_timeout_seconds,
                    prompt_callback=self._device_code_prompt,
                    cache_persistence_options=cache,
                )
            )
        if self.settings.azure_auth_mode in {"browser", "interactive_browser", "auto"}:
            credentials.append(
                InteractiveBrowserCredential(
                    tenant_id=tenant,
                    cache_persistence_options=cache,
                )
            )
        if not credentials:
            raise ModelError(
                "No Azure credential method is available. Set MELVAN_AUTH_MODE to "
                "device_code, browser, or auto."
            )
        return credentials[0] if len(credentials) == 1 else ChainedTokenCredential(*credentials)

    def _auth_description(self) -> str:
        methods = []
        if shutil.which("azd"):
            methods.append("Azure Developer CLI")
        if shutil.which("az"):
            methods.append("Azure CLI")
        if self.settings.azure_auth_mode in {"device_code", "auto"}:
            methods.append("device code")
        if self.settings.azure_auth_mode in {"browser", "interactive_browser", "auto"}:
            methods.append("browser")
        if self.settings.azure_auth_mode == "managed_identity":
            methods.append("managed identity")
        return ", ".join(methods) or "configured Azure Identity chain"

    @staticmethod
    def _device_code_prompt(verification_uri: str, user_code: str, expires_on: Any) -> None:
        expiry = getattr(expires_on, "isoformat", lambda: str(expires_on))()
        print(
            "\nAzure sign-in required for MELvan."
            f"\nOpen: {verification_uri}"
            f"\nEnter code: {user_code}"
            f"\nCode expires: {expiry}\n",
            flush=True,
        )


def validate_result(
    payload: dict[str, Any], elements: list[dict[str, Any]], model_version: str
) -> AnalysisResult:
    classification = payload.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ModelError("The model returned an unsupported classification.")
    category = payload.get("outcome_category")
    change_types = payload.get("outcome_change_types") or []
    raw_direction = payload.get("result_direction")
    if raw_direction not in {"Positive", "Negative"}:
        raise ModelError("The model returned an invalid result direction.")
    direction = raw_direction if classification == "Outcome" else None
    if classification == "Outcome":
        if direction is None:  # Defensive invariant; the transport schema is non-nullable.
            raise ModelError("An Outcome must include a valid result direction.")
    if classification == "Outcome":
        if category not in OUTCOME_CHANGE_TYPES or not change_types:
            raise ModelError("An Outcome must include a valid category and change type.")
        if any(value not in OUTCOME_CHANGE_TYPES[category] for value in change_types):
            raise ModelError("The model returned an invalid Outcome change type.")
    else:
        # These fields are required by the structured transport schema. Discard
        # harmless Outcome-only metadata when the classification makes it inapplicable.
        category = None
        change_types = []

    status = payload.get("match_status")
    raw_decisions = payload.get("mapping_decisions") or []
    decisions = [
        MappingDecision(
            element_id=str(item.get("element_id") or ""),
            confidence=_confidence(item.get("confidence")),
            rationale=str(item.get("rationale") or "").strip(),
        )
        for item in raw_decisions
    ]
    ids = [item.element_id for item in decisions]
    valid_ids = {element["id"] for element in elements}
    if status == "existing_match":
        if (
            not ids
            or len(ids) != len(set(ids))
            or any(element_id not in valid_ids for element_id in ids)
            or any(not item.rationale for item in decisions)
        ):
            raise ModelError("The model returned an invalid existing-element mapping.")
        if payload.get("proposed_element") is not None:
            raise ModelError("A proposal cannot accompany an existing match.")
    elif status == "no_suitable_match":
        if ids:
            raise ModelError("A no-suitable-match result cannot contain existing mappings.")
        if not str(payload.get("no_match_rationale") or "").strip():
            raise ModelError("A no-suitable-match result requires a rationale.")
    else:
        raise ModelError("The model returned an invalid match status.")

    if payload.get("proposed_element") is not None:
        raise ModelError("The model cannot propose or create Logic Model elements.")

    return AnalysisResult(
        summary=str(payload["summary"]).strip(),
        classification=classification,
        classification_confidence=_confidence(payload["classification_confidence"]),
        classification_rationale=str(payload["classification_rationale"]).strip(),
        result_direction=direction,
        outcome_category=category,
        outcome_change_types=list(change_types),
        match_status=status,
        mapping_decisions=decisions,
        no_match_rationale=str(payload.get("no_match_rationale") or "").strip(),
        proposed_element=None,
        model_version=model_version,
    )


def _logic_model_schema() -> dict[str, Any]:
    element = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "display_id": {"type": "string"},
            "label": {"type": "string"},
            "element_type": {"type": "string", "enum": ["Output", "Outcome"]},
            "level": {
                "type": "string",
                "enum": [
                    "output", "enabling_output", "short_term_outcome",
                    "medium_term_outcome", "long_term_outcome", "ultimate_goal",
                ],
            },
            "group": {"type": ["string", "null"]},
        },
        "required": ["display_id", "label", "element_type", "level", "group"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "elements": {"type": "array", "items": element},
        },
        "required": ["title", "elements"],
    }


def validate_logic_model_structure(payload: Any, source_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ModelError("The Logic Model extraction was not an object.")
    title = " ".join(str(payload.get("title") or Path(source_name).stem).split())
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        raise ModelError("The Logic Model extraction did not include an elements list.")
    elements: list[dict[str, Any]] = []
    display_ids: set[str] = set()
    valid_levels = {
        "output", "enabling_output", "short_term_outcome", "medium_term_outcome",
        "long_term_outcome", "ultimate_goal",
    }
    for raw in raw_elements:
        if not isinstance(raw, dict):
            continue
        display_id = " ".join(str(raw.get("display_id") or "").split()).upper()
        label = " ".join(str(raw.get("label") or "").split())
        element_type = raw.get("element_type")
        level = raw.get("level")
        group = " ".join(str(raw.get("group") or "").split()) or None
        if not display_id or not label or element_type not in {"Output", "Outcome"} or level not in valid_levels:
            continue
        if display_id in display_ids:
            raise ModelError(f"The Logic Model contains duplicate notation: {display_id}.")
        display_ids.add(display_id)
        elements.append(
            {
                "display_id": display_id,
                "label": label,
                "element_type": element_type,
                "level": level,
                "group": group,
            }
        )
    if not elements:
        raise ModelError("No Outputs or Outcomes could be ingested from the Logic Model file.")
    return {"title": title or Path(source_name).stem, "elements": elements}


def _logic_element_type_level(display_id: str) -> tuple[str, str]:
    value = display_id.upper()
    if value.startswith("SO"):
        return "Outcome", "short_term_outcome"
    if value.startswith("MO"):
        return "Outcome", "medium_term_outcome"
    if value.startswith("LO"):
        return "Outcome", "long_term_outcome"
    if value.startswith("UG"):
        return "Outcome", "ultimate_goal"
    return "Output", "output"


def build_model_adapter(settings: Settings) -> ModelAdapter:
    if settings.model_mode == "foundry":
        return AzureFoundryModelAdapter(settings)
    if settings.model_mode == "openai":
        return OpenAIModelAdapter(settings)
    return DemoModelAdapter()


def _confidence(value: Any) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise ModelError("Confidence values must be between zero and one.")
    return round(number, 3)
