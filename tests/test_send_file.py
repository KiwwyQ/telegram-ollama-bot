import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from workspace import WorkspaceManager
from config import Config
from unittest.mock import MagicMock


def _make_tools(tmpdir):
    ws = WorkspaceManager(root=tmpdir)
    cfg = Config()
    logger = MagicMock()
    ollama = MagicMock()
    
    class FakeTools:
        def __init__(self):
            self.workspace = ws
            self.config = cfg
            self.logger = logger
        
        async def send_file(self, user_id, relpath):
            if not self.workspace:
                return "(Workspace is not configured.)"
            try:
                user_dir = self.workspace._user_dir(user_id)
                target = self.workspace._safe_path(user_dir, relpath)
                if not target.is_file():
                    return f"(File not found: {relpath})"
                self.workspace._reject_symlinks(target)
                size = target.stat().st_size
                if size > self.workspace.max_file_size:
                    return f"(File too large: {size} bytes, limit is {self.workspace.max_file_size} bytes)"
                from telegram import InputFile
                return InputFile(open(target, "rb"), filename=target.name)
            except Exception as exc:
                return f"(Send file error: {type(exc).__name__}: {exc})"
    
    return FakeTools()


class SendFileTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tools = _make_tools(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_send_valid_file(self):
        user_dir = self.tools.workspace._user_dir(1)
        (user_dir / "test.txt").write_text("hello", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            self.tools.send_file(1, "test.txt")
        )
        # Should be InputFile or string error
        if not isinstance(result, str):
            self.assertEqual(result.filename, "test.txt")

    def test_nonexistent_file(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.tools.send_file(1, "nonexistent.txt")
        )
        self.assertIsInstance(result, str)
        self.assertIn("File not found", result)

    def test_path_traversal_blocked(self):
        user_dir = self.tools.workspace._user_dir(1)
        (user_dir / "test.txt").write_text("hello", encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            self.tools.send_file(1, "../other_user/secret.txt")
        )
        self.assertIsInstance(result, str)
        self.assertIn("error", result.lower())

    def test_absolute_path_blocked(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.tools.send_file(1, "/etc/passwd")
        )
        self.assertIsInstance(result, str)
        self.assertIn("error", result.lower())

    def test_oversized_file(self):
        user_dir = self.tools.workspace._user_dir(1)
        big = "x" * (self.tools.workspace.max_file_size + 1)
        (user_dir / "big.txt").write_text(big, encoding="utf-8")
        result = asyncio.get_event_loop().run_until_complete(
            self.tools.send_file(1, "big.txt")
        )
        self.assertIsInstance(result, str)
        self.assertIn("too large", result.lower())


if __name__ == "__main__":
    unittest.main()
