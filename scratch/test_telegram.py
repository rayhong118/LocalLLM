import sys
import os
import asyncio
import httpx
import re

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.notifier import send_telegram_notification

async def auto_discover_chat_id(token: str) -> str:
    """Queries Telegram getUpdates to find the chat ID of whoever recently messaged the bot."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    print(f"Checking for recent messages to your bot at: {url}")
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", [])
                if not results:
                    print("\n⚠️ No messages found. Please open Telegram, search for your bot, and send it a message (like /start) first, then run this script again.")
                    return ""
                
                # Get the last message sender's chat info
                last_result = results[-1]
                message = last_result.get("message") or last_result.get("channel_post")
                if message:
                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    username = chat.get("username", "Unknown")
                    first_name = chat.get("first_name", "User")
                    print(f"\n✨ Found a message from {first_name} (@{username}) with Chat ID: {chat_id}")
                    return str(chat_id)
            else:
                print(f"Telegram API returned status code {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Connection failed: {e}")
    return ""

def update_config_file(chat_id: str):
    """Automatically updates TELEGRAM_CHAT_ID in config.py."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.py")
    if not os.path.exists(config_path):
        print(f"Could not find config.py at {config_path}")
        return
        
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Search for TELEGRAM_CHAT_ID = ...
    pattern = r'TELEGRAM_CHAT_ID\s*=\s*["\'].*?["\']'
    replacement = f'TELEGRAM_CHAT_ID = "{chat_id}"'
    
    if re.search(pattern, content):
        new_content = re.sub(pattern, replacement, content)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"✅ Automatically updated TELEGRAM_CHAT_ID in config.py!")
    else:
        print(f"Could not automatically modify config.py. Please manually set TELEGRAM_CHAT_ID = \"{chat_id}\" in config.py.")

async def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # Standard library handles this on non-Windows/older versions
        
    token = config.TELEGRAM_BOT_TOKEN
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        print("❌ Please set TELEGRAM_BOT_TOKEN in config.py first.")
        return
        
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        print("🔍 TELEGRAM_CHAT_ID is empty. Attempting auto-discovery...")
        discovered_id = await auto_discover_chat_id(token)
        if discovered_id:
            update_config_file(discovered_id)
            # Re-read config by modifying the module attribute directly
            config.TELEGRAM_CHAT_ID = discovered_id
            chat_id = discovered_id
        else:
            return
            
    print(f"\n🚀 Sending test notification using:")
    print(f"  Token: {token[:15]}...")
    print(f"  Chat ID: {chat_id}")
    
    test_result = (
        "This is a **test result** sent from the LocalLLM task runner!\n\n"
        "Here is a mock list of matches:\n"
        "- **[Senior Frontend Developer](https://www.sofi.com/careers/)** (San Francisco, CA)\n"
        "  - Tech Stack: React (Required), TypeScript (Yes)\n"
        "- **[Full Stack Engineer](https://www.sofi.com/careers/)** (Remote)\n"
        "  - Tech Stack: React (Required), TypeScript (No)"
    )
    
    await send_telegram_notification(
        task_id=999,
        prompt="Test Telegram Notification Integration",
        status="COMPLETED",
        result_content=test_result
    )
    print("\nCheck your Telegram bot! If you received the message, setup is complete. 🎉")

if __name__ == "__main__":
    asyncio.run(main())
