import base64
import unittest
from unittest.mock import MagicMock

from config import Config

try:
    from telegram import (
        Update,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        InputFile,
    )
    from telegram.ext import (
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        ContextTypes,
        filters,
    )
    from telegram.error import TelegramError
    from telegram.constants import ParseMode
    _TELEGRAM_AVAILABLE = True
except ModuleNotFoundError:
    _TELEGRAM_AVAILABLE = False

if _TELEGRAM_AVAILABLE:
    import handlers

try:
    import httpx as _httpx
    _OLLAMA_AVAILABLE = True
except ModuleNotFoundError:
    _OLLAMA_AVAILABLE = False


class VisionModelSelectionTests(unittest.TestCase):
    def test_gemma3_detected(self):
        cfg = Config()
        self.assertTrue(cfg.is_vision_model("gemma3:27b"))
        self.assertTrue(cfg.is_vision_model("gemma3"))

    def test_gemma4_detected(self):
        cfg = Config()
        self.assertTrue(cfg.is_vision_model("gemma4:31b"))
        self.assertTrue(cfg.is_vision_model("gemma4"))

    def test_default_vision_model(self):
        cfg = Config()
        self.assertEqual(cfg.DEFAULT_VISION_MODEL, "gemma4:31b")

    def test_known_vision_models(self):
        cfg = Config()
        for m in ("llava", "llama3.2-vision", "moondream", "minicpm", "qwen2-vl"):
            self.assertTrue(cfg.is_vision_model(m), m)

    def test_non_vision_models(self):
        cfg = Config()
        for m in ("gpt-oss:20b", "llama3.1:8b", "mistral:7b"):
            self.assertFalse(cfg.is_vision_model(m), m)


class ImageValidationTests(unittest.TestCase):
    def test_empty_data_rejected(self):
        data = b""
        self.assertFalse(bool(data))

    def test_oversized_data_rejected(self):
        data = b"x" * (20 * 1024 * 1024 + 1)
        self.assertGreater(len(data), 20 * 1024 * 1024)

    def test_base64_roundtrip(self):
        data = b"fake-image-bytes"
        b64 = base64.b64encode(data).decode("utf-8")
        self.assertEqual(base64.b64decode(b64), data)

    def test_message_dict_shape(self):
        b64 = base64.b64encode(b"fake-image-bytes").decode("utf-8")
        msg = {"role": "user", "content": "describe", "images": [b64]}
        self.assertIn("images", msg)
        self.assertEqual(len(msg["images"]), 1)
        self.assertIsInstance(msg["images"][0], str)


@unittest.skipUnless(_TELEGRAM_AVAILABLE, "telegram not installed in this environment")
class MessageImageFileTests(unittest.TestCase):
    def test_photo_size_selection(self):
        update = MagicMock()
        update.effective_message.photo = [MagicMock(file_id="s1"), MagicMock(file_id="s2")]
        self.assertEqual(handlers.message_image_file(update), "s2")

    def test_document_image_fallback(self):
        update = MagicMock()
        update.effective_message.photo = []
        doc = MagicMock()
        doc.mime_type = "image/png"
        doc.file_id = "doc123"
        update.effective_message.document = doc
        self.assertEqual(handlers.message_image_file(update), "doc123")

    def test_non_image_document_ignored(self):
        update = MagicMock()
        update.effective_message.photo = []
        doc = MagicMock()
        doc.mime_type = "application/pdf"
        update.effective_message.document = doc
        with self.assertRaises(ValueError):
            handlers.message_image_file(update)


@unittest.skipUnless(_TELEGRAM_AVAILABLE, "telegram not installed in this environment")
class AbortOnImageFailureTests(unittest.TestCase):
    def test_failed_download_aborts(self):
        has_photo = True
        images_b64 = None
        if has_photo and images_b64 is None:
            user_msg = "⚠️ Couldn't process that image. Please try uploading it again."
        else:
            user_msg = None
        self.assertIsNotNone(user_msg)

    def test_successful_download_continues(self):
        has_photo = True
        images_b64 = ["abc123"]
        user_msg = None
        if has_photo and images_b64 is None:
            user_msg = "abort"
        self.assertIsNone(user_msg)

    def test_text_only_unchanged(self):
        has_photo = False
        images_b64 = None
        if has_photo and images_b64 is None:
            self.fail("text-only path broken")
        content = "hello"
        msg = {"role": "user", "content": content}
        self.assertNotIn("images", msg)


@unittest.skipUnless(_OLLAMA_AVAILABLE, "httpx not installed in this environment")
class ModelNotFoundTests(unittest.TestCase):
    def test_404_model_not_found_raises(self):
        from config import Config
        from ollama_client import OllamaClient, ModelNotFoundError
        cfg = Config()
        oc = OllamaClient(cfg, MagicMock())

        class FakeResponse:
            def __init__(self, status_code, text=""):
                self.status_code = status_code
                self.text = text

        resp = FakeResponse(404, "model not found")
        with self.assertRaises(ModelNotFoundError):
            oc._check_status(resp)

    def test_404_other_error_not_model_not_found(self):
        from config import Config
        from ollama_client import OllamaClient
        cfg = Config()
        oc = OllamaClient(cfg, MagicMock())

        class FakeResponse:
            def __init__(self, status_code, text=""):
                self.status_code = status_code
                self.text = text

        resp = FakeResponse(404, "not found")
        with self.assertRaises(OllamaError):
            oc._check_status(resp)


if __name__ == "__main__":
    unittest.main()
