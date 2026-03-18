"""
Discord Webhook — Signal Report Delivery

Sends formatted signal reports to a Discord channel via webhook URL.
Discord webhooks are free, require no bot hosting, and support rich embeds.
"""

import logging

import requests

from .formatter import format_discord_report
from src.config import SYSTEM_NAME
from src.utils.key_loader import get_key

logger = logging.getLogger(__name__)

MAX_DISCORD_LENGTH = 1900  # Discord limit is 2000 per message


def send_signal_report(result: dict) -> bool:
    """
    Format and send the full signal report to Discord.

    Args:
        result: Pipeline output dict

    Returns:
        True if all messages sent successfully
    """
    webhook_url = get_key('DISCORD_WEBHOOK_URL')

    if not webhook_url:
        logger.info("Discord webhook not configured, skipping")
        return False

    message = format_discord_report(result)
    return _send_webhook(webhook_url, message)


def send_holiday_message() -> bool:
    """Send a market holiday notification (single clean message)."""
    webhook_url = get_key('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        return False

    msg = (
        f'## {SYSTEM_NAME} — Error\n\n'
        'Market holiday today.\n'
        'System will resume on the next trading day.'
    )
    return _send_webhook(webhook_url, msg)


def send_error_message(error: str) -> bool:
    """Send an error notification to Discord (single clean message, no raw trace)."""
    webhook_url = get_key('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        return False

    msg = (
        f'## {SYSTEM_NAME} — Error\n\n'
        'Pipeline encountered an error.\n'
        'Check GitHub Actions logs for details.'
    )
    return _send_webhook(webhook_url, msg)


def _send_webhook(webhook_url: str, content: str) -> bool:
    """Send content to Discord webhook, chunking if needed."""
    chunks = _chunk_message(content, MAX_DISCORD_LENGTH)

    success = True
    for chunk in chunks:
        try:
            resp = requests.post(
                webhook_url,
                json={'content': chunk, 'username': SYSTEM_NAME},
                timeout=30
            )

            if resp.status_code not in (200, 204):
                logger.error(f"Discord send failed: {resp.status_code} - {resp.text}")
                success = False
            else:
                logger.info(f"Discord message sent ({len(chunk)} chars)")

        except requests.RequestException as e:
            logger.error(f"Discord send error: {e}")
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
