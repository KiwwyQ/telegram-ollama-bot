"""
Structured logging setup.

The logger never prints API keys or other secrets. Call sites are responsible for
not passing secrets into log messages; this module additionally installs a filter
that redacts any obvious secret-like substrings as a safety net.
"""
import logging
import re
import sys

# Matches long hex/alpha-numeric bearer tokens and typical API key shapes.
_SECRET_RE = re.compile(r"(Bearer\s+[A-Za-z0-9_\-]{8,})|([A-Za-z0-9_\-]{32,})")


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact(str(record.msg))
            if isinstance(record.args, dict):
                record.args = {k: _redact(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact(str(v)) for v in record.args)
        except Exception:
            pass
        return True


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


def setup_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
    )
    handler.setFormatter(formatter)
    handler.addFilter(SecretFilter())

    logger = logging.getLogger("bot")
    logger.handlers = [handler]
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger
