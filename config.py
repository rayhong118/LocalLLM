# config.py
import sys

# LLM Configuration
LLM_MODEL = "gemma4:26b"
CONTEXT_WINDOW = 16384
LLM_TIMEOUT = 300
TEMPERATURE = 0

# Browser Configuration
HEADLESS = True
BROWSER_WAIT_TIME = 5.0  # Increased for slow grocery sites
MAX_STEPS = 50
MAX_FAILURES = 3

# OS Specifics
IS_WINDOWS = sys.platform == 'win32'