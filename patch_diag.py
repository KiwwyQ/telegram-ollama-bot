import io

def patch_ollama_client():
    p = "ollama_client.py"
    s = io.open(p, encoding="utf-8").read()

    s = s.replace(
        '''    def _check_status(self, resp: httpx.Response) -> None:
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
''',
        '''    def _check_status(self, resp: httpx.Response) -> None:
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
''',
        1,
    )

    # chat
    s = s.replace(
        '''    async def chat(self, api_key: str, model: str, messages: list, stream: bool = False) -> str:
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
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(api_key), json=payload)
                self._check_status(resp)
                data = resp.json()
                return data.get("message", {}).get("content", "")
        except (httpx.HTTPError, OllamaError):
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise OllamaError("Unexpected error while contacting Ollama.") from exc
''',
        '''    async def chat(self, api_key: str, model: str, messages: list, stream: bool = False) -> str:
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
''',
        1,
    )

    # stream_chat
    s = s.replace(
        '''        try:
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
            self.logger.debug("streaming failed (%s); falling back to non-stream", type(exc).__name__)
            yield await self.chat(api_key, model, messages, stream=False)
''',
        '''        resp = None
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
''',
        1,
    )

    # web_search
    s = s.replace(
        '''        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(api_key), json=payload)
                self._check_status(resp)
                data = resp.json()
                return self._normalize_search(data, query)
        except (httpx.HTTPError, OllamaError):
            raise
        except Exception:
            # Never leak internals; return a generic failure string the bot can show.
            return f"(Web search for '{query}' could not be completed right now.)"
''',
        '''        resp = None
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
''',
        1,
    )

    # list_models
    s = s.replace(
        '''        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, headers=self._headers(api_key))
                self._check_status(resp)
                data = resp.json()
                models = data.get("models") or []
                return [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
        except Exception as exc:
            self.logger.debug("list_models failed: %s", type(exc).__name__)
            return []
''',
        '''        resp = None
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
''',
        1,
    )

    io.open(p, "w", encoding="utf-8").write(s)
    print("ollama_client.py patched")


def patch_handlers():
    p = "handlers.py"
    s = io.open(p, encoding="utf-8").read()

    s = s.replace(
        '''async def _finalize_error(bot, chat_id, status_id, ctx, exc, parse_mode, message):
    if exc is not None:
        ctx.logger.warning("finalize error: %s", type(exc).__name__)
''',
        '''async def _finalize_error(bot, chat_id, status_id, ctx, exc, parse_mode, message, model=None):
    if exc is not None:
        ctx.logger.warning("finalize error | model=%s | exc=%s", model, type(exc).__name__)
''',
        1,
    )

    # image failure log
    s = s.replace(
        '''            ctx.logger.warning("image processing failed: %s", type(exc).__name__)
''',
        '''            ctx.logger.warning("image processing failed | model=%s | exc=%s", model, type(exc).__name__)
''',
        1,
    )

    # add model=model to all _finalize_error call sites inside _generate
    # these are unique enough; append model=model before the closing paren
    repls = [
        (
            'await _finalize_error(bot, chat.id, status_id, ctx, e, parse_mode)\n',
            'await _finalize_error(bot, chat.id, status_id, ctx, e, parse_mode, model=model)\n',
        ),
        (
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "🚫 Your free Ollama daily limit has been reached. "\n                              "It usually resets ~24h after your first request today (or at midnight UTC).")\n',
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "🚫 Your free Ollama daily limit has been reached. "\n                              "It usually resets ~24h after your first request today (or at midnight UTC).",\n                              model=model)\n',
        ),
        (
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "🔑 Your Ollama key appears invalid. Re-set it with /setkey in a private chat.")\n',
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "🔑 Your Ollama key appears invalid. Re-set it with /setkey in a private chat.",\n                              model=model)\n',
        ),
        (
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "⚠️ Something went wrong talking to Ollama. Please try again in a moment.")\n',
            'await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,\n                              "⚠️ Something went wrong talking to Ollama. Please try again in a moment.",\n                              model=model)\n',
        ),
        (
            'await _finalize_error(bot, chat.id, status_id, ctx, exc, parse_mode,\n                              "⚠️ Unexpected error. Please try again later.")\n',
            'await _finalize_error(bot, chat.id, status_id, ctx, exc, parse_mode,\n                              "⚠️ Unexpected error. Please try again later.",\n                              model=model)\n',
        ),
    ]
    for old, new in repls:
        if old in s:
            s = s.replace(old, new, 1)
        else:
            print("WARNING: expected call-site block not found")

    io.open(p, "w", encoding="utf-8").write(s)
    print("handlers.py patched")


patch_ollama_client()
patch_handlers()
print("DONE")
