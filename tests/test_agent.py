import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock

from agent import run_agent_loop, AgentError


def _make_ctx(tools):
    ctx = MagicMock()
    ctx.tools = tools
    ctx.ollama = MagicMock()
    ctx.memory = MagicMock()
    ctx.workspace = MagicMock()
    ctx.config = MagicMock()
    return ctx


class AgentLoopTests(unittest.TestCase):
    def test_stops_when_no_tool_markers(self):
        ctx = _make_ctx(MagicMock())
        ctx.ollama.chat = AsyncMock(return_value="Final answer.")
        messages = [{"role": "user", "content": "Hello"}]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent_loop(ctx, 1, 1, MagicMock(), "key", "model", messages, None, None)
        )
        self.assertEqual(result, "Final answer.")
        self.assertEqual(ctx.ollama.chat.call_count, 1)

    def test_respects_max_steps(self):
        ctx = _make_ctx(MagicMock())
        ctx.ollama.chat = AsyncMock(return_value="[SEARCH: test]")
        messages = [{"role": "user", "content": "Search"}]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent_loop(ctx, 1, 1, MagicMock(), "key", "model", messages, None, None, max_steps=2)
        )
        self.assertIn("couldn't finish this task within the allowed steps", result)
        self.assertEqual(ctx.ollama.chat.call_count, 2)

    def test_tool_results_fed_back(self):
        tools = MagicMock()
        tools.extract_search_queries = MagicMock(return_value=["query"])
        tools.do_web_search = AsyncMock(return_value="result1")
        ctx = _make_ctx(tools)
        responses = ["[SEARCH: query]", "Final answer."]
        ctx.ollama.chat = AsyncMock(side_effect=responses)
        messages = [{"role": "user", "content": "Search"}]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent_loop(ctx, 1, 1, MagicMock(), "key", "model", messages, None, None)
        )
        self.assertEqual(result, "Final answer.")
        self.assertEqual(ctx.ollama.chat.call_count, 2)

    def test_web_search_error_continues(self):
        tools = MagicMock()
        tools.extract_search_queries = MagicMock(return_value=["query"])
        tools.do_web_search = AsyncMock(side_effect=Exception("search failed"))
        ctx = _make_ctx(tools)
        responses = ["[SEARCH: query]", "Final answer."]
        ctx.ollama.chat = AsyncMock(side_effect=responses)
        messages = [{"role": "user", "content": "Search"}]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent_loop(ctx, 1, 1, MagicMock(), "key", "model", messages, None, None)
        )
        self.assertEqual(result, "Final answer.")
        self.assertEqual(ctx.ollama.chat.call_count, 2)

    def test_timeout(self):
        ctx = _make_ctx(MagicMock())
        async def slow_chat(*args, **kwargs):
            import asyncio
            await asyncio.sleep(0.1)
            return "[SEARCH: test]"
        ctx.ollama.chat = slow_chat
        messages = [{"role": "user", "content": "Search"}]

        result = asyncio.get_event_loop().run_until_complete(
            run_agent_loop(ctx, 1, 1, MagicMock(), "key", "model", messages, None, None, timeout=0.05)
        )
        self.assertIn("ran out of time before finishing", result)


if __name__ == "__main__":
    unittest.main()
