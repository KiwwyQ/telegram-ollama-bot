import io
import re

PATH = "handlers.py"
with io.open(PATH, "r", encoding="utf-8") as f:
    t = f.read()

# --- R1: add Telegram hard-limit constants ---
anchor = "# Callback data prefixes.\n"
assert anchor in t, "callback anchor missing"
t = t.replace(
    anchor,
    anchor + "\n# Telegram hard limits.\nMAX_MESSAGE_LEN = 4096\nMAX_CAPTION_LEN = 1024\n",
    1,
)

# --- R2: replace safe_send / safe_edit with splitting + RetryAfter handling ---
start = t.index("async def safe_send(")
end = t.index("def _parse_mode")
NEW_A = '''def split_text(text, limit=MAX_MESSAGE_LEN):
    """Split text into chunks <= limit, preferring line/word boundaries.

    Avoids cutting mid-word when possible; returns [text] if it already fits.
    Used so replies never exceed Telegram's 4096-char message limit.
    """
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    out = []
    for para in text.split("\\n"):
        if len(para) <= limit:
            if out and len(out[-1]) + len(para) + 1 <= limit:
                out[-1] += "\\n" + para
            else:
                out.append(para)
            continue
        cur = ""
        for w in para.split(" "):
            if len(w) > limit:
                if cur:
                    out.append(cur)
                    cur = ""
                out.extend(w[i:i + limit] for i in range(0, len(w), limit))
                continue
            sep = " " if cur else ""
            if len(cur) + len(sep) + len(w) > limit:
                out.append(cur)
                cur = w
            else:
                cur = cur + sep + w
        if cur:
            out.append(cur)
    res = []
    for c in out:
        if len(c) <= limit:
            res.append(c)
        else:
            res.extend(c[i:i + limit] for i in range(0, len(c), limit))
    return res


async def safe_send(bot, chat_id: int, text: str, ctx: BotContext, parse_mode=None, **kw):
    text = text or "\u2026"
    # Captions (photos/documents/GIFs) are limited to 1024 characters.
    if kw.get("caption") and len(kw["caption"]) > MAX_CAPTION_LEN:
        kw["caption"] = kw["caption"][:MAX_CAPTION_LEN]
    if len(text) > MAX_MESSAGE_LEN:
        # Too long: split into consecutive messages (plain text avoids broken
        # markdown entities spanning chunks). Returns the last message sent.
        last = None
        for part in split_text(text, MAX_MESSAGE_LEN):
            last = await _send_message(bot, chat_id, part, None, ctx, kw)
        return last
    return await _send_message(bot, chat_id, text, parse_mode, ctx, kw)


async def _send_message(bot, chat_id, text, parse_mode, ctx, kw):
    for _ in range(3):
        try:
            return await bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode,
                disable_web_page_preview=True, **kw,
            )
        except RetryAfter as e:
            ctx.logger.warning("send rate-limited, sleeping %ss", e.retry_after)
            await asyncio.sleep(max(1, int(e.retry_after)))
        except TelegramError:
            if parse_mode:
                parse_mode = None
                continue
            return None
    return None


async def safe_edit(bot, chat_id: int, message_id: int, text: str, ctx: BotContext, parse_mode=None):
    text = text or "\u2026"
    # Edits share the 4096 limit; if exceeded, fall back to sending the (split)
    # text as new messages instead of editing.
    if len(text) > MAX_MESSAGE_LEN:
        return await safe_send(bot, chat_id, text, ctx, parse_mode=None)
    for _ in range(3):
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id,
                parse_mode=parse_mode, disable_web_page_preview=True,
            )
            return True
        except RetryAfter as e:
            ctx.logger.warning("edit rate-limited, sleeping %ss", e.retry_after)
            await asyncio.sleep(max(1, int(e.retry_after)))
        except TelegramError:
            if parse_mode:
                parse_mode = None
                continue
            return False
    return False


async def _deliver_final(bot, chat_id, status_id, text, ctx, parse_mode):
    """Send the final answer: one clean edit, or split messages if too long."""
    chunks = split_text(text or "", MAX_MESSAGE_LEN)
    if len(chunks) == 1:
        if status_id:
            await safe_edit(bot, chat_id, status_id, chunks[0], ctx, parse_mode)
        else:
            await safe_send(bot, chat_id, chunks[0], ctx, parse_mode)
        return
    sent = False
    if status_id:
        sent = await safe_edit(bot, chat_id, status_id, chunks[0], ctx, parse_mode=None)
    if not sent:
        await safe_send(bot, chat_id, chunks[0], ctx, parse_mode=None)
    for part in chunks[1:]:
        await safe_send(bot, chat_id, part, ctx, parse_mode=None)


'''
assert t[start:end].strip().startswith("async def safe_send"), "R2 region wrong"
t = t[:start] + NEW_A + t[end:]

