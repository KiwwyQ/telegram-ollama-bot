"""
Per-user workspace filesystem with strict path boundary enforcement.

Each Telegram user gets a dedicated directory under WORKSPACE_ROOT, keyed by
their Telegram user ID (integer). All operations resolve paths relative to that
directory and reject traversal, absolute paths, symlinks, and cross-user access.

The workspace path itself is chosen by trusted application code; the model only
sees relative filenames in tool markers.
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional


class WorkspaceError(Exception):
    """Raised when a filesystem operation is not allowed."""


class WorkspaceManager:
    def __init__(
        self,
        root: str = "workspaces",
        max_file_size: int = 5 * 1024 * 1024,
        max_workspace_size: int = 100 * 1024 * 1024,
        max_files: int = 1000,
    ) -> None:
        self.root = pathlib.Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_size = max_file_size
        self.max_workspace_size = max_workspace_size
        self.max_files = max_files

    def _user_dir(self, user_id: int) -> pathlib.Path:
        d = self.root / str(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_path(self, user_dir: pathlib.Path, relpath: str) -> pathlib.Path:
        if os.path.isabs(relpath):
            raise WorkspaceError("Absolute paths are not allowed.")
        normalized = os.path.normpath(relpath)
        if normalized.startswith("..") or "/.." in normalized or "\\.." in normalized:
            raise WorkspaceError("Path escapes workspace.")
        target = (user_dir / normalized).resolve()
        user_dir_resolved = user_dir.resolve()
        if target == user_dir_resolved:
            return target
        try:
            target.relative_to(user_dir_resolved)
        except ValueError:
            raise WorkspaceError("Path escapes workspace.") from None
        return target

    def _reject_symlinks(self, path: pathlib.Path) -> None:
        if path.is_symlink():
            raise WorkspaceError("Symlinks are not allowed.")
        for parent in [path] + list(path.parents):
            if parent == path.parent:
                break
            if parent.is_symlink():
                raise WorkspaceError("Symlinks are not allowed.")

    def _count_files(self, user_dir: pathlib.Path) -> int:
        return sum(1 for f in user_dir.rglob("*") if f.is_file())

    def _current_size(self, user_dir: pathlib.Path) -> int:
        return sum(f.stat().st_size for f in user_dir.rglob("*") if f.is_file())

    async def list_files(self, user_id: int, relpath: str = ".") -> list[dict]:
        user_dir = self._user_dir(user_id)
        target = self._safe_path(user_dir, relpath)
        if not target.is_dir():
            raise WorkspaceError("Not a directory.")
        entries = []
        for p in sorted(target.iterdir()):
            entries.append(
                {
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": p.stat().st_size if p.is_file() else 0,
                }
            )
        return entries

    async def read_file(self, user_id: int, relpath: str) -> str:
        user_dir = self._user_dir(user_id)
        target = self._safe_path(user_dir, relpath)
        if target.is_dir():
            raise WorkspaceError("Cannot read a directory.")
        if not target.exists():
            raise WorkspaceError("File not found.")
        self._reject_symlinks(target)
        size = target.stat().st_size
        if size > self.max_file_size:
            raise WorkspaceError(f"File too large ({size} > {self.max_file_size}).")
        return target.read_text(encoding="utf-8")

    async def write_file(self, user_id: int, relpath: str, content: str) -> None:
        user_dir = self._user_dir(user_id)
        target = self._safe_path(user_dir, relpath)
        if target.is_dir():
            raise WorkspaceError("Cannot overwrite a directory.")
        self._reject_symlinks(target.parent)
        content_size = len(content.encode("utf-8"))
        if content_size > self.max_file_size:
            raise WorkspaceError(f"File too large ({content_size} > {self.max_file_size}).")
        current_size = self._current_size(user_dir)
        if current_size + content_size > self.max_workspace_size:
            raise WorkspaceError("Workspace size limit exceeded.")
        current_files = self._count_files(user_dir)
        if current_files >= self.max_files and not target.exists():
            raise WorkspaceError("Workspace file limit exceeded.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def delete_file(self, user_id: int, relpath: str) -> None:
        user_dir = self._user_dir(user_id)
        target = self._safe_path(user_dir, relpath)
        if target.is_dir():
            raise WorkspaceError("Cannot delete a directory.")
        if not target.exists():
            raise WorkspaceError("File not found.")
        self._reject_symlinks(target)
        target.unlink()
