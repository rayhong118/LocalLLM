import html
import re
import httpx
import logging
import config

logger = logging.getLogger(__name__)

def markdown_to_telegram_html(text: str) -> str:
    """Safely converts basic markdown (**bold**, `code`, [link](url)) to Telegram HTML format."""
    # First escape raw HTML special characters
    escaped = html.escape(text)
    
    # [Text](Url) -> <a href="Url">Text</a>
    pattern_link = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    escaped = re.sub(pattern_link, r'<a href="\2">\1</a>', escaped)
    
    # **Bold** -> <b>Bold</b>
    escaped = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', escaped)
    
    # `Code` -> <code>Code</code>
    escaped = re.sub(r'`([^`]+)`', r'<code>\1</code>', escaped)
    
    return escaped

async def send_telegram_notification(task_id: int, prompt: str, status: str, result_content: str):
    """Sends a completion/failure report via the Telegram Bot API."""
    token = getattr(config, "TELEGRAM_BOT_TOKEN", None)
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", None)
    
    if not token or not chat_id:
        logger.info("[Telegram] Notification skipped: Credentials not configured.")
        return

    status_emoji = "✅" if status == "COMPLETED" else "❌"
    escaped_prompt = html.escape(prompt)
    formatted_result = markdown_to_telegram_html(result_content)
    
    # Truncate if content is too long for Telegram (max 4096 characters)
    max_len = 3800
    if len(formatted_result) > max_len:
        formatted_result = formatted_result[:max_len] + "\n\n<i>(Result truncated...)</i>"

    message = (
        f"{status_emoji} <b>Task Completion Report</b>\n"
        f"<b>Task ID:</b> {task_id}\n"
        f"<b>Prompt:</b> {escaped_prompt}\n"
        f"<b>Status:</b> {status}\n\n"
        f"<b>Result:</b>\n{formatted_result}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"[Telegram] Notification sent for task {task_id}")
            else:
                logger.error(f"[Telegram] Bot API returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"[Telegram] Connection failed: {e}")
