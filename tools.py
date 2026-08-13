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

This avoids depending on native function-calling support which varies across
models and simplifies error handling.
"""
import base64
import io
import re

import httpx
from telegram import InputFile

from config import Config
from workspace import WorkspaceManager, WorkspaceError

GIF_RE = re.compile(r"\[GIF:(.*?)\]", re.IGNORECASE)
FILE_RE = re.compile(r"\[FILE:([^\]]+)\](.*?)\[/FILE\]", re.IGNORECASE | re.DOTALL)
SEARCH_RE = re.compile(r"\[SEARCH:(.*?)\]", re.IGNORECASE)
WS_LIST_RE = re.compile(r"\[WS_LIST\]", re.IGNORECASE)
WS_READ_RE = re.compile(r"\[WS_READ:([^\]]+)\]", re.IGNORECASE)
WS_WRITE_RE = re.compile(r"\[WS_WRITE:([^\]]+)\](.*?)\[/WS_WRITE\]", re.IGNORECASE | re.DOTALL)
WS_DELETE_RE = re.compile(r"\[WS_DELETE:([^\]]+)\]", re.IGNORECASE)


class Tools:
    def __init__(self, ollama, config: Config, logger, workspace: Optional[WorkspaceManager] = None):
        self.ollama = ollama
        self.config = config
        self.logger = logger
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        # Klipy GIF API: optional. Without a key the GIF tool is disabled so the
        # bot never crashes. Get a free production key: https://klipy.com/docs
        self.klipy_key = (config.KLIPY_API_KEY or "").strip()
        self.gif_enabled = bool(self.klipy_key)
        self.workspace = workspace

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

    # ------------------------------------------------------------------ image
    @staticmethod
    def encode_image_bytes(data: bytes) -> str:
        """Return a base64 string suitable for the Ollama `images` field."""
        return base64.b64encode(data).decode("utf-8")
