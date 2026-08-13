"""
Ollama Cloud API client.

Official docs:
  * Overview : https://docs.ollama.com/cloud
  * Chat API : https://docs.ollama.com/api/chat
  * Web search: https://docs.ollama.com/capabilities/web-search
  * API index : https://docs.ollama.com/llms.txt
  * Keys      : https://ollama.com/settings/keys

The client talks to https://ollama.com (configurable via OLLAMA_BASE_URL) using
the user's personal API key in the `Authorization: Bearer <key>` header.

Endpoints used:
  * POST /api/chat         - chat completions (model, messages, stream, options)
  * POST /api/web_search   - web search (query, max_results) -> {results:[...]}
  * GET  /api/tags         - list available models (best effort, dynamic list)

All methods raise typed exceptions (AuthError, RateLimitError, OllamaError) so
the caller can present friendly, non-leaky messages to the user.
"""
import json

import httpx

from config import Config


class OllamaError(Exception):
    """Base error for Ollama Cloud issues."""


class AuthError(OllamaError):
    """Invalid / missing API key."""


class RateLimitError(OllamaError):
    """Free daily usage exhausted or hard rate limited."""


class ModelNotFoundError(OllamaError):
    """The requested model is not available on the Ollama server."""


class OllamaClient:
    def __init__(self, config: Config, logger):
        self.config = config
        self.logger = logger
        self.timeout = httpx.Timeout(150.0, connect=15.0)

    # ---------------------------------------------------------------- helpers
    def _headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _log_api_error(self, model, resp, exc):
        status = getattr(resp, "status_code", "n/a") if resp is not None else "n/a"
        body = ""
        if resp is not None:
            try:
                body = (resp.text or "")[:500]
            except Exception:
                body = "<unreadable>"
        self.logger.warning(
            "Ollama API error | model=%s | status=%s | exc=%s | body=%s",
            model,
            status,
            type(exc).__name__,
            body,
        )

    def _check_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise AuthError("Your Ollama key is invalid or missing.")
        if resp.status_code == 429:
            raise RateLimitError("Your free Ollama daily limit has been reached.")
        if resp.status_code == 404:
            body = resp.text or ""
            low = body.lower()
            if "model" in low and ("not found" in low or "no such" in low):
                raise ModelNotFoundError("Vision model not available on this Ollama server.")
        if resp.status_code >= 400:
            body = resp.text or ""
            low = body.lower()
            if any(k in low for k in ("limit", "quota", "exhausted", "exceed", "rate", "too many")):
                raise RateLimitError("Your free Ollama daily limit has been reached.")
            # Surface a generic, non-leaky error.
            raise OllamaError(f"Ollama request failed (HTTP {resp.status_code}).")

    # ------------------------------------------------------------------ chat
    async def chat(self, api_key: str, model: str, messages: list, stream: bool = False) -> str:
        """Return the assistant reply as a string."""
        if stream:
            return await self._chat_stream(api_key, model, messages)
        url = f"{self.config.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.7, "top_p": 0.9},
        }
        resp = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(api_key), json=payload)
                self._check_status(resp)
                data = resp.json()
                return data.get("message", {}).get("content", "")
        except (httpx.HTTPError, OllamaError) as exc:
            self._log_api_error(model, resp, exc)
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise OllamaError("Unexpected error while contacting Ollama.") from exc

    async def stream_chat(self, api_key: str, model: str, messages: list):
        """Async generator yielding text deltas as they arrive.

        Yields strings. If the endpoint does not support streaming (or errors),
        it yields the full response as a single chunk so callers can rely on it.
        """
        url = f"{self.config.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.7, "top_p": 0.9},
        }
        resp = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=self._headers(api_key), json=payload) as resp:
                    self._check_status(resp)
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(chunk, dict):
                            msg = chunk.get("message", {})
                            if isinstance(msg, dict) and msg.get("content"):
                                yield msg["content"]
                            if chunk.get("done"):
                                break
        except Exception as exc:
            self._log_api_error(model, resp, exc)
            self.logger.debug("streaming failed (%s); falling back to non-stream", type(exc).__name__)
            yield await self.chat(api_key, model, messages, stream=False)

    async def _chat_stream(self, api_key: str, model: str, messages: list) -> str:
        """Aggregate a streamed response into a single string."""
        pieces = []
        async for delta in self.stream_chat(api_key, model, messages):
            pieces.append(delta)
        return "".join(pieces)

    # ------------------------------------------------------------- web search
    async def web_search(self, api_key: str, query: str, max_results: int = 5) -> str:
        """Run a web search and return a normalized text block of results.

        Official request body: {"query": "...", "max_results": 5}  (max 10).
        Official response: {"results": [{"title", "url", "content"}, ...]}.
        """
        url = f"{self.config.OLLAMA_BASE_URL}/api/web_search"
        payload = {"query": query, "max_results": min(max(1, max_results), 10)}
        resp = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(api_key), json=payload)
                self._check_status(resp)
                data = resp.json()
                return self._normalize_search(data, query)
        except (httpx.HTTPError, OllamaError) as exc:
            self._log_api_error(query, resp, exc)
            raise
        except Exception:
            # Never leak internals; return a generic failure string the bot can show.
            return f"(Web search for '{query}' could not be completed right now.)"

    @staticmethod
    def _normalize_search(data: dict, query: str) -> str:
        """Turn the web search response into plain text.

        Primary shape: {"results": [{"title","url","content"}]}. We also tolerate
        small variations (data/hits/answer) defensively.
        """
        items = data.get("results") or data.get("data") or data.get("hits") or []
        if not items and isinstance(data.get("answer"), str):
            return data["answer"]
        if not items:
            # Dump whatever we got, truncated.
            return f"Search results for '{query}':\n" + json.dumps(data, ensure_ascii=False)[:2000]
        lines = []
        for i, item in enumerate(items[:6], 1):
            if isinstance(item, dict):
                title = item.get("title") or item.get("name") or ""
                snippet = item.get("content") or item.get("snippet") or item.get("description") or ""
                url = item.get("url") or item.get("link") or ""
                line = f"{i}. {title}".strip()
                if snippet:
                    line += f"\n   {snippet}"
                if url:
                    line += f"\n   {url}"
                lines.append(line)
            else:
                lines.append(f"{i}. {item}")
        return f"Web search results for '{query}':\n" + "\n".join(lines)

    # --------------------------------------------------------------- tags/list
    async def list_models(self, api_key: str) -> list:
        """Best-effort model listing. Returns [] on any failure."""
        url = f"{self.config.OLLAMA_BASE_URL}/api/tags"
        resp = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers(api_key))
                self._check_status(resp)
                data = resp.json()
                models = data.get("models") or []
                return [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
        except Exception as exc:
            self._log_api_error("(list_models)", resp, exc)
            return []
