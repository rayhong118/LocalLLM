# browser_utils.py
import subprocess
import config

def cleanup_headless_chrome():
    """Safety cleanup for Windows to prevent CDP zombie processes."""
    if config.IS_WINDOWS:
        try:
            # Taskkill is more reliable than wmic on modern Windows
            # Filtering for headless is hard via taskkill, but we can try to kill orphaned ones
            subprocess.run('taskkill /F /IM chrome.exe /FI "STATUS eq RUNNING" /FI "WINDOWTITLE eq "" "', shell=True, capture_output=True)
        except Exception as e:
            print(f"Warning: Headless cleanup failed: {e}")
