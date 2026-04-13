# browser_utils.py
import subprocess
import config

def cleanup_headless_chrome():
    """Safety cleanup for Windows to prevent CDP zombie processes."""
    if config.IS_WINDOWS:
        try:
            kill_cmd = 'wmic process where "name=\'chrome.exe\' and commandline like \'%--headless%\'" call terminate'
            subprocess.run(kill_cmd, shell=True, capture_output=True)
        except Exception as e:
            print(f"Warning: Headless cleanup failed: {e}")
