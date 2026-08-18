"""Dependency-free PDF export for accepted MELvan Evidence."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


A4_WIDTH = 595.28
A4_HEIGHT = 841.89
MARGIN = 48.0
INK = (0.0, 0.0, 0.0)
MUTED = (0.38, 0.38, 0.38)
LINE = (0.78, 0.78, 0.78)

LEVEL_LABELS = {
    "ultimate_goal": "Ultimate Goal",
    "long_term_outcome": "Long-term outcome",
    "medium_term_outcome": "Medium-term outcome",
    "short_term_outcome": "Short-term outcome",
    "working_outcome": "Reviewer-created outcome",
    "output": "Output",
}


def _clean_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, max_width: float, size: float, *, bold: bool = False) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    average_width = size * (0.54 if bold else 0.50)
    max_chars = max(8, int(max_width / average_width))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass
class _Page:
    commands: list[str]
    images: list["_Image"] = field(default_factory=list)


@dataclass
class _Image:
    name: str
    content: bytes
    pixel_width: int
    pixel_height: int


class EvidencePDF:
    def __init__(self, title: str, logic_model: str, generated_at: str):
        self.title = _clean_text(title)
        self.logic_model = _clean_text(logic_model)
        self.generated_at = _clean_text(generated_at)
        self.pages: list[_Page] = []
        self.page: _Page
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.page = _Page([])
        self.pages.append(self.page)
        self.y = A4_HEIGHT - MARGIN
        self._text(MARGIN, self.y, "MELvan Evidence", 9, bold=True)
        self._text(A4_WIDTH - MARGIN, self.y, self.logic_model, 8, color=MUTED, align="right")
        self._line(self.y - 9)
        self.y -= 25

    def _ensure(self, height: float) -> None:
        if self.y - height < 58:
            self._new_page()

    def _text(
        self,
        x: float,
        y: float,
        value: str,
        size: float,
        *,
        bold: bool = False,
        color: tuple[float, float, float] = INK,
        align: str = "left",
    ) -> None:
        value = _clean_text(value)
        if align == "right":
            x -= len(value) * size * (0.54 if bold else 0.50)
        font = "F2" if bold else "F1"
        self.page.commands.append(
            f"BT {color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg "
            f"/{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm "
            f"({_pdf_escape(value)}) Tj ET"
        )

    def _line(self, y: float) -> None:
        self.page.commands.append(
            f"{LINE[0]:.3f} {LINE[1]:.3f} {LINE[2]:.3f} RG 0.7 w "
            f"{MARGIN:.2f} {y:.2f} m {A4_WIDTH - MARGIN:.2f} {y:.2f} l S"
        )

    def paragraph(
        self,
        value: str,
        *,
        size: float = 10,
        bold: bool = False,
        color: tuple[float, float, float] = INK,
        indent: float = 0,
        after: float = 7,
    ) -> None:
        width = A4_WIDTH - (2 * MARGIN) - indent
        lines = _wrap(value, width, size, bold=bold)
        if not lines:
            return
        leading = size * 1.38
        for line in lines:
            self._ensure(leading)
            self._text(MARGIN + indent, self.y, line, size, bold=bold, color=color)
            self.y -= leading
        self.y -= after

    def label(self, value: str) -> None:
        self._ensure(18)
        self._text(MARGIN, self.y, value.upper(), 7.5, bold=True)
        self.y -= 13

    def heading(self, value: str, *, level: int) -> None:
        size = 20 if level == 1 else 14 if level == 2 else 11
        before = 5 if level == 1 else 17 if level == 2 else 10
        self._ensure(before + size * 2)
        self.y -= before
        self.paragraph(value, size=size, bold=True, after=8 if level < 3 else 5)

    def divider(self) -> None:
        self._ensure(15)
        self.y -= 4
        self._line(self.y)
        self.y -= 11

    def image(
        self,
        content: bytes,
        *,
        label: str | None = None,
        caption: str | None = None,
        after: float = 10,
    ) -> None:
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(content)) as source:
                image = ImageOps.exif_transpose(source).convert("L")
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=84, optimize=True)
                encoded = output.getvalue()
                pixel_width, pixel_height = image.size
        except (ImportError, OSError, ValueError) as exc:
            raise ValueError("Evidence attachment could not be rendered.") from exc
        max_width = A4_WIDTH - (2 * MARGIN)
        max_height = 280.0
        scale = min(max_width / pixel_width, max_height / pixel_height)
        width = pixel_width * scale
        height = pixel_height * scale
        caption_height = (
            _paragraph_height(caption, size=8, after=5) if caption else 0
        )
        self._ensure(height + after + caption_height + (18 if label else 0))
        if label:
            self.label(label)
        self.y -= height
        x = MARGIN + ((max_width - width) / 2)
        name = f"Im{len(self.page.images) + 1}"
        self.page.images.append(
            _Image(
                name=name,
                content=encoded,
                pixel_width=pixel_width,
                pixel_height=pixel_height,
            )
        )
        self.page.commands.append(
            f"q {width:.2f} 0 0 {height:.2f} {x:.2f} {self.y:.2f} cm /{name} Do Q"
        )
        self.y -= 6
        if caption:
            self.paragraph(caption, size=8, color=MUTED, after=after)
        else:
            self.y -= after

    def finish(self) -> bytes:
        total = len(self.pages)
        for index, page in enumerate(self.pages, start=1):
            page.commands.append(
                f"BT {MUTED[0]:.3f} {MUTED[1]:.3f} {MUTED[2]:.3f} rg "
                f"/F1 7.5 Tf 1 0 0 1 {MARGIN:.2f} 31 Tm "
                f"({_pdf_escape(self.generated_at)}) Tj ET"
            )
            footer = f"Page {index} of {total}"
            x = A4_WIDTH - MARGIN - len(footer) * 3.75
            page.commands.append(
                f"BT {MUTED[0]:.3f} {MUTED[1]:.3f} {MUTED[2]:.3f} rg "
                f"/F1 7.5 Tf 1 0 0 1 {x:.2f} 31 Tm ({footer}) Tj ET"
            )
        return _encode_pdf(self.pages)


def _encode_pdf(pages: list[_Page]) -> bytes:
    objects: list[bytes] = [b""]

    def add(value: bytes) -> int:
        objects.append(value)
        return len(objects) - 1

    regular_font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold_font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    pages_id = add(b"")
    page_ids: list[int] = []
    for page in pages:
        image_ids: dict[str, int] = {}
        for image in page.images:
            image_ids[image.name] = add(
                (
                    f"<< /Type /XObject /Subtype /Image "
                    f"/Width {image.pixel_width} /Height {image.pixel_height} "
                    f"/ColorSpace /DeviceGray /BitsPerComponent 8 "
                    f"/Filter /DCTDecode /Length {len(image.content)} >>\nstream\n"
                ).encode("ascii")
                + image.content
                + b"\nendstream"
            )
        stream = "\n".join(page.commands).encode("cp1252")
        stream_id = add(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
        xobjects = " ".join(
            f"/{name} {object_id} 0 R" for name, object_id in image_ids.items()
        )
        xobject_resources = f" /XObject << {xobjects} >>" if xobjects else ""
        page_id = add(
            (
                f"<< /Type /Page /Parent {pages_id} 0 R "
                f"/MediaBox [0 0 {A4_WIDTH:.2f} {A4_HEIGHT:.2f}] "
                f"/Resources << /Font << /F1 {regular_font} 0 R /F2 {bold_font} 0 R >>"
                f"{xobject_resources} >> "
                f"/Contents {stream_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)
    objects[pages_id] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids "
        f"[{' '.join(f'{item} 0 R' for item in page_ids)}] >>"
    ).encode("ascii")
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, value in enumerate(objects[1:], start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects)} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _paragraph_height(
    value: str,
    *,
    size: float,
    bold: bool = False,
    indent: float = 0,
    after: float = 7,
) -> float:
    width = A4_WIDTH - (2 * MARGIN) - indent
    return (len(_wrap(value, width, size, bold=bold)) * size * 1.38) + after


def _record_height(record: dict[str, Any], acceptance: str) -> float:
    """Estimate a record block so normal records do not split across pages."""
    analysis = record.get("analysis") or {}
    height = _paragraph_height("Evidence", size=9, bold=True, after=3)
    height += _paragraph_height(
        f"Approved {(record.get('evidence') or {}).get('accepted_at') or 'Unknown date'}",
        size=8.5,
        after=7,
    )
    height += 13 + _paragraph_height(record.get("original_text") or "", size=10.5, bold=True)
    capture = _capture_provenance(record)
    if capture:
        height += 13 + _paragraph_height(capture, size=9)
    context = record.get("context") or {}
    if context.get("text"):
        height += 13 + _paragraph_height(context["text"], size=9)
    if record.get("attachment_content"):
        height += 13 + 290
    height += 13 + _paragraph_height(_classification_text(analysis), size=9.5)
    height += 13 + _paragraph_height(analysis.get("summary") or "", size=9.5)
    height += 13 + _paragraph_height(
        f"Logic Model {analysis.get('logic_model_version') or ''}", size=9
    )
    height += 13
    mappings = record.get("mappings") or []
    if mappings:
        for mapping in mappings:
            height += _paragraph_height(
                mapping.get("label") or "", size=9, bold=True, indent=8, after=2
            )
            height += _paragraph_height(
                _mapping_meta(mapping), size=8.5, indent=8, after=3
            )
            if mapping.get("rationale"):
                height += _paragraph_height(
                    mapping["rationale"], size=8.5, indent=8, after=5
                )
    else:
        height += _paragraph_height("No approved Logic Model mapping", size=9)
    if analysis.get("classification_rationale"):
        height += 13 + _paragraph_height(
            analysis["classification_rationale"], size=9
        )
    height += 13 + _paragraph_height(acceptance, size=8.5)
    return height + 15


def _capture_provenance(record: dict[str, Any]) -> str:
    method = str(record.get("capture_method") or "text")
    if method == "text":
        return ""
    label = "Voice transcription" if method == "voice" else "Photo text"
    if record.get("capture_text_edited"):
        label += " | Reporter edited the captured text"
    return label


def _classification_text(analysis: dict[str, Any]) -> str:
    values = [str(analysis.get("classification") or "Unclassified")]
    if analysis.get("classification") == "Outcome":
        if analysis.get("result_direction"):
            values.append(str(analysis["result_direction"]))
        change_types = analysis.get("outcome_change_types") or []
        if change_types:
            values.append(f"Category: {', '.join(str(item) for item in change_types)}")
    return " | ".join(values)


def _mapping_meta(mapping: dict[str, Any]) -> str:
    level = LEVEL_LABELS.get(
        str(mapping.get("level") or ""),
        _clean_text(mapping.get("level") or "").replace("_", " ").title(),
    )
    source = (
        "MELvan proposal"
        if mapping.get("status") == "provisional"
        else "Reviewer-created"
        if mapping.get("source") == "reviewer"
        else level
    )
    identifier = mapping.get("display_id") or mapping.get("id") or ""
    return f"{source} | {identifier}"


def build_evidence_pdf(
    evidence: list[dict[str, Any]],
    *,
    logic_model_title: str,
    logic_model_version: str,
    generated_at: str,
    user_names: dict[str, str] | None = None,
) -> bytes:
    names = user_names or {}
    logic_model = f"Logic Model {logic_model_version}"
    pdf = EvidencePDF("MELvan Evidence Export", logic_model, f"Generated {generated_at}")
    pdf.heading("MELvan Evidence Export", level=1)
    pdf.paragraph(logic_model_title, size=11, bold=True, after=2)
    pdf.paragraph(f"{logic_model} | {len(evidence)} Evidence record{'s' if len(evidence) != 1 else ''}", size=9, color=MUTED, after=15)
    if not evidence:
        pdf.heading("No Evidence available", level=2)
        pdf.paragraph("No accepted Evidence is currently available for export.", color=MUTED)
        return pdf.finish()
    pdf._new_page()

    records = sorted(
        evidence,
        key=lambda item: (
            (item.get("evidence") or {}).get("accepted_at") or "",
            item.get("id") or "",
        ),
        reverse=True,
    )
    for record_number, record in enumerate(records, start=1):
        analysis = record.get("analysis") or {}
        accepted = record.get("evidence") or {}
        accepted_by = names.get(
            accepted.get("accepted_by"),
            accepted.get("accepted_by") or "Unknown Reviewer",
        )
        accepted_at = _clean_text(accepted.get("accepted_at") or "Unknown date")
        acceptance = f"Accepted by {accepted_by} on {accepted_at}"
        pdf._ensure(min(_record_height(record, acceptance), 690))
        pdf.paragraph(f"Evidence {record_number}", size=9, bold=True, after=3)
        pdf.paragraph(f"Approved {accepted_at}", size=8.5, color=MUTED, after=8)
        pdf.label("Statement")
        pdf.paragraph(record.get("original_text") or "", size=10.5, bold=True)
        capture = _capture_provenance(record)
        if capture:
            pdf.label("Capture")
            pdf.paragraph(capture, size=9, color=MUTED)
        context = record.get("context") or {}
        if context.get("text"):
            pdf.label("Context")
            pdf.paragraph(context["text"], size=9, color=MUTED)
        if record.get("attachment_content"):
            attachment = record.get("attachment") or {}
            dimensions = (
                f"Photo Statement | {attachment.get('width')} x "
                f"{attachment.get('height')} px"
                if attachment.get("width") and attachment.get("height")
                else "Photo Statement"
            )
            pdf.image(
                record["attachment_content"],
                label="Attachment",
                caption=dimensions,
            )
        pdf.label("Classification")
        pdf.paragraph(_classification_text(analysis), size=9.5)
        pdf.label("MELvan summary")
        pdf.paragraph(analysis.get("summary") or "", size=9.5, color=MUTED)
        pdf.label("Logic Model")
        pdf.paragraph(
            f"Logic Model {analysis.get('logic_model_version') or logic_model_version}",
            size=9,
        )
        pdf.label("Logic Model mapping")
        mappings = record.get("mappings") or []
        if mappings:
            for mapping in mappings:
                pdf.paragraph(
                    mapping.get("label") or "",
                    size=9,
                    bold=True,
                    indent=8,
                    after=2,
                )
                pdf.paragraph(
                    _mapping_meta(mapping),
                    size=8.5,
                    color=MUTED,
                    indent=8,
                    after=3,
                )
                if mapping.get("rationale"):
                    pdf.paragraph(
                        mapping["rationale"],
                        size=8.5,
                        color=MUTED,
                        indent=8,
                        after=5,
                    )
        else:
            pdf.paragraph("No approved Logic Model mapping", size=9, color=MUTED)
        if analysis.get("classification_rationale"):
            rationale = analysis["classification_rationale"]
            pdf._ensure(
                13
                + _paragraph_height(rationale, size=9)
                + 13
                + _paragraph_height(acceptance, size=8.5)
                + 15
            )
            pdf.label("Classification rationale")
            pdf.paragraph(rationale, size=9)
        pdf.label("Acceptance")
        pdf.paragraph(acceptance, size=8.5, color=MUTED)
        pdf.divider()
    return pdf.finish()


def export_filename(now: datetime) -> str:
    return f"melvan-evidence-{now.date().isoformat()}.pdf"
