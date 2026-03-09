"""
Chat Scanner — pulls 6 months of work chat history via Telethon.
Extracts conversations, groups them by chat, and prepares for analysis.
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel

logger = logging.getLogger(__name__)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
STRING_SESSION = os.getenv("TELEGRAM_STRING_SESSION", "")

# Work chats to scan — chat IDs or usernames
# These will be configured via env var as comma-separated list
WORK_CHAT_IDS = os.getenv("WORK_CHAT_IDS", "").split(",")
# Alternatively, scan all dialogs and filter by keywords
WORK_KEYWORDS = ["zbs", "zbsmedia", "premium", "реклама", "клиент", 
                  "проект", "съемк", "монтаж", "бюджет", "оплат",
                  "план банан", "planbanan", "charvak", "спонсор",
                  "тендер", "pepsi", "коммерч", "продакшн",
                  "trabaja", "блогер"]

# Months to scan
SCAN_MONTHS = int(os.getenv("SCAN_MONTHS", "6"))

# Max messages per chat to avoid overwhelming the analyzer
MAX_MESSAGES_PER_CHAT = int(os.getenv("MAX_MESSAGES_PER_CHAT", "2000"))


def get_client():
    """Create Telethon client from StringSession."""
    return TelegramClient(
        StringSession(STRING_SESSION),
        API_ID,
        API_HASH
    )


async def get_work_dialogs(client) -> list:
    """
    Get all work-related dialogs.
    Strategy: 
    1. If WORK_CHAT_IDS provided, use those
    2. Otherwise, scan all dialogs and filter by keywords in title/messages
    """
    dialogs = []
    
    # If explicit chat IDs provided
    explicit_ids = [cid.strip() for cid in WORK_CHAT_IDS if cid.strip()]
    if explicit_ids:
        for chat_id in explicit_ids:
            try:
                entity = await client.get_entity(chat_id if not chat_id.lstrip('-').isdigit() else int(chat_id))
                name = getattr(entity, 'title', None) or getattr(entity, 'first_name', '') + ' ' + getattr(entity, 'last_name', '')
                dialogs.append({
                    "entity": entity,
                    "id": entity.id,
                    "name": name.strip(),
                    "type": type(entity).__name__
                })
            except Exception as e:
                logger.warning(f"Could not resolve chat {chat_id}: {e}")
        return dialogs
    
    # Auto-detect work dialogs
    logger.info("No explicit chat IDs — auto-detecting work dialogs...")
    async for dialog in client.iter_dialogs(limit=100):
        title = (dialog.name or "").lower()
        
        # Skip obvious non-work chats
        if dialog.is_channel and not dialog.is_group:
            continue  # Skip broadcast channels, keep supergroups
            
        # Check if dialog title matches work keywords
        is_work = any(kw in title for kw in WORK_KEYWORDS)
        
        # For private chats, check recent messages for work context
        if not is_work and dialog.is_user:
            try:
                messages = []
                async for msg in client.iter_messages(dialog.entity, limit=20):
                    if msg.text:
                        messages.append(msg.text.lower())
                combined = " ".join(messages)
                is_work = any(kw in combined for kw in WORK_KEYWORDS)
            except Exception:
                pass
        
        if is_work:
            dialogs.append({
                "entity": dialog.entity,
                "id": dialog.id,
                "name": dialog.name or "Unknown",
                "type": type(dialog.entity).__name__
            })
    
    logger.info(f"Found {len(dialogs)} work-related dialogs")
    return dialogs


async def scan_chat_history(client, entity, months=SCAN_MONTHS) -> list:
    """
    Pull messages from a single chat for the last N months.
    Returns structured message data.
    """
    since = datetime.now(timezone.utc) - timedelta(days=months * 30)
    messages = []
    count = 0
    
    try:
        async for msg in client.iter_messages(entity, offset_date=datetime.now(timezone.utc), reverse=False):
            if msg.date < since:
                break
            if count >= MAX_MESSAGES_PER_CHAT:
                break
                
            if msg.text:
                sender_name = "Unknown"
                try:
                    sender = await msg.get_sender()
                    if sender:
                        if isinstance(sender, User):
                            sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                            if sender.username:
                                sender_name += f" (@{sender.username})"
                        elif hasattr(sender, 'title'):
                            sender_name = sender.title
                except Exception:
                    pass
                
                messages.append({
                    "date": msg.date.isoformat(),
                    "sender": sender_name,
                    "text": msg.text,
                    "reply_to": msg.reply_to_msg_id,
                })
                count += 1
                
    except Exception as e:
        logger.error(f"Error scanning chat: {e}")
    
    # Reverse to chronological order
    messages.reverse()
    return messages


async def scan_all_work_chats() -> dict:
    """
    Main scan function. Returns structured data for all work chats.
    Returns: {
        "chats": {
            "chat_name": {
                "id": ...,
                "type": ...,
                "messages": [...],
                "message_count": ...
            }
        },
        "total_messages": ...,
        "scan_period": "6 months",
        "scanned_at": "..."
    }
    """
    client = get_client()
    await client.start()
    
    try:
        dialogs = await get_work_dialogs(client)
        logger.info(f"Scanning {len(dialogs)} work chats...")
        
        result = {
            "chats": {},
            "total_messages": 0,
            "total_chats": len(dialogs),
            "scan_period": f"{SCAN_MONTHS} months",
            "scanned_at": datetime.now(timezone.utc).isoformat()
        }
        
        for dialog in dialogs:
            logger.info(f"Scanning: {dialog['name']} ({dialog['type']})...")
            messages = await scan_chat_history(client, dialog["entity"])
            
            if messages:
                result["chats"][dialog["name"]] = {
                    "id": dialog["id"],
                    "type": dialog["type"],
                    "messages": messages,
                    "message_count": len(messages)
                }
                result["total_messages"] += len(messages)
                logger.info(f"  → {len(messages)} messages")
        
        return result
        
    finally:
        await client.disconnect()


def chunk_messages(messages: list, chunk_size: int = 100) -> list:
    """Split messages into chunks for API analysis."""
    chunks = []
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i:i + chunk_size]
        chunks.append(chunk)
    return chunks


def format_messages_for_analysis(messages: list) -> str:
    """Format messages into readable text for Claude analysis."""
    lines = []
    for msg in messages:
        date = msg["date"][:10]
        lines.append(f"[{date}] {msg['sender']}: {msg['text']}")
    return "\n".join(lines)
