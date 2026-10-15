# config.py
import sys
import os
from pathlib import Path

# Load environment variables early
def _load_dotenv():
    """Minimal .env loader — checks backend/ and project root for .env."""
    candidates = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

_load_dotenv()

# LLM Configuration (Configured for LM Studio / OpenAI-compatible local server)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "lm_studio")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1"))
LLM_API_KEY = os.environ.get("LLM_API_KEY", "lm-studio")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5:9b")
CONTEXT_WINDOW = int(os.environ.get("CONTEXT_WINDOW", "32768"))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "300"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0"))

# Browser Configuration
HEADLESS = False  # Headed mode required: Safeway/Akamai detects and blocks headless Chrome, causing empty pages
BROWSER_WAIT_TIME = 5.0  # Increased for slow grocery sites
MAX_STEPS = 50
MAX_FAILURES = 3

# OS Specifics
IS_WINDOWS = sys.platform == 'win32'

# Telegram Configuration — credentials loaded from .env (not tracked by git)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_NOTIFY_ALL = False  # Set to True to notify for ALL tasks, False for scheduled tasks only

