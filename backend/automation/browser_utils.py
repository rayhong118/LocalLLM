# browser_utils.py
import subprocess
import logging
from backend import config

logger = logging.getLogger(__name__)

def cleanup_browser_processes():
    """Safety cleanup to close any Chrome browser windows opened for LocalLLM tasks."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                if 'chrome' in name and ('.browser_session_web' in cmdline or '--headless' in cmdline):
                    proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        logger.debug(f"psutil browser cleanup error: {e}")
        if config.IS_WINDOWS:
            try:
                cmd = 'Get-CimInstance Win32_Process -Filter "Name = \'chrome.exe\'" | Where-Object { $_.CommandLine -like "*browser_session_web*" -or $_.CommandLine -like "*--headless*" } | Invoke-CimMethod -MethodName Terminate'
                subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=5)
            except Exception as pe:
                logger.debug(f"PowerShell browser cleanup error: {pe}")

# Backwards compatibility alias
cleanup_headless_chrome = cleanup_browser_processes

