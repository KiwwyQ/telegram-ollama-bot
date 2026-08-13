"""
Configuration loaded from environment variables.

No API keys or secrets are ever hardcoded here. Everything that differs between
deployments (tokens, admin IDs, defaults) comes from the environment / .env file.
"""
import os
from dataclasses import dataclass, field


def _parse_int_set(value: str) -> set[int]:
    """Parse a comma separated list of Telegram user IDs into a set of ints."""
    result: set[int] = set()
    if not value:
        return result
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            # Ignore malformed entries rather than crashing on startup.
            pass
    return result


# Default catalogue of free Ollama Cloud models. This is used as a fallback list
# when the user has not set FREE_MODELS and the live /api/tags call is unavailable.
DEFAULT_FREE_MODELS = [
    "gpt-oss:20b",
    "gpt-oss:120b",
    "llama3.1:8b",
    "llama3.1:70b",
    "llama3.2:3b",
    "llama3.2-vision",
    "qwen2.5:7b",
    "qwen2.5:14b",
    "mistral:7b",
    "llava",
    "deepseek-r1:8b",
    "phi3:mini",
]


@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = ""
    BOT_USERNAME: str = ""  # populated at runtime from get_me()

    # Telegram user IDs (comma separated) that may perform extra admin actions.
    ADMIN_IDS: set[int] = field(default_factory=set)

    # Model configuration.
    DEFAULT_MODEL: str = "gpt-oss:20b"
    DEFAULT_VISION_MODEL: str = "gemma4:31b"
    OLLAMA_BASE_URL: str = "https://ollama.com"
    FREE_MODELS: list = field(default_factory=list)

    # Remote database (REQUIRED). SQLite is intentionally NOT used because the
    # Render filesystem is ephemeral. Provide a full async SQLAlchemy URL, e.g.
    #   MySQL:    mysql+aiomysql://user:pass@host:3306/dbname
    #   Postgres: postgresql+asyncpg://user:pass@host:5432/dbname
    DATABASE_URL: str = ""

    # Klipy GIF API key (optional). Without it the GIF tool is disabled.
    # Get a free production key at https://klipy.com/docs
    KLIPY_API_KEY: str = ""

    # Keep-alive / web server (used only when deploying as a Web Service).
    ENABLE_WEB_SERVER: bool = False
    PORT: int = 8080

    # Memory / context management.
    MAX_MEMORY_MESSAGES: int = 40
    SUMMARY_TRIGGER_TOKENS: int = 6000

    # Abuse protection.
    RATE_LIMIT_PER_USER_SECONDS: int = 3

    # Behaviour.
    STREAM_RESPONSES: bool = True
    LOG_LEVEL: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        env_models = [m.strip() for m in os.environ.get("FREE_MODELS", "").split(",") if m.strip()]
        return cls(
            TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            ADMIN_IDS=_parse_int_set(os.environ.get("ADMIN_IDS", "")),
            DEFAULT_MODEL=os.environ.get("DEFAULT_MODEL", "gpt-oss:20b"),
            DEFAULT_VISION_MODEL=os.environ.get("DEFAULT_VISION_MODEL", "gemma4:31b"),
            OLLAMA_BASE_URL=os.environ.get("OLLAMA_BASE_URL", "https://ollama.com").rstrip("/"),
            FREE_MODELS=env_models or list(DEFAULT_FREE_MODELS),
            DATABASE_URL=os.environ.get("DATABASE_URL", ""),
            KLIPY_API_KEY=os.environ.get("KLIPY_API_KEY", ""),
            ENABLE_WEB_SERVER=os.environ.get("ENABLE_WEB_SERVER", "false").lower() in ("1", "true", "yes"),
            PORT=int(os.environ.get("PORT", "8080")),
            MAX_MEMORY_MESSAGES=int(os.environ.get("MAX_MEMORY_MESSAGES", "40")),
            SUMMARY_TRIGGER_TOKENS=int(os.environ.get("SUMMARY_TRIGGER_TOKENS", "6000")),
            RATE_LIMIT_PER_USER_SECONDS=int(os.environ.get("RATE_LIMIT_PER_USER_SECONDS", "3")),
            STREAM_RESPONSES=os.environ.get("STREAM_RESPONSES", "true").lower() in ("1", "true", "yes"),
            LOG_LEVEL=os.environ.get("LOG_LEVEL", "INFO"),
        )

    def is_vision_model(self, model: str) -> bool:
        """Heuristic to detect whether a model name supports image input."""
        lowered = (model or "").lower()
        markers = ("vision", "llava", "moondream", "minicpm", "qwen-vl", "qwen2-vl", "pixtral", "gemma3", "gemma4")
        return any(m in lowered for m in markers)
