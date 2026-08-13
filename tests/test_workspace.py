import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from workspace import WorkspaceManager, WorkspaceError


class WorkspaceSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ws = WorkspaceManager(root=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_traversal_blocked(self):
        with self.assertRaises(WorkspaceError):
            self.ws._safe_path(self.ws._user_dir(1), "../other_user/secret.txt")

    def test_absolute_path_blocked(self):
        with self.assertRaises(WorkspaceError):
            self.ws._safe_path(self.ws._user_dir(1), "/etc/passwd")

    def test_symlink_escape_blocked_read(self):
        user_dir = self.ws._user_dir(1)
        outside = Path(self.tmpdir.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = user_dir / "link.txt"
        link.symlink_to(outside)
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "link.txt"))

    def test_symlink_escape_blocked_write(self):
        user_dir = self.ws._user_dir(1)
        outside_dir = Path(self.tmpdir.name) / "outside"
        outside_dir.mkdir()
        link = user_dir / "link"
        link.symlink_to(outside_dir)
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "link/pwned.txt", "evil"))

    def test_another_user_workspace_inaccessible(self):
        self.ws._user_dir(2)
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(2, "secret.txt", "user2 data"))
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "../2/secret.txt"))

    def test_normal_write_and_read(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "notes.txt", "hello world"))
        content = asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "notes.txt"))
        self.assertEqual(content, "hello world")

    def test_normal_list(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "a.txt", "a"))
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "b.txt", "b"))
        entries = asyncio.get_event_loop().run_until_complete(self.ws.list_files(1))
        names = [e["name"] for e in entries]
        self.assertIn("a.txt", names)
        self.assertIn("b.txt", names)

    def test_normal_delete(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "old.txt", "bye"))
        asyncio.get_event_loop().run_until_complete(self.ws.delete_file(1, "old.txt"))
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "old.txt"))

    def test_write_to_subdir_creates_it(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "sub/data.txt", "nested"))
        content = asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "sub/data.txt"))
        self.assertEqual(content, "nested")

    def test_list_subdir(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "sub/data.txt", "x"))
        entries = asyncio.get_event_loop().run_until_complete(self.ws.list_files(1, "sub"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "data.txt")

    def test_unicode_filename(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "файл.txt", "unicode"))
        content = asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "файл.txt"))
        self.assertEqual(content, "unicode")

    def test_overwrite_existing_file(self):
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "f.txt", "v1"))
        asyncio.get_event_loop().run_until_complete(self.ws.write_file(1, "f.txt", "v2"))
        self.assertEqual(
            asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "f.txt")),
            "v2",
        )

    def test_delete_nonexistent_raises(self):
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.delete_file(1, "nope.txt"))

    def test_read_directory_raises(self):
        with self.assertRaises(WorkspaceError):
            asyncio.get_event_loop().run_until_complete(self.ws.read_file(1, "."))


if __name__ == "__main__":
    unittest.main()