# --- R3: remove in-loop display edit for non-stream path (regex, ignore special chars) ---
pat = re.compile(
    r'                reply_text = await ctx\.ollama\.chat\(api_key, model, full, stream=False\).*?'
    r'await safe_edit\(bot, chat\.id, status_id, display, ctx, parse_mode\)',
    re.S,
)
assert pat.search(t), "non-stream loop block missing"
t = pat.sub(
    '                reply_text = await ctx.ollama.chat(api_key, model, full, stream=False)',
    t, 1,
)

# --- R4: pass is_group into streaming edits ---
old_call = "reply_text = await _stream_and_edit(bot, chat.id, status_id, ctx, api_key, model, full, parse_mode)"
assert old_call in t, "stream call missing"
t = t.replace(old_call, old_call + ", is_group=not is_private", 1)

# --- R5: rewrite _stream_and_edit (sparse, throttled edits) ---
sa = t.index("async def _stream_and_edit(")
sb = t.index("async def _finalize_error(")
NEW_STREAM = '''async def _stream_and_edit(bot, chat_id, status_id, ctx, api_key, model, full, parse_mode, is_group):
    """Stream the reply and do *sparse* progressive edits of the status message.

    Edits are throttled (every ~2s in private, ~3s in groups, and only when a
    meaningful chunk of new text has arrived) to avoid Telegram 429 limits.
    Internal tool markers are stripped so the user never sees `[SEARCH: ...]`.
    """
    collected = []
    last_edit = 0.0
    last_len = 0
    interval = 3.0 if is_group else 2.0
    min_delta = 200
    async for delta in ctx.ollama.stream_chat(api_key, model, full):
        collected.append(delta)
        now = time.time()
        snippet = ctx.tools.strip_markers("".join(collected)) or "\u2026 searching\u2026"
        if now - last_edit >= interval and len(snippet) - last_len >= min_delta:
            await safe_edit(bot, chat_id, status_id, snippet + " \u270d\ufe0f", ctx, parse_mode)
            last_edit = now
            last_len = len(snippet)
    return "".join(collected)


'''
t = t[:sa] + NEW_STREAM + t[sb:]

# --- R6: rewrite final delivery tail (GIF/FILE -> _deliver_final) ---
sc = t.index("# ---- tools: GIF + FILE (post-process) ----")
sd = t.index("def message_photo(update):")
NEW_TAIL = '''    # ---- tools: GIF + FILE (post-process) ----
    gifs = ctx.tools.GIF_RE.findall(reply_text)
    files = ctx.tools.FILE_RE.findall(reply_text)
    final_text = ctx.tools.strip_markers(reply_text)

    if ctx.tools.gif_enabled:
        for term in gifs[:3]:
            term = term.strip()
            gif_url = await ctx.tools.search_gif(term)
            if gif_url:
                try:
                    await bot.send_animation(chat.id, animation=gif_url, caption=None)
                except Exception as exc:
                    ctx.logger.debug("send_animation failed: %s", type(exc).__name__)
            else:
                await safe_send(bot, chat.id, f"(Couldn't find a GIF for '{term}' right now.)", ctx)
    else:
        if gifs:
            ctx.logger.debug("GIF requested but Klipy key not configured; skipping.")

    for fname, fcontent in files[:3]:
        try:
            doc = ctx.tools.make_document(fname.strip(), fcontent)
            await bot.send_document(chat.id, document=doc)
        except Exception as exc:
            ctx.logger.debug("send_document failed: %s", type(exc).__name__)

    if not final_text and (gifs or files):
        final_text = "\u2705 Here you go!"

    # Deliver the final answer (handles the 4096-char limit by splitting).
    await _deliver_final(bot, chat.id, status_id, final_text, ctx, parse_mode)

    # ---- memory + usage ----
    await ctx.memory.add_message(chat.id, "user", ctx.tools.strip_markers(content))
    await ctx.memory.add_message(chat.id, "assistant", final_text)

    try:
        count = await ctx.storage.bump_usage(user.id)
        if count == 40 or count == 80:
            await safe_send(
                bot, chat.id,
                "\u2139\ufe0f Friendly reminder: you're using Ollama's free tier. If you hit a limit, "
                "the bot will tell you and it resets daily.", ctx,
            )
    except Exception:
        pass

    # Summarize if needed (uses the triggering user's key).
    try:
        await ctx.memory.maybe_summarize(chat.id, api_key, model)
    except Exception as exc:
        ctx.logger.debug("summarize skipped: %s", type(exc).__name__)


'''
t = t[:sc] + NEW_TAIL + t[sd:]

with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(t)
print("PATCH APPLIED OK")
