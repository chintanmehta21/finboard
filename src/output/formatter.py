"""
Shared Message Formatting — Telegram & Discord

Formats the pipeline output into structured, readable signal reports.
Handles both bullish and bearish candidate formatting with full metrics.
"""

import logging
from datetime import date, datetime

import pandas as pd
import pytz

from src.config import (
    SYSTEM_NAME, TELEGRAM_TOP_N, DISCORD_TOP_N, DIVIDER_TELEGRAM
)

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# Regime display info (no emoji for regime line)
REGIME_DISPLAY = {
    'BULL': {'label': 'STRUCTURAL BULL', 'exposure': '100%'},
    'DIP': {'label': 'RISK-ON DIP', 'exposure': '60%'},
    'SIDEWAYS': {'label': 'VOLATILE SIDEWAYS', 'exposure': '30%'},
    'BEAR': {'label': 'BEAR / FII FLIGHT', 'exposure': '0%'},
}


def _get_display_date(result: dict) -> str:
    """Get display date from result's last_trading_date, fallback to now."""
    ltd = result.get('last_trading_date')
    if ltd:
        if isinstance(ltd, date):
            return ltd.strftime('%A, %d %b %Y')
        return str(ltd)
    return datetime.now(IST).strftime('%A, %d %b %Y')


def format_telegram_report(result: dict) -> str:
    """
    Format full signal report for Telegram (HTML parse mode).

    Args:
        result: Pipeline output dict with bullish, bearish, regime, macro

    Returns:
        Formatted HTML string for Telegram message
    """
    display_date = _get_display_date(result)
    regime = result.get('regime_name', 'UNKNOWN')
    regime_info = REGIME_DISPLAY.get(regime, REGIME_DISPLAY['SIDEWAYS'])
    regime_scalar = result.get('regime_scalar', 1.0)

    lines = []

    # Header — clean, no emoji prefix
    lines.append(f'<b>{SYSTEM_NAME} — Daily Report</b>')
    lines.append(f'{display_date}')
    lines.append(f'Regime: <b>{regime_info["label"]}</b> ({regime_info["exposure"]} exp.)')

    # Regime warning for BEAR
    if regime_scalar == 0:
        lines.append('Bear regime — reduced sizing')

    lines.append(DIVIDER_TELEGRAM)

    # Bullish candidates (top N only)
    bullish = result.get('bullish')
    if bullish is not None:
        if isinstance(bullish, pd.DataFrame) and not bullish.empty:
            lines.append('')
            lines.append('<b>TOP BULLISH CANDIDATES</b>')
            lines.append('')

            top_n = bullish.head(TELEGRAM_TOP_N)
            for i, (_, row) in enumerate(top_n.iterrows(), 1):
                confidence = row.get('adj_confidence', row.get('defensive_score', row.get('confidence', 0)))
                close = row.get('close', 0)
                ret_3m = row.get('return_3m', 0)
                ret_1w = row.get('return_1w', 0)
                target = row.get('target_high', 0)
                stop = row.get('stop_loss', 0)

                lines.append(f'<b>{i}. {row["symbol"]}</b> — Score: <b>{confidence:.0f}</b>')
                # CMP → 3M Ret → 1W Ret → Target → S/L
                metrics = f'   CMP: ₹{close:,.0f}'
                if ret_3m:
                    metrics += f' | 3M: {ret_3m:+.1f}%'
                if ret_1w:
                    metrics += f' | 1W: {ret_1w:+.1f}%'
                lines.append(metrics)

                if target or stop:
                    target_line = '  '
                    if target:
                        target_line += f' Target: ₹{target:,.0f}'
                    if stop:
                        target_line += f' | S/L: ₹{stop:,.0f}'
                    lines.append(target_line)

                lines.append('')
        elif isinstance(bullish, list) and bullish:
            lines.append('')
            lines.append('<b>TOP BULLISH CANDIDATES</b>')
            lines.append('')
            for i, row in enumerate(bullish[:TELEGRAM_TOP_N], 1):
                lines.append(f'<b>{i}. {row.get("symbol", "")}</b>')
                lines.append(f'   CMP: ₹{row.get("close", 0):,.0f}')
                lines.append('')
        else:
            lines.append('')
            lines.append('No bullish candidates passed all stages today.')
            lines.append('')
    else:
        lines.append('')
        lines.append('No bullish candidates passed all stages today.')
        lines.append('')

    lines.append(DIVIDER_TELEGRAM)

    # Bearish candidates (top N only)
    bearish = result.get('bearish')
    if bearish is not None:
        if isinstance(bearish, pd.DataFrame) and not bearish.empty:
            lines.append('')
            lines.append('<b>BEARISH CANDIDATES</b>')
            lines.append('')

            top_n = bearish.head(TELEGRAM_TOP_N)
            for i, (_, row) in enumerate(top_n.iterrows(), 1):
                close = row.get('close', 0)
                ret_3m = row.get('return_3m', 0)
                ret_1w = row.get('return_1w', 0)
                lines.append(f'<b>{i}. {row["symbol"]}</b> — Score: <b>{row.get("bearish_score", 0):.0f}</b>')
                metrics = f'   CMP: ₹{close:,.0f}'
                if ret_3m:
                    metrics += f' | 3M: {ret_3m:+.1f}%'
                if ret_1w:
                    metrics += f' | 1W: {ret_1w:+.1f}%'
                lines.append(metrics)
                lines.append(f'   M-Score: {row.get("m_score", 0):.1f} | CCR: {row.get("ccr", 0):.2f} | RS: {row.get("mansfield_rs", 0):.1f}')
                lines.append('')

    lines.append(DIVIDER_TELEGRAM)

    # Macro snapshot
    macro = result.get('macro_snapshot', {})
    lines.append('')
    lines.append('<b>MACRO SNAPSHOT</b>')

    nifty = macro.get('nifty_close', 0)
    dma = macro.get('nifty_200dma', 0)
    dma_pct = macro.get('nifty_dma_pct', 0)
    lines.append(f'   Nifty 500: {nifty:,.0f} | 200 DMA: {dma:,.0f} ({dma_pct:+.1f}%)')

    vix = macro.get('india_vix', 0)
    lines.append(f'   India VIX: {vix:.1f}')

    usdinr = macro.get('usdinr', 0)
    inr_move = macro.get('usdinr_30d_move', 0)
    lines.append(f'   USD/INR: {usdinr:.2f} (30d: {inr_move:+.2f}%)')

    fii = macro.get('fii_net', 0)
    dii = macro.get('dii_net', 0)
    lines.append(f'   FII net: ₹{fii:,.0f} Cr | DII net: ₹{dii:,.0f} Cr')

    lines.append('')
    lines.append(DIVIDER_TELEGRAM)

    # Footer — minimal
    lines.append('<i>NOT financial advice</i>')

    return '\n'.join(lines)


