"""
Tool layer: web search, GIF search, file generation, shell execution, and vision image handling.

Tools are driven by a simple, reliable text protocol that the model triggers
inside its reply (documented in personality.TOOL_INSTRUCTION):
  * [SEARCH: query]   -> the bot performs a web search and re-asks the model
  * [GIF: term]       -> the bot sends a GIF as a separate animation message
  * [FILE:name.txt]   -> the bot sends file content as a document
                        content here [/FILE]
  * [SEND_FILE:path]  -> send a file from the user's workspace
  * [SHELL]command[/SHELL] -> run a shell command in the user's workspace (sandbox-only)

This avoids depending on native function-calling support which varies across
models and simplifies error handling.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from telegram import InputFile

from config import Config
from workspace import WorkspaceManager, WorkspaceError
from skill_manager import SkillManager, SkillError

GIF_RE = re.compile(r"\[GIF:(.*?)\]", re.IGNORECASE)
FILE_RE = re.compile(r"\[FILE:([^\]]+)\](.*?)\[/FILE\]", re.IGNORECASE | re.DOTALL)
SEARCH_RE = re.compile(r"\[SEARCH:(.*?)\]", re.IGNORECASE)
SKILL_RE = re.compile(r"\[SKILL:([^\]]+)\]", re.IGNORECASE)
SEND_FILE_RE = re.compile(r"\[SEND_FILE:([^\]]+)\]", re.IGNORECASE)
SHELL_RE = re.compile(r"\[SHELL\](.*?)(?:\[/SHELL\]|(?=\n\n|\Z))", re.IGNORECASE | re.DOTALL)


class Tools:
    def __init__(self, ollama, config: Config, logger, workspace: Optional[WorkspaceManager] = None, skill_manager: Optional[SkillManager] = None):
        self.ollama = ollama
        self.config = config
        self.logger = logger
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        self.klipy_key = (config.KLIPY_API_KEY or "").strip()
        self.gif_enabled = bool(self.klipy_key)
        self.workspace = workspace
        self.skill_manager = skill_manager

    # ----------------------------------------------------------- marker helpers
    @staticmethod
    def extract_search_queries(text: str) -> list:
        return [q.strip() for q in SEARCH_RE.findall(text) if q.strip()]

    @staticmethod
    def strip_markers(text: str) -> str:
        text = GIF_RE.sub("", text)
        text = FILE_RE.sub("", text)
        text = SEARCH_RE.sub("", text)
        text = SKILL_RE.sub("", text)
        text = SEND_FILE_RE.sub("", text)
        text = SHELL_RE.sub("", text)
        return text.strip()

    # --------------------------------------------------------------- web search
    async def do_web_search(self, api_key: str, query: str) -> str:
        return await self.ollama.web_search(api_key, query)

    # -------------------------------------------------------------------- GIF
    async def search_gif(self, query: str) -> str | None:
        """Search Klipy for a GIF and return a direct media URL, or None.

        Requires KLIPY_API_KEY (set in the environment). If the key is missing
        the GIF tool is disabled and this returns None without erroring.

        Endpoint pattern (Klipy):
          GET https://api.klipy.com/api/v1/{API_KEY}/gifs/search?q=...
        The exact JSON shape can vary, so we defensively hunt for the first
        media URL in the response.
        """
        if not self.gif_enabled:
            return None
        try:
            url = f"https://api.klipy.com/api/v1/{self.klipy_key}/gifs/search"
            resp = await self._http.get(url, params={"q": query, "limit": 8})
            if resp.status_code != 200:
                return None
            data = resp.json()
            return self._first_media_url(data)
        except Exception as exc:
            self.logger.debug("GIF search failed: %s", type(exc).__name__)
            return None

    @staticmethod
    def _first_media_url(obj) -> str | None:
        """Recursively find the first http(s) URL that looks like a GIF/MP4."""
        if isinstance(obj, dict):
            for value in obj.values():
                found = Tools._first_media_url(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = Tools._first_media_url(item)
                if found:
                    return found
        elif isinstance(obj, str):
            if obj.startswith("http") and (".gif" in obj or ".mp4" in obj or "media" in obj or "klipy" in obj):
                return obj
        return None

    # ------------------------------------------------------------------- file
    @staticmethod
    def make_document(filename: str, content: str) -> InputFile:
        return InputFile(io.BytesIO(content.encode("utf-8")), filename=filename)

    async def send_file(self, user_id: int, relpath: str) -> InputFile:
        if not self.workspace:
            raise WorkspaceError("Workspace is not configured.")
        user_dir = self.workspace._user_dir(user_id)
        target = self.workspace._safe_path(user_dir, relpath)
        if not target.is_file():
            raise WorkspaceError(f"File not found: {relpath}")
        self.workspace._reject_symlinks(target)
        size = target.stat().st_size
        if size > self.workspace.max_file_size:
            raise WorkspaceError(f"File too large: {size} bytes, limit is {self.workspace.max_file_size} bytes")
        return InputFile(open(target, "rb"), filename=target.name)

    # ------------------------------------------------------------------- shell
    async def do_shell(self, user_id: int, command: str) -> str:
        if not command or not command.strip():
            return "(Shell error: empty command)"
        cmd = command.strip()
        lower = cmd.lower()
        blocked = [
            "sudo", "su ", "passwd", "shadow", "shutdown", "reboot",
            "mkfs", "fdisk", "dd if=", "kill -9", "iptables", "ufw ",
            "chmod 777 /", "rm -rf /", "nc ", "ncat",
            "python -c", "perl -e", "ruby -e",
        ]
        for b in blocked:
            if b in lower:
                return f"(Shell error: command blocked for security: {b.strip()})"
        cwd = None
        if self.workspace:
            try:
                cwd = str(self.workspace._user_dir(user_id))
            except Exception:
                cwd = None
        env = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
        }
        if cwd:
            env["HOME"] = cwd
        try:
            loop = asyncio.get_event_loop()
            def _run():
                return subprocess.run(
                    cmd,
                    shell=True,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=self.config.SHELL_TIMEOUT,
                    env=env,
                )
            proc = await loop.run_in_executor(None, _run)
        except subprocess.TimeoutExpired:
            return f"(Shell error: command timed out after {self.config.SHELL_TIMEOUT}s)"
        except Exception as exc:
            return f"(Shell error: {type(exc).__name__}: {exc})"
        out = (proc.stdout or "")[: self.config.SHELL_MAX_OUTPUT]
        err = (proc.stderr or "")[: self.config.SHELL_MAX_OUTPUT // 2]
        if proc.returncode != 0:
            msg = f"(exit {proc.returncode})"
            if err:
                msg += f"\n{err}"
            return msg
        if not out and not err:
            return "(command completed with no output)"
        return out if out else err

    # ------------------------------------------------------------------- skills
    async def read_skill(self, name: str) -> str:
        if not self.skill_manager:
            return "(Skills are not configured.)"
        try:
            content = self.skill_manager.read_skill(name)
        except SkillError as exc:
            return f"(Skill error: {exc})"
        return f"[SKILL: {name}]\n{content}\n[/END SKILL]"

    # ------------------------------------------------------------------ image
    @staticmethod
    def encode_image_bytes(data: bytes) -> str:
        """Return a base64 string suitable for the Ollama `images` field."""
        return base64.b64encode(data).decode("utf-8")
