import asyncio
import subprocess
import os
import shutil
from playwright.async_api import async_playwright

PROFILE_DIR = os.path.abspath(".browser_session_web")

def cleanup():
    """Kill all Chrome and optionally reset the profile."""
    try:
        subprocess.run('taskkill /F /IM chrome.exe /T', shell=True, capture_output=True)
    except Exception:
        pass

async def main():
    print("=" * 50)
    print("  LocalLLM Session Login Helper")
    print("=" * 50)

    url = input("\nEnter the URL to open (leave blank for Google): ").strip()
    if not url:
        url = "https://www.google.com"

    # Check if profile is corrupted and offer reset
    if os.path.exists(PROFILE_DIR):
        print(f"\nExisting profile found at: {PROFILE_DIR}")
        reset = input("Reset browser profile? (recommended if crashes occur) [y/N]: ").strip().lower()
        if reset == 'y':
            print("Killing Chrome processes...")
            cleanup()
            print("Deleting old profile...")
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            print("Profile reset complete. You will need to re-login to all sites.")
    else:
        cleanup()

    print(f"\nLaunching browser -> {url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ]
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded")

        print("\nBrowser is open! Log in, then close the browser window to save.")
        print("Press Ctrl+C in this terminal to force quit.\n")

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
