"""
Lightweight agentic workflow for multi-step tool use.

This is NOT a multi-agent framework. It is a simple loop that lets Ollama
perform several tool actions in sequence when a request requires it:

  user request → model generates → execute tools → feed results → model generates → ...

The loop stops when:
- the model produces no tool markers (final answer)
- max steps is reached
- an unrecoverable error occurs
- timeout is exceeded

All existing tools (web search, workspace, eval, skills, files) are reused.
No new infrastructure is introduced.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

logger = logging.getLogger("agent")

GIF_RE = re.compile(r"\[GIF:(.*?)\]", re.IGNORECASE)
FILE_RE = re.compile(r"\[FILE:([^\]]+)\](.*?)\[/FILE\]", re.IGNORECASE | re.DOTALL)
SEARCH_RE = re.compile(r"\[SEARCH:(.*?)\]", re.IGNORECASE)
WS_LIST_RE = re.compile(r"\[WS_LIST\]", re.IGNORECASE)
WS_READ_RE = re.compile(r"\[WS_READ:([^\]]+)\]", re.IGNORECASE)
WS_WRITE_RE = re.compile(r"\[WS_WRITE:([^\]]+)\](.*?)\[/WS_WRITE\]", re.IGNORECASE | re.DOTALL)
WS_DELETE_RE = re.compile(r"\[WS_DELETE:([^\]]+)\]", re.IGNORECASE)
EVAL_RE = re.compile(r"\[EVAL\](.*?)\[/EVAL\]", re.IGNORECASE | re.DOTALL)
SKILL_RE = re.compile(r"\[SKILL:([^\]]+)\]", re.IGNORECASE)
SEND_FILE_RE = re.compile(r"\[SEND_FILE:([^\]]+)\]", re.IGNORECASE)
DONE_RE = re.compile(r"\[DONE\]", re.IGNORECASE)


class AgentError(Exception):
    """Raised when the agent loop cannot continue."""


def _extract_progress_description(reply: str) -> str:
    lines = (reply or "").strip().splitlines()
    for line in lines[:8]:
        text = line.strip()
        if not text:
            continue
        if text.startswith("[") or text.startswith("```"):
            continue
        if len(text) > 60:
            text = text[:57] + "..."
        return text
    return "Working..."


async def run_agent_loop(
    ctx,
    user_id: int,
    chat_id: int,
    bot,
    api_key: str,
    model: str,
    messages: list,
    status_id: Optional[int],
    parse_mode,
    sandbox_allowed: bool = False,
    max_steps: int = 5,
    timeout: float = 120.0,
) -> str:
    """Run a lightweight agentic tool loop.

    Args:
        ctx: BotContext with tools, ollama, memory, workspace.
        user_id: Telegram user ID for workspace/eval ownership.
        chat_id: Telegram chat ID for memory and status updates.
        bot: Telegram bot instance.
        api_key: Ollama API key.
        model: Model name.
        messages: Initial message list (system + history + user).
        status_id: Status message ID for progress updates (or None).
        parse_mode: Telegram parse mode.
        max_steps: Maximum tool-use iterations.
        timeout: Maximum wall-clock seconds for the entire loop.

    Returns:
        Final assistant reply text (tool markers stripped).
    """
    loop_messages = list(messages)
    start = time.perf_counter()
    logger.info("agent loop start | user=%s chat=%s model=%s max_steps=%s timeout=%s", user_id, chat_id, model, max_steps, timeout)

    for step in range(max_steps):
        if time.perf_counter() - start > timeout:
            logger.warning("agent timeout | user=%s step=%s elapsed=%s", user_id, step, time.perf_counter() - start)
            return "I'm sorry, I couldn't finish that task in time. Please try again with a simpler request."

        # Show progress on the status message.
        if status_id:
            try:
                desc = _extract_progress_description(reply) if step > 0 else "Starting..."
                await bot.edit_message_text(
                    text=f"🔄 Step {step + 1}/{max_steps} - {desc}",
                    chat_id=chat_id,
                    message_id=status_id,
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        # Generate.
        try:
            reply = await ctx.ollama.chat(api_key, model, loop_messages, stream=False)
        except Exception as exc:
            logger.error("agent generation failed | user=%s step=%s exc=%s", user_id, step, type(exc).__name__)
            raise AgentError(f"Generation failed: {type(exc).__name__}") from exc

        if not reply:
            continue

        # Parse tool markers.
        has_tools = any(regex.findall(reply) for regex in (
            SEARCH_RE, GIF_RE, FILE_RE, WS_LIST_RE, WS_READ_RE, WS_WRITE_RE,
            WS_DELETE_RE, EVAL_RE, SKILL_RE, SEND_FILE_RE,
        )) and not DONE_RE.search(reply)

        if not has_tools or DONE_RE.search(reply):
            logger.info("agent loop complete | user=%s steps=%s elapsed=%s", user_id, step + 1, time.perf_counter() - start)
            clean = DONE_RE.sub("", reply).strip()
            return clean

        # Execute tools and append results.
        tool_results = []

        # Web search.
        for q in SEARCH_RE.findall(reply)[:3]:
            q = q.strip()
            try:
                res = await ctx.tools.do_web_search(api_key, q)
                tool_results.append(f"[Web search results for '{q}']:\n{res}")
            except Exception as exc:
                tool_results.append(f"(Web search error: {type(exc).__name__})")

        # GIFs.
        for term in GIF_RE.findall(reply)[:3]:
            term = term.strip()
            if not ctx.tools.gif_enabled:
                tool_results.append(f"(GIF error: Klipy key not configured)")
                continue
            try:
                gif_url = await ctx.tools.search_gif(term)
                if gif_url:
                    try:
                        await bot.send_animation(chat_id, animation=gif_url, caption=None)
                    except Exception:
                        pass
                    tool_results.append(f"(Sent GIF: {term})")
                else:
                    tool_results.append(f"(GIF not found: {term})")
            except Exception as exc:
                tool_results.append(f"(GIF error: {type(exc).__name__})")

        # Files.
        for fname, fcontent in FILE_RE.findall(reply)[:3]:
            try:
                doc = ctx.tools.make_document(fname.strip(), fcontent)
                await bot.send_document(chat_id, document=doc)
                tool_results.append(f"(Sent file: {fname.strip()})")
            except Exception as exc:
                tool_results.append(f"(File error: {type(exc).__name__})")

        # Workspace.
        ws_ops = []
        for m in WS_LIST_RE.finditer(reply):
            ws_ops.append(("list", ".", ""))
        for m in WS_READ_RE.finditer(reply):
            ws_ops.append(("read", m.group(1).strip(), ""))
        for m in WS_WRITE_RE.finditer(reply):
            ws_ops.append(("write", m.group(1).strip(), m.group(2)))
        for m in WS_DELETE_RE.finditer(reply):
            ws_ops.append(("delete", m.group(1).strip(), ""))

        if ws_ops and sandbox_allowed:
            for op, relpath, content in ws_ops:
                try:
                    if op == "list":
                        tool_results.append(await ctx.tools.ws_list(user_id, relpath))
                    elif op == "read":
                        tool_results.append(await ctx.tools.ws_read(user_id, relpath))
                    elif op == "write":
                        tool_results.append(await ctx.tools.ws_write(user_id, relpath, content))
                    elif op == "delete":
                        tool_results.append(await ctx.tools.ws_delete(user_id, relpath))
                except Exception as exc:
                    tool_results.append(f"(Workspace error: {type(exc).__name__})")

        # Eval.
        for code in EVAL_RE.findall(reply)[:3]:
            if not sandbox_allowed:
                tool_results.append("(Eval error: sandbox not allowed)")
                continue
            try:
                from tools import REQUIRE_RE
                install = [pkg.strip() for pkg in REQUIRE_RE.findall(code) if pkg.strip()]
                cleaned = REQUIRE_RE.sub("", code) if install else code
                result = await ctx.tools.eval_tool.execute(user_id, cleaned, install=install)
                lines = [f"Eval result (exit_code={result['exit_code']}, time={result['execution_time']}s):"]
                if result["stdout"]:
                    lines.append(result["stdout"].rstrip())
                if result["stderr"]:
                    lines.append("STDERR:\n" + result["stderr"].rstrip())
                if result["files_created"]:
                    lines.append("Files created: " + ", ".join(result["files_created"]))
                if not result["success"]:
                    lines.append("(Execution failed)")
                tool_results.append("\n".join(lines))
            except Exception as exc:
                tool_results.append(f"(Eval error: {type(exc).__name__}: {exc}. Try checking the Python environment or simplifying the code.)")

        # Skills.
        for skill_name in SKILL_RE.findall(reply)[:3]:
            skill_name = skill_name.strip()
            if not skill_name:
                continue
            try:
                content = await ctx.tools.read_skill(skill_name)
                if content.startswith("(Skill error:") or content.startswith("(Skills are not configured.)"):
                    tool_results.append(content)
                else:
                    tool_results.append(f"(Loaded skill: {skill_name})")
                    raw = ctx.tools.skill_manager.read_skill(skill_name)
                    loop_messages.append({"role": "system", "content": f"[SKILL: {skill_name}]\n{raw}\n[/END SKILL]"})
            except Exception as exc:
                tool_results.append(f"(Skill error: {type(exc).__name__})")

        # Send file.
        for relpath in SEND_FILE_RE.findall(reply)[:3]:
            relpath = relpath.strip()
            if not relpath:
                continue
            try:
                file_input = await ctx.tools.send_file(user_id, relpath)
                await bot.send_document(chat_id, document=file_input, caption=f"📄 {relpath}")
                tool_results.append(f"(Sent file: {relpath})")
            except Exception as exc:
                tool_results.append(f"(Send file error: {type(exc).__name__})")

        if not tool_results:
            return (ctx.tools.strip_markers(reply) if hasattr(ctx, "tools") else reply.strip())

        # Feed results back and continue.
        loop_messages.append({"role": "assistant", "content": reply})
        loop_messages.append({"role": "user", "content": "\n\n".join(tool_results)})

    logger.warning("agent max steps reached | user=%s max_steps=%s elapsed=%s", user_id, max_steps, time.perf_counter() - start)
    return "I'm sorry, I couldn't complete this task. It may be too complex or I'm restricted from accessing the necessary tools. Please try a simpler request."
