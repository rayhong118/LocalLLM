import asyncio
import subprocess
import os
import shutil
import glob
from playwright.async_api import async_playwright

PROFILE_DIR = os.path.abspath(".browser_session_web")

def cleanup():
    """Kill Chrome processes and purge crash-inducing caches from the profile."""
    # Kill all chrome.exe
    try:
        subprocess.run('taskkill /F /IM chrome.exe /T', shell=True, capture_output=True)
    except Exception:
        pass

    if not os.path.exists(PROFILE_DIR):
        return

    # Delete GPU/shader caches — headless creates caches that crash headed mode
    cache_dirs = [
        "GrShaderCache", "ShaderCache", "GPUCache",
        os.path.join("Default", "GPUCache"),
        os.path.join("Default", "Cache"),
        os.path.join("Default", "Code Cache"),
        "CrashpadMetrics-active.pma",
    ]
    for d in cache_dirs:
        target = os.path.join(PROFILE_DIR, d)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            print(f"  Purged cache: {d}")
        elif os.path.isfile(target):
            try:
                os.remove(target)
                print(f"  Purged file: {d}")
            except Exception:
                pass

    # Delete all lock files
    for f in glob.glob(os.path.join(PROFILE_DIR, "**", "LOCK"), recursive=True):
        try:
            os.remove(f)
        except Exception:
            pass
    for name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        p = os.path.join(PROFILE_DIR, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

async def main():
    print("=" * 50)
    print("  LocalLLM Session Login Helper")
    print("=" * 50)

    url = input("\nEnter the URL to open (leave blank for Google): ").strip()
    if not url:
        url = "https://www.google.com"

    if os.path.exists(PROFILE_DIR):
        reset = input(f"\nReset browser profile entirely? [y/N]: ").strip().lower()
        if reset == 'y':
            print("Nuking profile...")
            subprocess.run('taskkill /F /IM chrome.exe /T', shell=True, capture_output=True)
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            print("Profile deleted. Starting fresh.")
        else:
            print("Cleaning up caches and locks...")
            cleanup()
    else:
        cleanup()

    print(f"\nLaunching browser -> {url}")

    async with async_playwright() as p:
        # Try system Chrome first (more stable on Windows), fall back to bundled Chromium
        for attempt, channel in enumerate(["chrome", None]):
            try:
                label = "System Chrome" if channel else "Bundled Chromium"
                print(f"  Attempt {attempt+1}: Using {label}...")
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    headless=False,
                    no_viewport=True,
                    channel=channel,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                    timeout=15000,
                )
                break  # Success!
            except Exception as e:
                print(f"  {label} failed: {e}")
                if attempt == 0:
                    print("  Falling back...")
                    continue
                else:
                    print("\nBoth Chrome and Chromium failed to launch.")
                    print("Try running again with 'y' to reset the profile.")
                    return

        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(url)

        print(f"\nBrowser is open! Log in, then close the browser window to save.")
        print("Press Ctrl+C to force quit.\n")

        try:
            while len(browser.pages) > 0:
                await asyncio.sleep(1)
        except Exception:
            pass

        print("Session saved successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
