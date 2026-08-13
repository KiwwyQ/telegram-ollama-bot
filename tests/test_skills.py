import os
import tempfile
import unittest
from pathlib import Path

from skill_manager import SkillManager, SkillError


class SkillManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.manager = SkillManager(skills_dir=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_empty_when_no_skills(self):
        self.assertEqual(self.manager.list_skills(), [])

    def test_list_single_skill(self):
        (Path(self.tmpdir.name) / "pdf").mkdir()
        (Path(self.tmpdir.name) / "pdf" / "SKILL.md").write_text("# PDF Skill", encoding="utf-8")
        self.assertEqual(self.manager.list_skills(), ["pdf"])

    def test_list_multiple_skills(self):
        for name in ("pdf", "excel", "web"):
            (Path(self.tmpdir.name) / name).mkdir()
            (Path(self.tmpdir.name) / name / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
        self.assertEqual(self.manager.list_skills(), ["excel", "pdf", "web"])

    def test_read_valid_skill(self):
        (Path(self.tmpdir.name) / "pdf").mkdir()
        content = "# PDF Skill\n\nUse pypdf to read PDFs."
        (Path(self.tmpdir.name) / "pdf" / "SKILL.md").write_text(content, encoding="utf-8")
        self.assertEqual(self.manager.read_skill("pdf"), content)

    def test_missing_skill_raises(self):
        with self.assertRaises(SkillError):
            self.manager.read_skill("nonexistent")

    def test_traversal_blocked(self):
        with self.assertRaises(SkillError):
            self.manager.read_skill("../etc/passwd")

    def test_absolute_path_blocked(self):
        with self.assertRaises(SkillError):
            self.manager.read_skill("/etc/passwd")

    def test_empty_name_rejected(self):
        with self.assertRaises(SkillError):
            self.manager.read_skill("")

    def test_malformed_name_rejected(self):
        with self.assertRaises(SkillError):
            self.manager.read_skill("../../../etc/passwd")

    def test_subdirectory_skill_is_listed(self):
        (Path(self.tmpdir.name) / "nested").mkdir(parents=True)
        (Path(self.tmpdir.name) / "nested" / "SKILL.md").write_text("hidden", encoding="utf-8")
        self.assertEqual(self.manager.list_skills(), ["nested"])

    def test_non_skill_directory_ignored(self):
        (Path(self.tmpdir.name) / "random").mkdir()
        (Path(self.tmpdir.name) / "random" / "other.txt").write_text("not a skill", encoding="utf-8")
        self.assertEqual(self.manager.list_skills(), [])

    def test_has_skill_true(self):
        (Path(self.tmpdir.name) / "pdf").mkdir()
        (Path(self.tmpdir.name) / "pdf" / "SKILL.md").write_text("x", encoding="utf-8")
        self.assertTrue(self.manager.has_skill("pdf"))

    def test_has_skill_false(self):
        self.assertFalse(self.manager.has_skill("nonexistent"))

    def test_unicode_skill_name(self):
        (Path(self.tmpdir.name) / "навык").mkdir()
        (Path(self.tmpdir.name) / "навык" / "SKILL.md").write_text("unicode", encoding="utf-8")
        self.assertEqual(self.manager.list_skills(), ["навык"])
        self.assertEqual(self.manager.read_skill("навык"), "unicode")


if __name__ == "__main__":
    unittest.main()
