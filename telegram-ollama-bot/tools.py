"""
Tool layer: web search, GIF search, file generation, and vision image handling.

Tools are driven by a simple, reliable text protocol that the model triggers
inside its reply (documented in personality.TOOL_INSTRUCTION):
  * [SEARCH: query]   -> the bot performs a web search and re-asks the model
  * [GIF: term]       -> the bot sends a GIF as a separate animation message
  * [FILE:name.txt]   -> the bot sends file content as a document
                        content here [/FILE]

This avoids depending on native function-calling support which varies across
models and simplifies error handling.
"""
import base64
import io
import re

import httpx
from telegram import InputFile

from config import Config

GIF_RE = re.compile(r"\[GIF:(.*?)\]", re.IGNORECASE)
FILE_RE = re.compile(r"\[FILE:([^\]]+)\](.*?)\[/FILE\]", re.IGNORECASE | re.DOTALL)
SEARCH_RE = re.compile(r"\[SEARCH:(.*?)\]", re.IGNORECASE)


class Tools:
    def __init__(self, ollama, config: Config, logger):
        self.ollama = ollama
        self.config = config
        self.logger = logger
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    # ----------------------------------------------------------- marker helpers
    @staticmethod
    def extract_search_queries(text: str) -> list:
        return [q.strip() for q in SEARCH_RE.findall(text) if q.strip()]

    @staticmethod
    def strip_markers(text: str) -> str:
        text = GIF_RE.sub("", text)
        text = FILE_RE.sub("", text)
        text = SEARCH_RE.sub("", text)
        return text.strip()

    # --------------------------------------------------------------- web search
    async def do_web_search(self, api_key: str, query: str) -> str:
        return await self.ollama.web_search(api_key, query)

    # -------------------------------------------------------------------- GIF
    async def search_gif(self, query: str) -> str | None:
        """Search a free GIF source and return a direct media URL, or None.

        Uses Klipy's free, key-less GIF search API. The exact response shape is
        not guaranteed, so we defensively hunt for the first media URL in the
        JSON payload.
        """
        try:
            url = "https://api.klipy.com/api/v1/gif/search"
            resp = await self._http.get(url, params={"q": query, "limit": 8})
            if resp.status_code != 200:
                return None
            data = resp.json()
            media_url = self._first_media_url(data)
            return media_url
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

    # ------------------------------------------------------------------ image
    @staticmethod
    def encode_image_bytes(data: bytes) -> str:
        """Return a base64 string suitable for the Ollama `images` field."""
        return base64.b64encode(data).decode("utf-8")
