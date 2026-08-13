import asyncio
import unittest
from unittest.mock import MagicMock

from personality import build_system_prompt, DEFAULT_PERSONALITY, TOOL_INSTRUCTION, LANGUAGES


# ---- helpers under test (replicated here to avoid importing telegram-dependent handlers) ----

def _display_name(user) -> str:
    if getattr(user, "username", None):
        return user.username
    parts = [getattr(user, "first_name", ""), getattr(user, "last_name", "")]
    name = " ".join(p for p in parts if p).strip()
    return name or str(getattr(user, "id", "User"))


def _prefix_user_message(msg: dict) -> dict:
    if msg.get("role") != "user":
        return msg
    name = msg.get("name")
    if not name:
        return msg
    content = msg.get("content", "")
    out = {"role": "user", "content": f"[{name}]: {content}"}
    if "images" in msg:
        out["images"] = msg["images"]
    return out


class DisplayNameTests(unittest.TestCase):
    def test_username_used(self):
        u = MagicMock(username="fox", first_name="Fox", last_name="Yz", id=1)
        self.assertEqual(_display_name(u), "fox")

    def test_first_last_no_username(self):
        u = MagicMock(username=None, first_name="Fox", last_name="Yz", id=1)
        self.assertEqual(_display_name(u), "Fox Yz")

    def test_first_only_no_username(self):
        u = MagicMock(username=None, first_name="Fox", last_name="", id=1)
        self.assertEqual(_display_name(u), "Fox")

    def test_unicode_name(self):
        u = MagicMock(username=None, first_name="Фокси", last_name="Икс", id=1)
        self.assertEqual(_display_name(u), "Фокси Икс")

    def test_missing_names_falls_back_to_id(self):
        u = MagicMock(username=None, first_name="", last_name="", id=42)
        self.assertEqual(_display_name(u), "42")

    def test_dm_current_user(self):
        u = MagicMock(username="fox", first_name="Fox", last_name="Yz", id=1)
        self.assertEqual(_display_name(u), "fox")


class PrefixUserMessageTests(unittest.TestCase):
    def test_user_message_prefixed(self):
        msg = {"role": "user", "content": "hello", "name": "Fox"}
        out = _prefix_user_message(msg)
        self.assertEqual(out, {"role": "user", "content": "[Fox]: hello"})

    def test_assistant_message_unchanged(self):
        msg = {"role": "assistant", "content": "hi"}
        out = _prefix_user_message(msg)
        self.assertEqual(out, msg)

    def test_user_message_with_name_field(self):
        msg = {"role": "user", "content": "hi", "name": "Alex"}
        out = _prefix_user_message(msg)
        self.assertEqual(out["content"], "[Alex]: hi")

    def test_user_message_missing_name_unchanged(self):
        msg = {"role": "user", "content": "hi"}
        out = _prefix_user_message(msg)
        self.assertEqual(out, msg)

    def test_user_message_with_images_preserved(self):
        msg = {"role": "user", "content": "look", "images": ["abc"], "name": "Fox"}
        out = _prefix_user_message(msg)
        self.assertEqual(out["content"], "[Fox]: look")
        self.assertEqual(out["images"], ["abc"])

    def test_group_two_users(self):
        msgs = [
            {"role": "user", "content": "hello", "name": "Fox"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "make pdf", "name": "Alex"},
        ]
        out = [_prefix_user_message(m) for m in msgs]
        self.assertEqual(out[0]["content"], "[Fox]: hello")
        self.assertEqual(out[1]["content"], "hi")
        self.assertEqual(out[2]["content"], "[Alex]: make pdf")

    def test_group_three_users(self):
        msgs = [
            {"role": "user", "content": "a", "name": "Fox"},
            {"role": "user", "content": "b", "name": "Alex"},
            {"role": "user", "content": "c", "name": "Jordan"},
        ]
        out = [_prefix_user_message(m) for m in msgs]
        self.assertEqual(out[0]["content"], "[Fox]: a")
        self.assertEqual(out[1]["content"], "[Alex]: b")
        self.assertEqual(out[2]["content"], "[Jordan]: c")

    def test_old_message_without_name_unchanged(self):
        # Backward compatibility: messages without a name field are returned as-is.
        msg = {"role": "user", "content": "old message"}
        out = _prefix_user_message(msg)
        self.assertEqual(out, msg)


class SystemPromptAttributionTests(unittest.TestCase):
    def test_dm_includes_attribution_note(self):
        prompt = build_system_prompt(DEFAULT_PERSONALITY, "en", None)
        self.assertIn("prefixed with the sender's display name", prompt)
        self.assertIn("Do not claim to see profiles directly", prompt)

    def test_group_includes_attribution_note(self):
        prompt = build_system_prompt(DEFAULT_PERSONALITY, "en", "Fox (@fox)")
        self.assertIn("prefixed with the sender's display name", prompt)
        self.assertIn("People in this chat may include: Fox (@fox)", prompt)

    def test_non_english_language_still_has_attribution(self):
        prompt = build_system_prompt(DEFAULT_PERSONALITY, "es", None)
        self.assertIn("prefixed with the sender's display name", prompt)
        self.assertIn("reply primarily in Spanish", prompt)


class MemoryNameStorageTests(unittest.TestCase):
    def test_add_message_stores_name_for_user(self):
        from memory import MemoryManager

        async def get_memory(chat_id):
            return []

        async def set_memory(chat_id, messages):
            pass

        storage = MagicMock()
        storage.get_memory = get_memory
        storage.set_memory = set_memory
        cfg = MagicMock()
        cfg.MAX_MEMORY_MESSAGES = 100
        ollama = MagicMock()
        logger = MagicMock()
        mem = MemoryManager(storage, cfg, ollama, logger)

        msgs = asyncio.get_event_loop().run_until_complete(
            mem.add_message(1, "user", "hello", name="Fox")
        )
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["name"], "Fox")
        self.assertEqual(msgs[0]["content"], "hello")

    def test_add_message_omits_name_for_assistant(self):
        from memory import MemoryManager

        async def get_memory(chat_id):
            return []

        async def set_memory(chat_id, messages):
            pass

        storage = MagicMock()
        storage.get_memory = get_memory
        storage.set_memory = set_memory
        cfg = MagicMock()
        cfg.MAX_MEMORY_MESSAGES = 100
        ollama = MagicMock()
        logger = MagicMock()
        mem = MemoryManager(storage, cfg, ollama, logger)

        msgs = asyncio.get_event_loop().run_until_complete(
            mem.add_message(1, "assistant", "hi", name="Fox")
        )
        self.assertNotIn("name", msgs[0])

    def test_add_message_backward_compat_no_name(self):
        from memory import MemoryManager

        async def get_memory(chat_id):
            return []

        async def set_memory(chat_id, messages):
            pass

        storage = MagicMock()
        storage.get_memory = get_memory
        storage.set_memory = set_memory
        cfg = MagicMock()
        cfg.MAX_MEMORY_MESSAGES = 100
        ollama = MagicMock()
        logger = MagicMock()
        mem = MemoryManager(storage, cfg, ollama, logger)

        msgs = asyncio.get_event_loop().run_until_complete(
            mem.add_message(1, "user", "old msg")
        )
        self.assertEqual(msgs[0]["role"], "user")
        self.assertNotIn("name", msgs[0])


if __name__ == "__main__":
    unittest.main()
