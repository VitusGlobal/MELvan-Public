from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from melvan.media import (
    TARGET_PHOTO_BYTES,
    AttachmentStore,
    AzureBlobAttachmentStore,
    AzureDocumentIntelligenceTextExtractor,
    AzureSpeechTranscriber,
    MediaError,
)


def exercise_attachment_storage_contract(
    case: unittest.TestCase,
    first,
    second,
) -> None:
    image = Image.new("RGB", (40, 20), "white")
    source = io.BytesIO()
    image.save(source, format="PNG")
    _, normalized, staging_key = first.stage(source.getvalue())
    final_key = first.promote(
        staging_key,
        "workspace-one",
        "statement-one",
        "attachment-one",
    )
    case.assertEqual(normalized.content, second.read(final_key))
    case.assertIn(final_key, second.list_keys())
    case.assertEqual("ready", second.readiness()["status"])

    exact = b"exact-migration-content"
    checksum = hashlib.sha256(exact).hexdigest()
    exact_key = "workspace-two/statement-two/attachment-two.jpg"
    second.write_exact(exact_key, exact, checksum)
    case.assertEqual(exact, first.read(exact_key))
    with case.assertRaisesRegex(MediaError, "already exists"):
        first.write_exact(exact_key, exact, checksum)
    first.delete(exact_key)
    case.assertNotIn(exact_key, second.list_keys())


class ProviderResponse(io.BytesIO):
    def __init__(self, content: bytes = b"", headers: dict[str, str] | None = None):
        super().__init__(content)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AttachmentStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = AttachmentStore(Path(self.tempdir.name))
        self.store.initialize()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_normalization_uses_signature_and_removes_source_metadata(self) -> None:
        image = Image.new("RGB", (120, 60), "white")
        exif = Image.Exif()
        exif[270] = "Private field note"
        source = io.BytesIO()
        image.save(source, format="JPEG", exif=exif)

        normalized = self.store.normalize(source.getvalue())

        self.assertEqual("image/jpeg", normalized.media_type)
        self.assertEqual((120, 60), (normalized.width, normalized.height))
        with Image.open(io.BytesIO(normalized.content)) as result:
            self.assertEqual({}, dict(result.getexif()))

    def test_invalid_image_signature_is_rejected(self) -> None:
        with self.assertRaisesRegex(MediaError, "valid JPEG, PNG, WebP, HEIC, or HEIF"):
            self.store.normalize(b"not-an-image")

    def test_staged_photo_promotes_to_opaque_statement_path(self) -> None:
        image = Image.new("RGB", (32, 32), "white")
        source = io.BytesIO()
        image.save(source, format="PNG")
        stage_id, _, staging_key = self.store.stage(source.getvalue())

        final_key = self.store.promote(
            staging_key,
            "workspace-one",
            "statement-one",
            "attachment-one",
        )

        self.assertNotIn(stage_id, final_key)
        self.assertEqual(
            "workspace-one/statement-one/attachment-one.jpg",
            final_key,
        )
        self.assertTrue(self.store.read(final_key).startswith(b"\xff\xd8"))

    def test_large_photo_is_automatically_reduced_to_provider_target(self) -> None:
        image = Image.effect_noise((3000, 3000), 100).convert("RGB")
        source = io.BytesIO()
        image.save(source, format="JPEG", quality=96)
        self.assertLess(len(source.getvalue()), 10 * 1024 * 1024)

        normalized = self.store.normalize(source.getvalue())

        self.assertLessEqual(len(normalized.content), TARGET_PHOTO_BYTES)
        self.assertLessEqual(normalized.width * normalized.height, 24_000_000)

    def test_heif_photo_is_normalized_when_heif_support_is_installed(self) -> None:
        try:
            import pillow_heif
        except ImportError:
            self.skipTest("pillow-heif is not installed in this test runtime")
        image = Image.new("RGB", (120, 60), "white")
        source = io.BytesIO()
        pillow_heif.from_pillow(image).save(source)

        normalized = self.store.normalize(source.getvalue())

        self.assertEqual("image/jpeg", normalized.media_type)
        self.assertEqual((120, 60), (normalized.width, normalized.height))

    def test_shared_attachment_storage_contract(self) -> None:
        exercise_attachment_storage_contract(self, self.store, self.store)


class FakeBlobDownload:
    def __init__(self, content: bytes):
        self.content = content

    def readall(self) -> bytes:
        return self.content


class FakeBlob:
    def __init__(self, values: dict[str, bytes], key: str):
        self.values = values
        self.key = key

    def download_blob(self) -> FakeBlobDownload:
        if self.key not in self.values:
            error = RuntimeError("not found")
            error.status_code = 404
            raise error
        return FakeBlobDownload(self.values[self.key])

    def upload_blob(self, content: bytes, *, overwrite: bool) -> None:
        if not overwrite and self.key in self.values:
            error = RuntimeError("already exists")
            error.status_code = 409
            raise error
        self.values[self.key] = content

    def delete_blob(self) -> None:
        self.values.pop(self.key, None)


