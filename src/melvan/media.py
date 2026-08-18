"""Transient media processing and persistent Statement photo storage."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .model import ModelAdapter, ModelError


MAX_VOICE_SECONDS = 60
MAX_AUDIO_BYTES = 2_100_000
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PHOTO_PIXELS = 64_000_000
MAX_NORMALIZED_PHOTO_PIXELS = 24_000_000
TARGET_PHOTO_BYTES = 3_500_000
STAGING_TTL_SECONDS = 60 * 60


class MediaError(RuntimeError):
    """A visible, retryable media-processing failure."""


@dataclass(frozen=True)
class MediaTextResult:
    text: str
    provider: str
    provider_version: str
    processing_ms: int
    outcome: str = "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "processing_ms": self.processing_ms,
            "outcome": self.outcome,
        }


class SpeechTranscriber:
    def transcribe(self, audio: bytes, media_type: str) -> MediaTextResult:
        raise NotImplementedError

    def readiness(self) -> dict[str, str]:
        raise NotImplementedError


class DemoSpeechTranscriber(SpeechTranscriber):
    """Deterministic adapter for local development and automated tests."""

    def transcribe(self, audio: bytes, media_type: str) -> MediaTextResult:
        if not audio:
            raise MediaError("No recording was received. Try recording again.")
        return MediaTextResult(
            text="This is a locally simulated voice transcription.",
            provider="melvan-demo",
            provider_version="speech-v1",
            processing_ms=1,
        )

    def readiness(self) -> dict[str, str]:
        return {"status": "ready", "mode": "demo"}


class DisabledSpeechTranscriber(SpeechTranscriber):
    def transcribe(self, audio: bytes, media_type: str) -> MediaTextResult:
        raise MediaError("Voice transcription is not configured for this environment.")

    def readiness(self) -> dict[str, str]:
        return {"status": "unavailable", "mode": "disabled"}


class AzureSpeechTranscriber(SpeechTranscriber):
    """Azure Speech short-audio adapter using managed identity."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential: Any = None

    def transcribe(self, audio: bytes, media_type: str) -> MediaTextResult:
        if not audio:
            raise MediaError("No recording was received. Try recording again.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise MediaError("The recording is too large. Record a shorter statement.")
        self._validate_wav(audio, media_type)
        endpoint = str(self.settings.speech_endpoint or "")
        if not endpoint:
            raise MediaError("Voice transcription is not configured for this environment.")
        started = time.monotonic()
        token = self._get_token()
        separator = "&" if "?" in endpoint else "?"
        request = urllib.request.Request(
            f"{endpoint}{separator}language={self.settings.speech_language}&format=detailed",
            data=audio,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.read()
            raise MediaError(
                "Voice transcription could not process this recording. Try again."
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MediaError("Voice transcription is temporarily unavailable. Try again.") from exc
        text = str(payload.get("DisplayText") or "").strip()
        if not text:
            alternatives = payload.get("NBest") or []
            if alternatives:
                text = str(alternatives[0].get("Display") or "").strip()
        if not text:
            raise MediaError("No speech was transcribed. Try recording again.")
        return MediaTextResult(
            text=text,
            provider="azure-speech",
            provider_version="short-audio-v1",
            processing_ms=int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _validate_wav(audio: bytes, media_type: str) -> None:
        if media_type.split(";", 1)[0].strip().lower() != "audio/wav":
            raise MediaError("This recording format is not supported. Try recording again.")
        try:
            with wave.open(io.BytesIO(audio), "rb") as recording:
                channels = recording.getnchannels()
                sample_width = recording.getsampwidth()
                sample_rate = recording.getframerate()
                frames = recording.getnframes()
                compression = recording.getcomptype()
        except (EOFError, wave.Error) as exc:
            raise MediaError("This recording format is not supported. Try recording again.") from exc
        if (
            channels != 1
            or sample_width != 2
            or sample_rate != 16_000
            or compression != "NONE"
        ):
            raise MediaError("This recording format is not supported. Try recording again.")
        if frames > MAX_VOICE_SECONDS * sample_rate:
            raise MediaError("Voice notes must be 60 seconds or shorter.")

    def readiness(self) -> dict[str, str]:
        return {
            "status": "ready" if self.settings.speech_endpoint else "unavailable",
            "mode": "azure",
        }

    def _get_token(self) -> str:
        if self._credential is None:
            try:
                from azure.identity import ManagedIdentityCredential
            except ImportError as exc:
                raise MediaError("Azure Speech dependencies are unavailable.") from exc
            self._credential = ManagedIdentityCredential(
                client_id=self.settings.azure_managed_identity_client_id
            )
        try:
            return self._credential.get_token(self.settings.speech_token_scope).token
        except Exception as exc:
            raise MediaError("Voice transcription credentials are unavailable.") from exc


class PhotoTextExtractor:
    def __init__(self, model: ModelAdapter):
        self.model = model

    def extract(self, image: bytes, media_type: str) -> MediaTextResult:
        started = time.monotonic()
        try:
            result = self.model.extract_photo_text(image, media_type)
        except ModelError as exc:
            raise MediaError("Photo text extraction is temporarily unavailable. Try again.") from exc
        return MediaTextResult(
            text=str(result.get("text") or "").strip(),
            provider=str(result.get("provider") or self.model.mode),
            provider_version=str(result.get("provider_version") or "photo-text-v1"),
            processing_ms=int((time.monotonic() - started) * 1000),
            outcome="success" if str(result.get("text") or "").strip() else "no_text",
        )

    def readiness(self) -> dict[str, str]:
        return {"status": "ready", "mode": self.model.mode}


class DisabledPhotoTextExtractor:
    def extract(self, image: bytes, media_type: str) -> MediaTextResult:
        raise MediaError("Photo text extraction is not configured for this environment.")

    def readiness(self) -> dict[str, str]:
        return {"status": "unavailable", "mode": "disabled"}


class AzureDocumentIntelligenceTextExtractor:
    """Azure Document Intelligence Read adapter using managed identity."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential: Any = None

    def extract(self, image: bytes, media_type: str) -> MediaTextResult:
        if not image:
            raise MediaError("Choose or take a photo before continuing.")
        endpoint = str(self.settings.ocr_endpoint or "").rstrip("/")
        if not endpoint:
            raise MediaError("Photo text extraction is not configured for this environment.")
        started = time.monotonic()
        token = self._get_token()
        api_version = urllib.parse.quote(self.settings.ocr_api_version, safe="")
        analyze_url = (
            f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
            f"?_overload=analyzeDocument&api-version={api_version}"
        )
        request = urllib.request.Request(
            analyze_url,
            data=image,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": media_type,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                operation_location = str(response.headers.get("Operation-Location") or "")
            self._validate_operation_location(endpoint, operation_location)
            payload = self._poll(operation_location, token)
        except MediaError:
            raise
        except urllib.error.HTTPError as exc:
            exc.read()
            raise MediaError(
                "Photo text extraction could not process this photo. Try again."
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MediaError(
                "Photo text extraction is temporarily unavailable. Try again."
            ) from exc
        text = self._result_text(payload)
        return MediaTextResult(
            text=text,
            provider="azure-document-intelligence",
            provider_version=f"prebuilt-read-{self.settings.ocr_api_version}",
            processing_ms=int((time.monotonic() - started) * 1000),
            outcome="success" if text else "no_text",
        )

    def readiness(self) -> dict[str, str]:
        return {
            "status": "ready" if self.settings.ocr_endpoint else "unavailable",
            "mode": "azure",
        }

    def _poll(self, operation_location: str, token: str) -> dict[str, Any]:
        for attempt in range(30):
            request = urllib.request.Request(
                operation_location,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            status = str(payload.get("status") or "").lower()
            if status == "succeeded":
                return payload
            if status in {"failed", "canceled"}:
                raise MediaError(
                    "Photo text extraction could not process this photo. Try again."
                )
            if status not in {"notstarted", "running"}:
                raise MediaError(
                    "Photo text extraction returned an unexpected response. Try again."
                )
            if attempt < 29:
                time.sleep(1)
        raise MediaError("Photo text extraction timed out. Try again.")

    @staticmethod
    def _validate_operation_location(endpoint: str, operation_location: str) -> None:
        expected = urllib.parse.urlsplit(endpoint)
        actual = urllib.parse.urlsplit(operation_location)
        if (
            actual.scheme != "https"
            or actual.scheme != expected.scheme
            or actual.netloc != expected.netloc
            or not actual.path.startswith("/documentintelligence/")
        ):
            raise MediaError(
                "Photo text extraction returned an invalid operation reference. Try again."
            )

    @staticmethod
    def _result_text(payload: dict[str, Any]) -> str:
        analyze_result = payload.get("analyzeResult") or {}
        text = str(analyze_result.get("content") or "").strip()
        if text:
            return text
        lines: list[str] = []
        for page in analyze_result.get("pages") or []:
            for line in page.get("lines") or []:
                value = str(line.get("content") or "").strip()
                if value:
                    lines.append(value)
        return "\n".join(lines)

    def _get_token(self) -> str:
        if self._credential is None:
            try:
                from azure.identity import ManagedIdentityCredential
            except ImportError as exc:
                raise MediaError(
                    "Azure Document Intelligence dependencies are unavailable."
                ) from exc
            self._credential = ManagedIdentityCredential(
                client_id=self.settings.azure_managed_identity_client_id
            )
        try:
            return self._credential.get_token(self.settings.ocr_token_scope).token
        except Exception as exc:
            raise MediaError(
                "Photo text extraction credentials are unavailable."
            ) from exc


@dataclass(frozen=True)
class NormalizedPhoto:
    content: bytes
    media_type: str
    width: int
    height: int
    checksum: str


@runtime_checkable
class AttachmentStorage(Protocol):
    def initialize(self) -> None: ...

    def normalize(self, content: bytes) -> NormalizedPhoto: ...

    def stage(self, content: bytes) -> tuple[str, NormalizedPhoto, str]: ...

    def promote(
        self, staging_key: str, workspace_id: str, statement_id: str, attachment_id: str
    ) -> str: ...

    def read(self, storage_key: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...

    def write_exact(
        self, storage_key: str, content: bytes, checksum: str
    ) -> None: ...

    def list_keys(self) -> list[str]: ...

    def readiness(self) -> dict[str, str]: ...


class LocalAttachmentStore:
    """Opaque file storage with validated, metadata-free display images."""

    def __init__(self, root: Path):
        self.root = root
        self.staging_root = root / "staging"

    def initialize(self) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def normalize(self, content: bytes) -> NormalizedPhoto:
        if not content:
            raise MediaError("Choose or take a photo before continuing.")
        if len(content) > MAX_PHOTO_BYTES:
            raise MediaError("The photo must be 10 MB or smaller.")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            raise MediaError("Photo processing is unavailable.") from exc
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            pass
        try:
            with Image.open(io.BytesIO(content)) as source:
                source.verify()
            with Image.open(io.BytesIO(content)) as source:
                width, height = source.size
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_SOURCE_PHOTO_PIXELS
                ):
                    raise MediaError("The photo must be 64 megapixels or smaller.")
                image = ImageOps.exif_transpose(source)
                image = image.convert("RGB")
                image = self._downscale_to_pixel_limit(image)
                normalized, image = self._compress(image)
                width, height = image.size
        except MediaError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MediaError("Choose a valid JPEG, PNG, WebP, HEIC, or HEIF photo.") from exc
        return NormalizedPhoto(
            content=normalized,
            media_type="image/jpeg",
            width=width,
            height=height,
            checksum=hashlib.sha256(normalized).hexdigest(),
        )

    @staticmethod
    def _downscale_to_pixel_limit(image: Any) -> Any:
        pixels = image.width * image.height
        if pixels <= MAX_NORMALIZED_PHOTO_PIXELS:
            return image
        from PIL import Image

        scale = (MAX_NORMALIZED_PHOTO_PIXELS / pixels) ** 0.5
        size = (
            max(1, int(image.width * scale)),
            max(1, int(image.height * scale)),
        )
        return image.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    def _compress(image: Any) -> tuple[bytes, Any]:
        from PIL import Image

        current = image
        for _ in range(6):
            for quality in (88, 82, 76, 70):
                output = io.BytesIO()
                current.save(output, format="JPEG", quality=quality, optimize=True)
                content = output.getvalue()
                if len(content) <= TARGET_PHOTO_BYTES:
                    return content, current
            scale = min(0.9, (TARGET_PHOTO_BYTES / max(1, len(content))) ** 0.5 * 0.95)
            size = (
                max(1, int(current.width * scale)),
                max(1, int(current.height * scale)),
            )
            if size == current.size:
                break
            current = current.resize(size, Image.Resampling.LANCZOS)
        if len(content) > TARGET_PHOTO_BYTES:
            raise MediaError("The photo could not be reduced to a supported size.")
        return content, current

    def stage(self, content: bytes) -> tuple[str, NormalizedPhoto, str]:
        photo = self.normalize(content)
        stage_id = f"stage-{uuid.uuid4().hex}"
        path = self.staging_root / f"{stage_id}.jpg"
        self._atomic_write(path, photo.content)
        return stage_id, photo, str(path.relative_to(self.root))

    def promote(
        self, staging_key: str, workspace_id: str, statement_id: str, attachment_id: str
    ) -> str:
        source = self._safe_path(staging_key)
        destination = self.root / workspace_id / statement_id / f"{attachment_id}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return str(destination.relative_to(self.root))

    def read(self, storage_key: str) -> bytes:
        path = self._safe_path(storage_key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise MediaError("The attached photo is unavailable.") from exc

    def delete(self, storage_key: str) -> None:
        try:
            self._safe_path(storage_key).unlink(missing_ok=True)
        except OSError:
            pass

    def write_exact(self, storage_key: str, content: bytes, checksum: str) -> None:
        if hashlib.sha256(content).hexdigest() != checksum:
            raise MediaError("The attachment checksum does not match its content.")
        path = self._safe_path(storage_key)
        if path.exists():
            raise MediaError("The destination attachment already exists.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)

    def list_keys(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and path.name != ".write-probe"
        )

    def readiness(self) -> dict[str, str]:
        try:
            self.initialize()
            probe = self.root / ".write-probe"
            self._atomic_write(probe, b"ready")
            probe.unlink(missing_ok=True)
            return {"status": "ready", "root": str(self.root)}
        except OSError:
            return {"status": "unavailable", "root": str(self.root)}

    def _safe_path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            raise MediaError("The attachment reference is invalid.")
        return candidate

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)


class AzureBlobAttachmentStore:
    """Private Blob Storage adapter using managed identity or an emulator."""

    def __init__(
        self,
        *,
        account_url: str | None,
        container: str,
        connection_string: str | None = None,
        credential: Any = None,
    ):
        if not account_url and not connection_string:
            raise ValueError(
                "Azure Blob storage requires an account URL or connection string."
            )
        self.account_url = account_url
        self.container = container
        self.connection_string = connection_string
        self._credential = credential
        self._container_client: Any = None
        self._normalizer = LocalAttachmentStore(Path("."))

    def _client(self) -> Any:
        if self._container_client is not None:
            return self._container_client
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise MediaError("Azure Blob attachment storage is unavailable.") from exc
        if self.connection_string:
            service = BlobServiceClient.from_connection_string(
                self.connection_string
            )
        else:
            credential = self._credential
            if credential is None:
                try:
                    from azure.identity import DefaultAzureCredential
                except ImportError as exc:
                    raise MediaError(
                        "Azure managed-identity support is unavailable."
                    ) from exc
                credential = DefaultAzureCredential()
            service = BlobServiceClient(
                account_url=str(self.account_url),
                credential=credential,
            )
        self._container_client = service.get_container_client(self.container)
        return self._container_client

    def initialize(self) -> None:
        client = self._client()
        try:
            client.create_container()
        except Exception as exc:
            if getattr(exc, "status_code", None) != 409:
                raise MediaError(
                    "The private attachment container is unavailable."
                ) from exc

    def normalize(self, content: bytes) -> NormalizedPhoto:
        return self._normalizer.normalize(content)

    def stage(self, content: bytes) -> tuple[str, NormalizedPhoto, str]:
        photo = self.normalize(content)
        stage_id = f"stage-{uuid.uuid4().hex}"
        key = f"staging/{stage_id}.jpg"
        self._client().upload_blob(
            name=key,
            data=photo.content,
            overwrite=False,
            metadata={"sha256": photo.checksum},
        )
        return stage_id, photo, key

    def promote(
        self, staging_key: str, workspace_id: str, statement_id: str, attachment_id: str
    ) -> str:
        if not staging_key.startswith("staging/"):
            raise MediaError("The attachment reference is invalid.")
        final_key = f"{workspace_id}/{statement_id}/{attachment_id}.jpg"
        client = self._client()
        source = client.get_blob_client(staging_key)
        destination = client.get_blob_client(final_key)
        content = source.download_blob().readall()
        destination.upload_blob(content, overwrite=False)
        source.delete_blob()
        return final_key

    def read(self, storage_key: str) -> bytes:
        if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
            raise MediaError("The attachment reference is invalid.")
        try:
            return self._client().download_blob(storage_key).readall()
        except Exception as exc:
            raise MediaError("The attached photo is unavailable.") from exc

    def delete(self, storage_key: str) -> None:
        if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
            return
        try:
            self._client().delete_blob(storage_key, delete_snapshots="include")
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

    def write_exact(self, storage_key: str, content: bytes, checksum: str) -> None:
        if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
            raise MediaError("The attachment reference is invalid.")
        if hashlib.sha256(content).hexdigest() != checksum:
            raise MediaError("The attachment checksum does not match its content.")
        try:
            self._client().upload_blob(
                name=storage_key,
                data=content,
                overwrite=False,
                metadata={"sha256": checksum},
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                raise MediaError("The destination attachment already exists.") from exc
            raise MediaError("The attachment could not be written to Blob Storage.") from exc

    def list_keys(self) -> list[str]:
        try:
            return sorted(str(item.name) for item in self._client().list_blobs())
        except Exception as exc:
            raise MediaError("Blob Storage attachments could not be listed.") from exc

    def readiness(self) -> dict[str, str]:
        try:
            properties = self._client().get_container_properties()
            if properties.get("public_access") not in {None, "disabled"}:
                raise MediaError("The attachment container must be private.")
            return {
                "status": "ready",
                "backend": "azure_blob",
                "container": self.container,
            }
        except Exception:
            return {
                "status": "unavailable",
                "backend": "azure_blob",
                "container": self.container,
            }


# Existing imports keep selecting the local development and hosted-demo adapter.
AttachmentStore = LocalAttachmentStore


def build_attachment_store(settings: Settings) -> AttachmentStorage:
    if settings.attachment_backend == "azure_blob":
        return AzureBlobAttachmentStore(
            account_url=settings.blob_account_url,
            container=settings.blob_container,
            connection_string=settings.blob_connection_string,
        )
    return LocalAttachmentStore(
        settings.attachment_root
        or (
            settings.persistent_database_path.parent / "attachments"
            if settings.persistent_database_path
            else settings.database_path.parent / "attachments"
        )
    )


def build_speech_transcriber(settings: Settings) -> SpeechTranscriber:
    if settings.speech_mode == "azure":
        return AzureSpeechTranscriber(settings)
    if settings.speech_mode == "disabled":
        return DisabledSpeechTranscriber()
    return DemoSpeechTranscriber()


def build_photo_text_extractor(
    settings: Settings, model: ModelAdapter
) -> PhotoTextExtractor | AzureDocumentIntelligenceTextExtractor | DisabledPhotoTextExtractor:
    if settings.ocr_mode == "azure":
        return AzureDocumentIntelligenceTextExtractor(settings)
    if settings.ocr_mode == "disabled":
        return DisabledPhotoTextExtractor()
    return PhotoTextExtractor(model)
