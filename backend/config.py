# config.py
import sys

# LLM Configuration
LLM_MODEL = "qwen3.5:9b"
CONTEXT_WINDOW = 32768
LLM_TIMEOUT = 300
TEMPERATURE = 0

# Browser Configuration
HEADLESS = True
BROWSER_WAIT_TIME = 5.0  # Increased for slow grocery sites
MAX_STEPS = 50
MAX_FAILURES = 3

# OS Specifics
IS_WINDOWS = sys.platform == 'win32'

# Telegram Configuration — credentials loaded from .env (not tracked by git)
import os
from pathlib import Path

def _load_dotenv():
    """Minimal .env loader — no external dependency needed."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

_load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_NOTIFY_ALL = False  # Set to True to notify for ALL tasks, False for scheduled tasks only
