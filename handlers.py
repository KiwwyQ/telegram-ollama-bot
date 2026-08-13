"""
Telegram handlers: commands, trigger rules and the generation orchestration.

Trigger rules (critical):
  * Private chat  -> respond to everything (except commands).
  * Group chat    -> respond ONLY when:
      - the message mentions @BotUsername, or
      - the message is a reply to one of the bot's own previous messages.
    Normal group messages are ignored completely.
  * Replying to a human message that also @mentions the bot -> the replied-to
    text is included as context.
  * Replying to the bot's own message -> conversation continues naturally.

Generation is serialized per chat with an asyncio.Lock (the per-chat queue), so
if several messages arrive while one is generating, they are processed in order.
"""
import asyncio
import base64
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError, RetryAfter
from telegram.constants import ParseMode

from personality import build_system_prompt, DEFAULT_PERSONALITY, LANGUAGES
from ollama_client import AuthError, RateLimitError, OllamaError
from tools import GIF_RE, FILE_RE, SEARCH_RE

# user_id -> last request timestamp (process-local abuse throttle).
_RATE_LIMIT: dict[int, float] = {}

# Callback data prefixes.

# Telegram hard limits.
MAX_MESSAGE_LEN = 4096
MAX_CAPTION_LEN = 1024
_CB_MODEL = "model:"
_CB_LANG = "lang:"


@dataclass
class BotContext:
    config: object
    storage: object
    ollama: object
    memory: object
    tools: object
    logger: object
    config_obj: object = field(default=None, repr=False)


# --------------------------------------------------------------------- helpers
def _mention(text: str, username: str) -> bool:
    if not username:
        return False
    return f"@{username.lower()}" in (text or "").lower()


async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def _fmt_setting(user: Optional[dict]) -> Optional[str]:
    fmt = (user or {}).get("fmt", "none")
    return fmt if fmt in ("html", "markdown", "none") else "none"


def split_text(text, limit=MAX_MESSAGE_LEN):
    """Split text into chunks <= limit, preferring line/word boundaries.

    Avoids cutting mid-word when possible; returns [text] if it already fits.
    Used so replies never exceed Telegram's 4096-char message limit.
    """
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    out = []
    for para in text.split("\n"):
        if len(para) <= limit:
            if out and len(out[-1]) + len(para) + 1 <= limit:
                out[-1] += "\n" + para
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
    text = text or "…"
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
    text = text or "…"
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


def _parse_mode(fmt: str):
    if fmt == "html":
        return ParseMode.HTML
    if fmt == "markdown":
        return ParseMode.MARKDOWN_V2
    return None


# =================================================================== COMMANDS
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    user = update.effective_user
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"👋 Hi {user.first_name}! I'm your Ollama Cloud assistant bot.\n\n"
            "To use me you need a free Ollama Cloud API key:\n"
            "1. Get one at https://ollama.com/settings/keys\n"
            "2. Send it to me here (in a private chat): /setkey YOUR_KEY\n\n"
            "Then just message me, or in groups mention me with @"
            f"{ctx.config.BOT_USERNAME}.\n\n"
            "Use /help to see all commands."
        ),
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    is_private = update.effective_chat.type == "private"
    text = (
        "🤖 *Commands*\n\n"
        "/start - welcome & setup\n"
        "/setkey <key> - set your Ollama Cloud key (DM only)\n"
        "/delkey - remove your stored key\n"
        "/model - list & pick a free model\n"
        "/personality [text] - view or set personality\n"
        "/lang - set reply language\n"
        "/format [none|html|md] - reply formatting\n"
        "/clear or /new - clear this chat's memory\n"
        "/stats - show current model & memory info\n"
        "/help - this message\n\n"
        "*How to talk to me*\n"
        "- In DMs: just message me.\n"
        "- In groups: mention me with @" + ctx.config.BOT_USERNAME + " or reply to my messages.\n\n"
        "*Tools I can use*\n"
        "- Web search (I decide when I need fresh info)\n"
        "- GIFs (when appropriate)\n"
        "- Generate text files you can download\n"
        "- Understand images you send me\n\n"
        "*Getting a free key*\n"
        "Visit https://ollama.com/settings/keys , create a key, then send /setkey <key> here."
    )
    if not is_private:
        text += "\n\nℹ️ Your Ollama key is personal and must be set in a private chat with me."
    await safe_send(context.bot, update.effective_chat.id, text, ctx, parse_mode=ParseMode.MARKDOWN)


async def cmd_setkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat = update.effective_chat
    if chat.type != "private":
        await safe_send(
            context.bot, chat.id,
            "🔒 For security, set your key in a *private chat* with me. DM me /setkey <key>.",
            ctx, parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not context.args:
        await safe_send(
            context.bot, chat.id,
            "Usage: /setkey <your_ollama_key>\nGet a free key at https://ollama.com/settings/keys",
            ctx, parse_mode=ParseMode.MARKDOWN,
        )
        return
    key = context.args[0].strip()
    if len(key) < 8:
        await safe_send(context.bot, chat.id, "That key looks too short. Please check it and try again.", ctx)
        return
    await ctx.storage.set_user_fields(update.effective_user.id, ollama_key=key)
    await safe_send(
        context.bot, chat.id,
        "✅ Your Ollama Cloud key is saved (private to your account). You can now talk to me!",
        ctx,
    )


async def cmd_delkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    if update.effective_chat.type != "private":
        await safe_send(context.bot, update.effective_chat.id, "Run /delkey in a private chat with me.", ctx)
        return
    await ctx.storage.delete_key(update.effective_user.id)
    await safe_send(context.bot, update.effective_chat.id, "🗑️ Your Ollama key has been removed.", ctx)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"

    # List available free cloud models dynamically via /api/tags using the
    # triggering user's key; fall back to the static catalogue if unavailable.
    models: list = list(ctx.config.FREE_MODELS)
    user_rec = await ctx.storage.get_user(user.id) or {}
    api_key = user_rec.get("ollama_key")
    if api_key:
        try:
            live = await ctx.ollama.list_models(api_key)
            if live:
                # Prefer live models but keep the curated order for familiar ones.
                models = live
        except Exception as exc:
            ctx.logger.debug("dynamic model list failed: %s", type(exc).__name__)

    if not models:
        await safe_send(
            context.bot, chat.id,
            "No models available right now. Set your key with /setkey and try again, "
            "or check https://ollama.com/settings/keys.", ctx,
        )
        return

    buttons = [[InlineKeyboardButton(m, callback_data=f"{_CB_MODEL}{m}")] for m in models]
    markup = InlineKeyboardMarkup(buttons)
    scope = "this group" if not is_private else "you"
    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"Pick a free model for {scope}. In groups only admins can change the group model.\n"
            "Your personal Ollama key is always used for generation."
        ),
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def cmd_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"

    if is_private:
        if not context.args:
            current = (await ctx.storage.get_user(user.id) or {}).get("personality") or DEFAULT_PERSONALITY
            await safe_send(
                context.bot, chat.id,
                f"🧠 Your current personality:\n\n{current}\n\n"
                "To change it: /personality <text>",
                ctx,
            )
            return
        text = " ".join(context.args)
        await ctx.storage.set_user_fields(user.id, personality=text)
        await safe_send(context.bot, chat.id, "✅ Personality updated for your private chats.", ctx)
        return

    # Group: only admins may set the group personality.
    if not await is_admin(context.bot, chat.id, user.id):
        await safe_send(context.bot, chat.id, "🔒 Only group admins can set the group personality.", ctx)
        return
    if not context.args:
        current = (await ctx.storage.get_group(chat.id) or {}).get("personality")
        await safe_send(
            context.bot, chat.id,
            f"🧠 Current group personality:\n\n{current or DEFAULT_PERSONALITY}\n\n"
            "To change it (admin only): /personality <text>",
            ctx,
        )
        return
    text = " ".join(context.args)
    await ctx.storage.set_group_fields(chat.id, personality=text)
    await safe_send(context.bot, chat.id, "✅ Group personality updated.", ctx)


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat = update.effective_chat
    user = update.effective_user
    if context.args:
        code = context.args[0].strip().lower()
        if code not in LANGUAGES:
            await safe_send(
                context.bot, chat.id,
                "Unknown language code. Use /lang to see the list.", ctx,
            )
            return
        await ctx.storage.set_user_fields(user.id, language=code)
        await safe_send(context.bot, chat.id, f"🌐 Language set to {LANGUAGES[code]}.", ctx)
        return
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"{_CB_LANG}{code}")]
        for code, name in LANGUAGES.items()
    ]
    await context.bot.send_message(
        chat_id=chat.id, text="Choose your preferred language:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    if context.args:
        fmt = context.args[0].strip().lower()
        if fmt not in ("none", "html", "md"):
            await safe_send(context.bot, update.effective_chat.id, "Use: /format none | html | md", ctx)
            return
        await ctx.storage.set_user_fields(update.effective_user.id, fmt=fmt)
        await safe_send(context.bot, update.effective_chat.id, f"🎨 Reply formatting set to: {fmt}", ctx)
        return
    await safe_send(
        context.bot, update.effective_chat.id,
        "Current reply formatting options: none (plain), html, md (MarkdownV2).\n"
        "Set with /format <option>.", ctx,
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat_id = update.effective_chat.id
    await ctx.memory.clear(chat_id)
    await safe_send(context.bot, chat_id, "🧹 Memory cleared for this chat.", ctx)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"
    user_rec = await ctx.storage.get_user(user.id) or {}
    key_set = bool(user_rec.get("ollama_key"))
    model = user_rec.get("model") or ctx.config.DEFAULT_MODEL
    if not is_private:
        grp = await ctx.storage.get_group(chat.id) or {}
        model = grp.get("model") or ctx.config.DEFAULT_MODEL
    mem = await ctx.memory.get_messages(chat.id)
    est = sum(len(m.get("content", "")) // 4 for m in mem)
    text = (
        f"📊 *Stats*\n"
        f"Model: `{model}`\n"
        f"Ollama key set: {'yes' if key_set else 'NO'}\n"
        f"Language: {user_rec.get('language', 'en')}\n"
        f"Messages in memory: {len(mem)}\n"
        f"Approx. context tokens: ~{est}\n"
        f"Summarize threshold: {ctx.config.SUMMARY_TRIGGER_TOKENS}"
    )
    await safe_send(context.bot, chat.id, text, ctx, parse_mode=ParseMode.MARKDOWN)


# ============================================================= CALLBACK QUERIES
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"

    if data.startswith(_CB_MODEL):
        model = data[len(_CB_MODEL):]
        if is_private:
            await ctx.storage.set_user_fields(user.id, model=model)
            target = "you"
        else:
            if not await is_admin(context.bot, chat.id, user.id):
                await query.edit_message_text("🔒 Only group admins can change the group model.")
                return
            await ctx.storage.set_group_fields(chat.id, model=model)
            target = "this group"
        await query.edit_message_text(f"✅ Model set to `{model}` for {target}.", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith(_CB_LANG):
        code = data[len(_CB_LANG):]
        await ctx.storage.set_user_fields(user.id, language=code)
        await query.edit_message_text(f"🌐 Language set to {LANGUAGES.get(code, code)}.")


# ============================================================ MESSAGE HANDLING
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx: BotContext = context.bot_data["ctx"]
    message = update.effective_message
    if not message:
        return
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"
    bot_username = ctx.config.BOT_USERNAME

    text = (message.text or message.caption or "").strip()
    has_photo = bool(message.photo)

    # Ignore pure commands here (CommandHandler handles them).
    if text.startswith("/"):
        return

    # ---- trigger detection ----
    triggered = False
    replied_to_bot = False
    replied_human_text = None

    rtm = message.reply_to_message
    if rtm:
        ruser = rtm.from_user
        if ruser and ruser.is_bot and (ruser.username == bot_username):
            triggered = True
            replied_to_bot = True
        elif _mention(text, bot_username):
            triggered = True
            replied_human_text = (rtm.text or rtm.caption or "")

    if not triggered and is_private:
        triggered = True
    if not triggered and _mention(text, bot_username):
        triggered = True

    if not triggered:
        return

    # ---- key check (groups: message author's own key) ----
    user_rec = await ctx.storage.get_user(user.id) or {}
    api_key = user_rec.get("ollama_key")
    if not api_key:
        if is_private:
            await safe_send(
                context.bot, chat.id,
                "🔑 You haven't set your Ollama Cloud key yet.\n"
                "1. Get a free key: https://ollama.com/settings/keys\n"
                "2. Send me: /setkey YOUR_KEY (only in this private chat)",
                ctx,
            )
        else:
            await safe_send(
                context.bot, chat.id,
                f"🔑 {user.first_name or 'You'} need to set your own Ollama key first.\n"
                "DM me /setkey YOUR_KEY (get a free key at https://ollama.com/settings/keys).",
                ctx,
            )
        return

    # ---- per-user rate limit ----
    now = time.time()
    last = _RATE_LIMIT.get(user.id, 0.0)
    if now - last < ctx.config.RATE_LIMIT_PER_USER_SECONDS:
        await safe_send(
            context.bot, chat.id,
            "⏳ Please wait a moment between messages to avoid hitting free limits.", ctx,
        )
        return
    _RATE_LIMIT[user.id] = now

    # Serialize generation per chat (the per-chat queue).
    lock = context.chat_data.setdefault("gen_lock", asyncio.Lock())
    async with lock:
        await _generate(update, context, ctx, text, has_photo, replied_human_text, api_key, user_rec)


async def _generate(update, context, ctx, text, has_photo, replied_human_text, api_key, user_rec):
    chat = update.effective_chat
    user = update.effective_user
    is_private = chat.type == "private"
    bot = context.bot

    # Status message (hourglass).
    status = await safe_send(bot, chat.id, "⏳", ctx)
    status_id = status.message_id if status else None

    # Determine model + personality.
    if is_private:
        model = user_rec.get("model") or ctx.config.DEFAULT_MODEL
        personality = user_rec.get("personality") or DEFAULT_PERSONALITY
        language = (await ctx.storage.get_user(user.id) or {}).get("language", "en")
    else:
        grp = await ctx.storage.get_group(chat.id) or {}
        model = grp.get("model") or ctx.config.DEFAULT_MODEL
        personality = grp.get("personality") or DEFAULT_PERSONALITY
        language = user_rec.get("language", "en")

    fmt = _fmt_setting(user_rec)
    parse_mode = _parse_mode(fmt)

    # Vision routing.
    images_b64 = None
    if has_photo:
        try:
            photo = message_photo(update)
            f = await bot.get_file(photo.file_id)
            data = await f.download_as_bytearray()
            images_b64 = [base64.b64encode(bytes(data)).decode("utf-8")]
            if not ctx.config.is_vision_model(model):
                model = ctx.config.DEFAULT_VISION_MODEL
        except Exception as exc:
            ctx.logger.warning("image download failed: %s", type(exc).__name__)
            images_b64 = None

    # Build the new user message content (with replied context).
    content = text
    if replied_human_text:
        content = f"(Replying to: {replied_human_text})\n\n{content}"
    if has_photo and not content:
        content = "What is in this image?"

    new_user_msg = {"role": "user", "content": content}
    if images_b64:
        new_user_msg["images"] = images_b64

    # Participants line for group awareness.
    participants = None
    if not is_private:
        participants = f"{user.first_name} (@{user.username or user.id})"

    system_prompt = build_system_prompt(personality, language, participants)

    # Load memory (history).
    history = await ctx.memory.get_messages(chat.id)
    # Build full message list (exclude prior system summaries' duplication is fine).
    full = [{"role": "system", "content": system_prompt}] + list(history) + [new_user_msg]

    # ---- generation with tool loop ----
    reply_text = ""
    try:
        for attempt in range(4):
            if ctx.config.STREAM_RESPONSES:
                reply_text = await _stream_and_edit(bot, chat.id, status_id, ctx, api_key, model, full, parse_mode, is_group=not is_private)
            else:
                reply_text = await ctx.ollama.chat(api_key, model, full, stream=False)
            # Tool: web search.
            searches = ctx.tools.extract_search_queries(reply_text)
            if searches and attempt < 3:
                for q in searches[:3]:
                    try:
                        ctx.logger.info("web search: %s", q)
                        res = await ctx.tools.do_web_search(api_key, q)
                        full.append({"role": "user", "content": f"[Web search results for '{q}']:\n{res}"})
                    except (RateLimitError, AuthError, OllamaError) as e:
                        # Surface limit errors immediately and stop.
                        await _finalize_error(bot, chat.id, status_id, ctx, e, parse_mode)
                        return
                continue  # re-ask model with search results
            break
    except RateLimitError:
        await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,
                              "🚫 Your free Ollama daily limit has been reached. "
                              "It usually resets ~24h after your first request today (or at midnight UTC).")
        return
    except AuthError:
        await _finalize_error(bot, chat.id, status_id, ctx, None, parse_mode,
                              "🔑 Your Ollama key appears invalid. Re-set it with /setkey in a private chat.")
        return
    except OllamaError as e:
        await _finalize_error(bot, chat.id, status_id, ctx, e, parse_mode,
                              "⚠️ Something went wrong talking to Ollama. Please try again in a moment.")
        return
    except Exception as exc:
        ctx.logger.exception("generation error")
        await _finalize_error(bot, chat.id, status_id, ctx, exc, parse_mode,
                              "⚠️ Unexpected error. Please try again later.")
        return

        # ---- tools: GIF + FILE (post-process) ----
    gifs = GIF_RE.findall(reply_text)
    files = FILE_RE.findall(reply_text)
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
        final_text = "✅ Here you go!"

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
                "ℹ️ Friendly reminder: you're using Ollama's free tier. If you hit a limit, "
                "the bot will tell you and it resets daily.", ctx,
            )
    except Exception:
        pass

    # Summarize if needed (uses the triggering user's key).
    try:
        await ctx.memory.maybe_summarize(chat.id, api_key, model)
    except Exception as exc:
        ctx.logger.debug("summarize skipped: %s", type(exc).__name__)


def message_photo(update):
    return update.effective_message.photo[-1]


async def _stream_and_edit(bot, chat_id, status_id, ctx, api_key, model, full, parse_mode, is_group):
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
        snippet = ctx.tools.strip_markers("".join(collected)) or "… searching…"
        if now - last_edit >= interval and len(snippet) - last_len >= min_delta:
            await safe_edit(bot, chat_id, status_id, snippet + " ✍️", ctx, parse_mode)
            last_edit = now
            last_len = len(snippet)
    return "".join(collected)


async def _finalize_error(bot, chat_id, status_id, ctx, exc, parse_mode, message):
    if exc is not None:
        ctx.logger.warning("finalize error: %s", type(exc).__name__)
    if status_id:
        try:
            await bot.edit_message_text(text=message, chat_id=chat_id, message_id=status_id,
                                        disable_web_page_preview=True)
        except Exception:
            pass
    else:
        await safe_send(bot, chat_id, message, ctx)


# ================================================================ REGISTRATION
def register_handlers(app, ctx: BotContext):
    app.bot_data["ctx"] = ctx
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setkey", cmd_setkey))
    app.add_handler(CommandHandler("delkey", cmd_delkey))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("personality", cmd_personality))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("format", cmd_format))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("new", cmd_clear))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message)
    )
