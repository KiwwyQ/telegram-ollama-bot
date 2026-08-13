"""
Skill manager: read-only instruction files loaded from the skills directory.

Skills are Markdown instruction files that tell the AI how to accomplish a task
using the tools it already has. They are NOT executable code, NOT plugins, and
the AI cannot modify them.

Security rules:
  - Only files from the configured skills directory are loaded.
  - Path traversal is rejected.
  - Skill names must be simple identifiers (alphanumeric, hyphens, underscores).
  - Skills are read-only; the AI cannot write to the skills directory.
  - Skills do not override the main system prompt or security instructions.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


class SkillError(Exception):
    """Raised when a skill cannot be loaded."""


class SkillManager:
    def __init__(self, skills_dir: str = "skills") -> None:
        self.skills_dir = Path(skills_dir).resolve()

    def list_skills(self) -> list[str]:
        if not self.skills_dir.is_dir():
            return []
        names = []
        for entry in self.skills_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                names.append(entry.name)
        return sorted(names)

    def read_skill(self, name: str) -> str:
        if not name or not re.match(r"^[A-Za-z0-9_\-\u0080-\uFFFF]+$", name):
            raise SkillError(f"Invalid skill name: {name!r}")
        skill_dir = (self.skills_dir / name).resolve()
        if not str(skill_dir).startswith(str(self.skills_dir) + os.sep):
            raise SkillError("Path escapes skills directory.")
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise SkillError(f"Skill not found: {name}")
        try:
            return skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError(f"Skill unreadable: {name}") from exc

    def has_skill(self, name: str) -> bool:
        if not name or not re.match(r"^[A-Za-z0-9_\-\u0080-\uFFFF]+$", name):
            return False
        skill_dir = (self.skills_dir / name).resolve()
        if not str(skill_dir).startswith(str(self.skills_dir) + os.sep):
            return False
        return (skill_dir / "SKILL.md").is_file()