def format_discord_report(result: dict) -> str:
    """
    Format signal report for Discord (Markdown).
    Mirrors Telegram formatting with Discord markdown syntax.
    """
    display_date = _get_display_date(result)
    regime = result.get('regime_name', 'UNKNOWN')
    regime_info = REGIME_DISPLAY.get(regime, REGIME_DISPLAY['SIDEWAYS'])
    regime_scalar = result.get('regime_scalar', 1.0)

    lines = []

    # Header
    lines.append(f'# {SYSTEM_NAME} — Daily Report')
    lines.append(f'**{display_date}**')
    lines.append(f'Regime: **{regime_info["label"]}** ({regime_info["exposure"]} exp.)')

    if regime_scalar == 0:
        lines.append('Bear regime — reduced sizing')

    lines.append('---')

    # Bullish
    bullish = result.get('bullish')
    if bullish is not None:
        if isinstance(bullish, pd.DataFrame) and not bullish.empty:
            lines.append('## TOP BULLISH CANDIDATES')
            lines.append('')

            top_n = bullish.head(DISCORD_TOP_N)
            for i, (_, row) in enumerate(top_n.iterrows(), 1):
                confidence = row.get('adj_confidence', row.get('defensive_score', row.get('confidence', 0)))
                close = row.get('close', 0)
                ret_3m = row.get('return_3m', 0)
                ret_1w = row.get('return_1w', 0)
                target = row.get('target_high', 0)
                stop = row.get('stop_loss', 0)

                lines.append(f'**{i}. {row["symbol"]}** — Score: **{confidence:.0f}**')
                metrics = f'> CMP: Rs.{close:,.0f}'
                if ret_3m:
                    metrics += f' | 3M: {ret_3m:+.1f}%'
                if ret_1w:
                    metrics += f' | 1W: {ret_1w:+.1f}%'
                if target:
                    metrics += f' | Target: Rs.{target:,.0f}'
                if stop:
                    metrics += f' | S/L: Rs.{stop:,.0f}'
                lines.append(metrics)
                lines.append('')
        else:
            lines.append('> No bullish candidates today.')
    else:
        lines.append('> No bullish candidates today.')

    lines.append('---')

    # Bearish
    bearish = result.get('bearish')
    if bearish is not None and isinstance(bearish, pd.DataFrame) and not bearish.empty:
        lines.append('## BEARISH CANDIDATES')
        lines.append('')

        top_n = bearish.head(DISCORD_TOP_N)
        for i, (_, row) in enumerate(top_n.iterrows(), 1):
            close = row.get('close', 0)
            ret_3m = row.get('return_3m', 0)
            ret_1w = row.get('return_1w', 0)
            lines.append(f'**{i}. {row["symbol"]}** — Score: **{row.get("bearish_score", 0):.0f}**')
            metrics = f'> CMP: Rs.{close:,.0f}'
            if ret_3m:
                metrics += f' | 3M: {ret_3m:+.1f}%'
            if ret_1w:
                metrics += f' | 1W: {ret_1w:+.1f}%'
            metrics += f' | M-Score: {row.get("m_score", 0):.1f} | CCR: {row.get("ccr", 0):.2f}'
            lines.append(metrics)
            lines.append('')

    lines.append('---')

    # Macro
    macro = result.get('macro_snapshot', {})
    lines.append('## MACRO SNAPSHOT')
    usdinr = macro.get('usdinr', 0)
    inr_move = macro.get('usdinr_30d_move', 0)
    lines.append(
        f'Nifty 500: {macro.get("nifty_close", 0):,.0f} | '
        f'VIX: {macro.get("india_vix", 0):.1f} | '
        f'USD/INR: {usdinr:.2f} ({inr_move:+.2f}%) | '
        f'FII: Rs.{macro.get("fii_net", 0):,.0f} Cr | '
        f'DII: Rs.{macro.get("dii_net", 0):,.0f} Cr'
    )
    lines.append('')
    lines.append('*NOT financial advice*')

    return '\n'.join(lines)
