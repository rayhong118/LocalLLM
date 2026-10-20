import html
import re
import httpx
import logging
from backend import config

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

    status_emojis = {
        "SUCCESS": "✅",
        "COMPLETED": "✅",
        "PARTIAL_SUCCESS": "🟡",
        "NO_RESULT": "⚪",
        "FAILED": "❌",
        "CANCELLED": "⏹️",
    }
    status_emoji = status_emojis.get(status, "📋")
    escaped_prompt = html.escape(prompt)

    # 1. Strip raw data payload code blocks (e.g. wsdot-map-data) before sending to Telegram
    clean_content = re.sub(r'```(?:wsdot-map-data|[a-z0-9_-]+-data)[\s\S]*?```', '', result_content or '').strip()

    # 2. Pre-truncate markdown before HTML formatting to prevent slicing tags in half
    max_raw_len = 3200
    is_truncated = False
    if len(clean_content) > max_raw_len:
        clean_content = clean_content[:max_raw_len].rstrip()
        is_truncated = True

    formatted_result = markdown_to_telegram_html(clean_content)
    if is_truncated:
        formatted_result += "\n\n<i>(Result truncated for Telegram...)</i>"

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
                # Fallback: If Telegram fails with 400 (e.g. unclosed HTML tag), retry sending as plain text
                logger.warning(f"[Telegram] HTML parse failed ({resp.status_code}: {resp.text}); retrying with plain text fallback.")
                fallback_text = (
                    f"{status_emoji} Task Completion Report\n"
                    f"Task ID: {task_id}\n"
                    f"Prompt: {prompt}\n"
                    f"Status: {status}\n\n"
                    f"Result:\n{clean_content[:3500]}"
                )
                fallback_payload = {
                    "chat_id": chat_id,
                    "text": fallback_text,
                    "disable_web_page_preview": True
                }
                fallback_resp = await client.post(url, json=fallback_payload, timeout=15)
                if fallback_resp.status_code == 200:
                    logger.info(f"[Telegram] Plain text fallback notification sent for task {task_id}")
                else:
                    logger.error(f"[Telegram] Plain text fallback also failed: {fallback_resp.text}")
    except Exception as e:
        logger.error(f"[Telegram] Connection failed: {e}")
