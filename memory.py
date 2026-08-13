"""
Conversation memory manager.

Memory is keyed by chat id, which works for both private chats (chat id == user id
scope) and groups (shared per group). Older messages are automatically summarized
when the estimated token count grows too large, keeping requests within model
context limits.
"""
from config import Config


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Cheap and good enough for trimming."""
    return max(1, len(text or "") // 4)


class MemoryManager:
    def __init__(self, storage, config: Config, ollama, logger):
        self.storage = storage
        self.config = config
        self.ollama = ollama
        self.logger = logger

    async def add_message(self, chat_id: int, role: str, content: str, images=None, name=None) -> list:
        messages = await self.storage.get_memory(chat_id)
        entry = {"role": role, "content": content}
        if images:
            entry["images"] = images
        if role == "user" and name:
            entry["name"] = name
        messages.append(entry)
        if len(messages) > self.config.MAX_MEMORY_MESSAGES:
            # Keep the most recent messages; oldest are summarized beforehand.
            messages = messages[-self.config.MAX_MEMORY_MESSAGES :]
        await self.storage.set_memory(chat_id, messages)
        return messages

    async def get_messages(self, chat_id: int) -> list:
        return await self.storage.get_memory(chat_id)

    async def clear(self, chat_id: int) -> None:
        await self.storage.set_memory(chat_id, [])

    async def maybe_summarize(self, chat_id: int, api_key: str, model: str) -> None:
        """Summarize the oldest part of the conversation when it gets large."""
        messages = await self.storage.get_memory(chat_id)
        total = sum(estimate_tokens(m.get("content", "")) for m in messages)
        if total < self.config.SUMMARY_TRIGGER_TOKENS or len(messages) < 10:
            return

        keep = messages[-10:]
        to_summarize = messages[:-10]
        if not to_summarize:
            return

        convo_parts = []
        for m in to_summarize:
            role = m.get('role', 'user')
            content = m.get('content', '')
            if role == 'user':
                n = m.get('name') or 'User'
                content = f'[{n}]: {content}'
            convo_parts.append(f'{role}: {content}')
        convo = "\n\n".join(convo_parts)
        summary_messages = [
            {
                "role": "system",
                "content": (
                    "Summarize the following conversation concisely. Preserve key facts, "
                    "decisions, names, preferences and unresolved questions. Return only "
                    "the summary, no preamble."
                ),
            },
            {"role": "user", "content": convo},
        ]
        try:
            summary = await self.ollama.chat(api_key, model, summary_messages, stream=False)
        except Exception as exc:
            self.logger.warning("Summarization skipped: %s", type(exc).__name__)
            return
        if not summary:
            return
        new_messages = [
            {"role": "system", "content": f"[Summary of earlier conversation]\n{summary}"}
        ] + keep
        await self.storage.set_memory(chat_id, new_messages)
        self.logger.info("Summarized memory for chat %s", chat_id)