class FakeContainer:
    def __init__(self, public_access: str | None = "disabled"):
        self.values: dict[str, bytes] = {}
        self.public_access = public_access

    def create_container(self) -> None:
        return None

    def upload_blob(
        self,
        *,
        name: str,
        data: bytes,
        overwrite: bool,
        metadata: dict[str, str],
    ) -> None:
        del metadata
        FakeBlob(self.values, name).upload_blob(data, overwrite=overwrite)

    def get_blob_client(self, key: str) -> FakeBlob:
        return FakeBlob(self.values, key)

    def download_blob(self, key: str) -> FakeBlobDownload:
        return FakeBlob(self.values, key).download_blob()

    def delete_blob(self, key: str, *, delete_snapshots: str) -> None:
        del delete_snapshots
        FakeBlob(self.values, key).delete_blob()

    def get_container_properties(self) -> dict[str, str]:
        return {"public_access": self.public_access}

    def list_blobs(self):
        return [SimpleNamespace(name=key) for key in self.values]


class AzureBlobAttachmentStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.container = FakeContainer()
        self.first = AzureBlobAttachmentStore(
            account_url="https://example.blob.core.windows.net",
            container="attachments",
            credential=object(),
        )
        self.first._container_client = self.container
        self.second = AzureBlobAttachmentStore(
            account_url="https://example.blob.core.windows.net",
            container="attachments",
            credential=object(),
        )
        self.second._container_client = self.container

    def test_promoted_attachment_is_available_to_another_replica(self) -> None:
        self.container.values["staging/stage-one.jpg"] = b"normalized-photo"

        key = self.first.promote(
            "staging/stage-one.jpg",
            "workspace-one",
            "statement-one",
            "attachment-one",
        )

        self.assertEqual(
            "workspace-one/statement-one/attachment-one.jpg",
            key,
        )
        self.assertEqual(b"normalized-photo", self.second.read(key))
        self.assertNotIn("staging/stage-one.jpg", self.container.values)

    def test_invalid_object_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(MediaError, "reference is invalid"):
            self.first.read("../other-workspace/photo.jpg")

    def test_readiness_does_not_expose_account_url(self) -> None:
        readiness = self.first.readiness()
        self.assertEqual("ready", readiness["status"])
        self.assertEqual("azure_blob", readiness["backend"])
        self.assertNotIn("account_url", readiness)

    def test_exact_key_write_lists_and_rejects_overwrite(self) -> None:
        content = b"migrated-photo"
        checksum = hashlib.sha256(content).hexdigest()

        self.first.write_exact(
            "workspace-one/statement-one/attachment-one.jpg",
            content,
            checksum,
        )

        self.assertEqual(
            ["workspace-one/statement-one/attachment-one.jpg"],
            self.second.list_keys(),
        )
        self.assertEqual(
            content,
            self.second.read("workspace-one/statement-one/attachment-one.jpg"),
        )
        with self.assertRaisesRegex(MediaError, "already exists"):
            self.first.write_exact(
                "workspace-one/statement-one/attachment-one.jpg",
                content,
                checksum,
            )

    def test_public_container_is_not_ready(self) -> None:
        store = AzureBlobAttachmentStore(
            account_url="https://example.blob.core.windows.net",
            container="attachments",
            credential=object(),
        )
        store._container_client = FakeContainer(public_access="blob")

        self.assertEqual("unavailable", store.readiness()["status"])


@unittest.skipUnless(
    os.getenv("MELVAN_TEST_AZURITE_CONNECTION_STRING"),
    "Azurite integration is not configured",
)
class AzuriteAttachmentStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.container = f"melvan-test-{uuid.uuid4().hex}"
        self.first = AzureBlobAttachmentStore(
            account_url=None,
            container=self.container,
            connection_string=os.environ["MELVAN_TEST_AZURITE_CONNECTION_STRING"],
        )
        self.first.initialize()
        self.second = AzureBlobAttachmentStore(
            account_url=None,
            container=self.container,
            connection_string=os.environ["MELVAN_TEST_AZURITE_CONNECTION_STRING"],
        )

    def tearDown(self) -> None:
        try:
            self.first._client().delete_container()
        except Exception:
            pass

    def test_private_cross_replica_contract_and_exact_copy(self) -> None:
        exercise_attachment_storage_contract(self, self.first, self.second)


class AzureSpeechTranscriberTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            speech_endpoint=(
                "https://melvan-speech.example.test/stt/speech/recognition/"
                "conversation/cognitiveservices/v1"
            ),
            speech_language="en-US",
            speech_token_scope="https://cognitiveservices.azure.com/.default",
            azure_managed_identity_client_id="identity-client-id",
        )
        self.transcriber = AzureSpeechTranscriber(self.settings)

    @staticmethod
    def wav(seconds: float = 0.1) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(16_000)
            recording.writeframes(b"\x00\x00" * int(16_000 * seconds))
        return output.getvalue()

    def test_short_wav_uses_managed_identity_speech_contract(self) -> None:
        response = io.BytesIO(json.dumps({"DisplayText": "A program result."}).encode())
        with (
            patch.object(self.transcriber, "_get_token", return_value="token"),
            patch("urllib.request.urlopen", return_value=response) as open_url,
        ):
            result = self.transcriber.transcribe(self.wav(), "audio/wav")

        request = open_url.call_args.args[0]
        self.assertIn("language=en-US&format=detailed", request.full_url)
        self.assertEqual("Bearer token", request.headers["Authorization"])
        self.assertEqual(
            "audio/wav; codecs=audio/pcm; samplerate=16000",
            request.headers["Content-type"],
        )
        self.assertEqual("A program result.", result.text)
        self.assertEqual("azure-speech", result.provider)

    def test_recording_over_sixty_seconds_is_rejected_before_provider_call(self) -> None:
        with (
            patch.object(self.transcriber, "_get_token", return_value="token"),
            patch("urllib.request.urlopen") as open_url,
            self.assertRaisesRegex(MediaError, "60 seconds or shorter"),
        ):
            self.transcriber.transcribe(self.wav(60.01), "audio/wav")

        open_url.assert_not_called()

    def test_wrong_wav_shape_is_rejected(self) -> None:
        output = io.BytesIO()
        with wave.open(output, "wb") as recording:
            recording.setnchannels(2)
            recording.setsampwidth(2)
            recording.setframerate(44_100)
            recording.writeframes(b"\x00\x00\x00\x00" * 100)

        with self.assertRaisesRegex(MediaError, "format is not supported"):
            self.transcriber.transcribe(output.getvalue(), "audio/wav")


class AzureDocumentIntelligenceTextExtractorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            ocr_endpoint="https://melvan-ocr.example.test",
            ocr_api_version="2024-11-30",
            ocr_token_scope="https://cognitiveservices.azure.com/.default",
            azure_managed_identity_client_id="identity-client-id",
        )
        self.extractor = AzureDocumentIntelligenceTextExtractor(self.settings)

    def test_read_operation_uses_managed_identity_and_returns_text(self) -> None:
        operation = (
            "https://melvan-ocr.example.test/documentintelligence/"
            "documentModels/prebuilt-read/analyzeResults/result-one"
            "?api-version=2024-11-30"
        )
        accepted = ProviderResponse(headers={"Operation-Location": operation})
        completed = ProviderResponse(
            json.dumps(
                {
                    "status": "succeeded",
                    "analyzeResult": {"content": "Workshop notes\nDecision recorded"},
                }
            ).encode()
        )
        with (
            patch.object(self.extractor, "_get_token", return_value="token"),
            patch(
                "urllib.request.urlopen",
                side_effect=[accepted, completed],
            ) as open_url,
        ):
            result = self.extractor.extract(b"jpeg", "image/jpeg")

        analyze_request = open_url.call_args_list[0].args[0]
        poll_request = open_url.call_args_list[1].args[0]
        self.assertIn("prebuilt-read:analyze", analyze_request.full_url)
        self.assertIn("api-version=2024-11-30", analyze_request.full_url)
        self.assertEqual("Bearer token", analyze_request.headers["Authorization"])
        self.assertEqual("image/jpeg", analyze_request.headers["Content-type"])
        self.assertEqual(operation, poll_request.full_url)
        self.assertEqual("Workshop notes\nDecision recorded", result.text)
        self.assertEqual("azure-document-intelligence", result.provider)
        self.assertEqual("success", result.outcome)

    def test_valid_no_text_result_is_not_a_provider_failure(self) -> None:
        operation = (
            "https://melvan-ocr.example.test/documentintelligence/"
            "documentModels/prebuilt-read/analyzeResults/result-two"
        )
        with (
            patch.object(self.extractor, "_get_token", return_value="token"),
            patch(
                "urllib.request.urlopen",
                side_effect=[
                    ProviderResponse(headers={"Operation-Location": operation}),
                    ProviderResponse(
                        json.dumps(
                            {"status": "succeeded", "analyzeResult": {"content": ""}}
                        ).encode()
                    ),
                ],
            ),
        ):
            result = self.extractor.extract(b"jpeg", "image/jpeg")

        self.assertEqual("", result.text)
        self.assertEqual("no_text", result.outcome)

    def test_untrusted_operation_location_is_rejected_before_polling(self) -> None:
        with (
            patch.object(self.extractor, "_get_token", return_value="token"),
            patch(
                "urllib.request.urlopen",
                return_value=ProviderResponse(
                    headers={
                        "Operation-Location": (
                            "https://attacker.example/documentintelligence/results/one"
                        )
                    }
                ),
            ) as open_url,
            self.assertRaisesRegex(MediaError, "invalid operation reference"),
        ):
            self.extractor.extract(b"jpeg", "image/jpeg")

        self.assertEqual(1, open_url.call_count)


if __name__ == "__main__":
    unittest.main()
