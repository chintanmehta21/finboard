"""
Telegram Bot — Signal Report Delivery

Sends formatted signal reports to a private Telegram channel via Bot API.
Handles message chunking for Telegram's 4096-character limit.
"""

import logging

import requests

from .formatter import format_telegram_report
from src.config import SYSTEM_NAME
from src.utils.key_loader import get_key

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/sendMessage'
MAX_MESSAGE_LENGTH = 4000  # Leave margin below 4096 limit


def send_signal_report(result: dict) -> bool:
    """
    Format and send the full signal report to Telegram.

    Args:
        result: Pipeline output dict

    Returns:
        True if all messages sent successfully
    """
    token = get_key('TELEGRAM_TOKEN')
    chat_id = get_key('TELEGRAM_CHAT')

    if not token or not chat_id:
        logger.warning("Telegram credentials not configured, skipping send")
        return False

    message = format_telegram_report(result)
    return _send_message(token, chat_id, message)


def send_holiday_message() -> bool:
    """Send a market holiday notification (single clean message)."""
    token = get_key('TELEGRAM_TOKEN')
    chat_id = get_key('TELEGRAM_CHAT')

    if not token or not chat_id:
        return False

    msg = (
        f'<b>{SYSTEM_NAME} — Error</b>\n\n'
        'Market holiday today.\n'
        'System will resume on the next trading day.'
    )
    return _send_message(token, chat_id, msg)


def send_error_message(error: str) -> bool:
    """Send an error notification to Telegram (single clean message, no raw trace)."""
    token = get_key('TELEGRAM_TOKEN')
    chat_id = get_key('TELEGRAM_CHAT')

    if not token or not chat_id:
        return False

    msg = (
        f'<b>{SYSTEM_NAME} — Error</b>\n\n'
        'Pipeline encountered an error.\n'
        'Check GitHub Actions logs for details.'
    )
    return _send_message(token, chat_id, msg)


def _send_message(token: str, chat_id: str, text: str) -> bool:
    """Send a message to Telegram, chunking if necessary."""
    url = TELEGRAM_API.format(token=token)

    # Split into chunks if message exceeds Telegram limit
    chunks = _chunk_message(text, MAX_MESSAGE_LENGTH)

    success = True
    for chunk in chunks:
        try:
            resp = requests.post(url, json={
                'chat_id': chat_id,
                'text': chunk,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            }, timeout=30)

            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.status_code} - {resp.text}")
                success = False
            else:
                logger.info(f"Telegram message sent ({len(chunk)} chars)")

        except requests.RequestException as e:
            logger.error(f"Telegram send error: {e}")
            success = False

    return success


def _chunk_message(text: str, max_len: int) -> list[str]:
    """Split message into chunks at line boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ''

    for line in text.split('\n'):
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f'{current}\n{line}' if current else line

    if current:
        chunks.append(current)

    return chunks
