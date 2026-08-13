"""
Tool layer: web search, GIF search, file generation, and vision image handling.

Tools are driven by a simple, reliable text protocol that the model triggers
inside its reply (documented in personality.TOOL_INSTRUCTION):
  * [SEARCH: query]   -> the bot performs a web search and re-asks the model
  * [GIF: term]       -> the bot sends a GIF as a separate animation message
  * [FILE:name.txt]   -> the bot sends file content as a document
                        content here [/FILE]
  * [WS_LIST]         -> list files in the user's workspace
  * [WS_READ:path]    -> read a file from the user's workspace
  * [WS_WRITE:path]   -> write content to a file in the user's workspace
                        content here [/WS_WRITE]
  * [WS_DELETE:path]  -> delete a file from the user's workspace
  * [EVAL]...[/EVAL]  -> execute Python code in the user's workspace

This avoids depending on native function-calling support which varies across
models and simplifies error handling.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Optional

import httpx
from telegram import InputFile

from config import Config
from workspace import WorkspaceManager, WorkspaceError
from eval_tool import PythonEval, EvalError
from skill_manager import SkillManager, SkillError

GIF_RE = re.compile(r"\[GIF:(.*?)\]", re.IGNORECASE)
FILE_RE = re.compile(r"\[FILE:([^\]]+)\](.*?)\[/FILE\]", re.IGNORECASE | re.DOTALL)
SEARCH_RE = re.compile(r"\[SEARCH:(.*?)\]", re.IGNORECASE)
WS_LIST_RE = re.compile(r"\[WS_LIST\]", re.IGNORECASE)
WS_READ_RE = re.compile(r"\[WS_READ:([^\]]+)\]", re.IGNORECASE)
WS_WRITE_RE = re.compile(r"\[WS_WRITE:([^\]]+)\](.*?)\[/WS_WRITE\]", re.IGNORECASE | re.DOTALL)
WS_DELETE_RE = re.compile(r"\[WS_DELETE:([^\]]+)\]", re.IGNORECASE)
EVAL_RE = re.compile(r"\[EVAL\](.*?)\[/EVAL\]", re.IGNORECASE | re.DOTALL)
REQUIRE_RE = re.compile(r"^\s*#\s*REQUIRE:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
SKILL_RE = re.compile(r"\[SKILL:([^\]]+)\]", re.IGNORECASE)
SEND_FILE_RE = re.compile(r"\[SEND_FILE:([^\]]+)\]", re.IGNORECASE)


class Tools:
    def __init__(self, ollama, config: Config, logger, workspace: Optional[WorkspaceManager] = None, eval_tool: Optional[PythonEval] = None, skill_manager: Optional[SkillManager] = None):
        self.ollama = ollama
        self.config = config
        self.logger = logger
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        # Klipy GIF API: optional. Without a key the GIF tool is disabled so the
        # bot never crashes. Get a free production key: https://klipy.com/docs
        self.klipy_key = (config.KLIPY_API_KEY or "").strip()
        self.gif_enabled = bool(self.klipy_key)
        self.workspace = workspace
        self.eval_tool = eval_tool
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
        text = WS_LIST_RE.sub("", text)
        text = WS_READ_RE.sub("", text)
        text = WS_WRITE_RE.sub("", text)
        text = WS_DELETE_RE.sub("", text)
        text = EVAL_RE.sub("", text)
        text = SKILL_RE.sub("", text)
        text = SEND_FILE_RE.sub("", text)
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

    # ------------------------------------------------------------------- workspace
    async def ws_list(self, user_id: int, relpath: str = ".") -> str:
        if not self.workspace:
            return "(Workspace is not configured.)"
        try:
            entries = await self.workspace.list_files(user_id, relpath)
        except WorkspaceError as exc:
            return f"(Workspace error: {exc})"
        if not entries:
            return "(Workspace is empty.)"
        lines = []
        for entry in entries:
            label = "/" if entry["is_dir"] else ""
            size = entry["size"]
            human = f"{size // 1024}KB" if size >= 1024 else f"{size}B"
            lines.append(f"{entry['name']}{label} ({human})")
        return "Workspace files:\n" + "\n".join(lines)

    async def ws_read(self, user_id: int, relpath: str) -> str:
        if not self.workspace:
            return "(Workspace is not configured.)"
        try:
            content = await self.workspace.read_file(user_id, relpath)
        except WorkspaceError as exc:
            return f"(Workspace error: {exc})"
        return content

    async def ws_write(self, user_id: int, relpath: str, content: str) -> str:
        if not self.workspace:
            return "(Workspace is not configured.)"
        try:
            await self.workspace.write_file(user_id, relpath, content)
        except WorkspaceError as exc:
            return f"(Workspace error: {exc})"
        return f"(Wrote {relpath})"

    async def ws_delete(self, user_id: int, relpath: str) -> str:
        if not self.workspace:
            return "(Workspace is not configured.)"
        try:
            await self.workspace.delete_file(user_id, relpath)
        except WorkspaceError as exc:
            return f"(Workspace error: {exc})"
        return f"(Deleted {relpath})"

    # ------------------------------------------------------------------- eval
    async def run_eval(self, user_id: int, code: str) -> str:
        if not self.eval_tool:
            return "(Eval is not configured.)"
        try:
            install = [pkg.strip() for pkg in REQUIRE_RE.findall(code) if pkg.strip()]
            if install:
                cleaned = REQUIRE_RE.sub("", code)
            else:
                cleaned = code
            result = await self.eval_tool.execute(user_id, cleaned, install=install)
        except EvalError as exc:
            return f"(Eval error: {exc})"
        except Exception as exc:
            return f"(Eval error: {type(exc).__name__}: {exc})"
        lines = [
            f"Eval result (exit_code={result['exit_code']}, time={result['execution_time']}s):",
        ]
        if result["stdout"]:
            lines.append(result["stdout"].rstrip())
        if result["stderr"]:
            lines.append("STDERR:")
            lines.append(result["stderr"].rstrip())
        if result["files_created"]:
            lines.append("Files created: " + ", ".join(result["files_created"]))
        if not result["success"]:
            lines.append("(Execution failed)")
        return "\n".join(lines)

    # ------------------------------------------------------------------- skills
    async def read_skill(self, name: str) -> str:
        if not self.skill_manager:
            return "(Skills are not configured.)"
        try:
            content = self.skill_manager.read_skill(name)
        except SkillError as exc:
            return f"(Skill error: {exc})"
        return f"[SKILL: {name}]\n{content}\n[/END SKILL]"

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

    # ------------------------------------------------------------------ image
    @staticmethod
    def encode_image_bytes(data: bytes) -> str:
        """Return a base64 string suitable for the Ollama `images` field."""
        return base64.b64encode(data).decode("utf-8")
