"""
Shared Message Formatting — Telegram & Discord

Formats the pipeline output into structured, readable signal reports.
Handles both bullish and bearish candidate formatting with full metrics.
"""

import logging
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# Regime display info
REGIME_DISPLAY = {
    'BULL': {'emoji': '\U0001f7e2', 'label': 'STRUCTURAL BULL', 'exposure': '100%'},
    'DIP': {'emoji': '\U0001f7e1', 'label': 'RISK-ON DIP', 'exposure': '60%'},
    'SIDEWAYS': {'emoji': '\U0001f7e0', 'label': 'VOLATILE SIDEWAYS', 'exposure': '30%'},
    'BEAR': {'emoji': '\U0001f534', 'label': 'BEAR / FII FLIGHT', 'exposure': '0%'},
}


def format_telegram_report(result: dict) -> str:
    """
    Format full signal report for Telegram (HTML parse mode).

    Args:
        result: Pipeline output dict with bullish, bearish, regime, macro

    Returns:
        Formatted HTML string for Telegram message
    """
    now = datetime.now(IST)
    regime = result.get('regime_name', 'UNKNOWN')
    regime_info = REGIME_DISPLAY.get(regime, REGIME_DISPLAY['SIDEWAYS'])

    lines = []

    # Header
    lines.append('\U0001f4ca <b>NSE ALPHA SYSTEM \u2014 Daily Signal Report</b>')
    lines.append(
        f'\U0001f4c5 {now.strftime("%A, %d %b %Y")} | '
        f'Regime: {regime_info["emoji"]} <b>{regime_info["label"]}</b> '
        f'({regime_info["exposure"]} exposure)'
    )
    lines.append('\u2501' * 36)

    # Bullish candidates
    bullish = result.get('bullish')
    if bullish is not None and not bullish.empty:
        lines.append('')
        lines.append('\u2B06\uFE0F <b>TOP BULLISH CANDIDATES</b>')
        lines.append('')

        for i, (_, row) in enumerate(bullish.iterrows(), 1):
            confidence = row.get('adj_confidence', row.get('confidence', 0))
            close = row.get('close', 0)
            target = row.get('target_high', 0)
            stop = row.get('stop_loss', 0)
            deliv_pct = row.get('deliv_pct', 0)
            atr = row.get('atr14', 0)
            rs_slope = row.get('rs_slope', 0)

            lines.append(f'<b>{i}. {row["symbol"]}</b> \u2014 Confidence: <b>{confidence:.0f}/100</b>')
            lines.append(f'   \U0001f4c8 CMP: \u20b9{close:,.0f} | Target: \u20b9{target:,.0f} | Stop: \u20b9{stop:,.0f}')

            if deliv_pct > 0:
                lines.append(f'   \U0001f4e6 Delivery%: {deliv_pct:.1f}%')

            lines.append(f'   \U0001f4d0 ATR14: \u20b9{atr:,.0f} | RS Slope: {rs_slope:+.1f}%/5d')

            if result.get('regime_scalar', 1.0) < 1.0:
                pct = int(result['regime_scalar'] * 100)
                lines.append(f'   \u26a0\ufe0f Regime: Size at {pct}% of normal ({regime})')

            lines.append('')
    else:
        lines.append('')
        lines.append('\u26a0\ufe0f No bullish candidates passed all 5 stages today.')
        lines.append('')

    lines.append('\u2501' * 36)

    # Bearish candidates
    bearish = result.get('bearish')
    if bearish is not None and not bearish.empty:
        lines.append('')
        lines.append('\U0001f534 <b>BEARISH CANDIDATES (Deteriorating Fundamentals)</b>')
        lines.append('')

        for i, (_, row) in enumerate(bearish.iterrows(), 1):
            lines.append(f'<b>{i}. {row["symbol"]}</b> \u2014 Short Signal Score: <b>{row.get("bearish_score", 0):.0f}/100</b>')
            lines.append(f'   \U0001f4c9 CMP: \u20b9{row.get("close", 0):,.0f} | M-Score: {row.get("m_score", 0):.1f}')
            lines.append(f'   \u26a0\ufe0f CCR: {row.get("ccr", 0):.2f} | Mansfield RS: {row.get("mansfield_rs", 0):.1f}')

            if row.get('lvgi_rising'):
                lines.append(f'   \U0001f6ab Rising LVGI ({row.get("lvgi", 0):.2f}) \u2014 leverage increasing QoQ')

            lines.append('')

    lines.append('\u2501' * 36)

    # Macro snapshot
    macro = result.get('macro_snapshot', {})
    lines.append('')
    lines.append('\U0001f310 <b>MACRO SNAPSHOT</b>')

    nifty = macro.get('nifty_close', 0)
    dma = macro.get('nifty_200dma', 0)
    dma_pct = macro.get('nifty_dma_pct', 0)
    lines.append(f'   Nifty 500: {nifty:,.0f} | 200 DMA: {dma:,.0f} ({dma_pct:+.1f}%)')

    vix = macro.get('india_vix', 0)
    lines.append(f'   India VIX: {vix:.1f}')

    inr_move = macro.get('usdinr_30d_move', 0)
    inr_trigger = '\u26a0\ufe0f FII flight risk!' if abs(inr_move) > 2 else ''
    lines.append(f'   USD/INR 30d move: {inr_move:+.1f}% {inr_trigger}')

    fii = macro.get('fii_net', 0)
    dii = macro.get('dii_net', 0)
    lines.append(f'   FII net: \u20b9{fii:,.0f} Cr | DII net: \u20b9{dii:,.0f} Cr')

    lines.append('')
    lines.append('\u2501' * 36)

    # Pipeline stats
    stats = result.get('pipeline_stats', {})
    lines.append(
        f'\u2699\ufe0f Universe: {stats.get("total_universe", 0)} | '
        f'Stage 1A: {stats.get("stage_1a_pass", 0)} | '
        f'Stage 1B: {stats.get("stage_1b_pass", 0)} | '
        f'Scored: {stats.get("stage_2_scored", 0)}'
    )
    lines.append('<i>Generated by NSE Alpha System v2.0 | NOT financial advice</i>')

    return '\n'.join(lines)


def format_discord_report(result: dict) -> str:
    """
    Format signal report for Discord (Markdown).
    Discord supports markdown but not HTML.
    """
    now = datetime.now(IST)
    regime = result.get('regime_name', 'UNKNOWN')
    regime_info = REGIME_DISPLAY.get(regime, REGIME_DISPLAY['SIDEWAYS'])

    lines = []

    lines.append('# NSE ALPHA SYSTEM - Daily Signal Report')
    lines.append(f'**{now.strftime("%A, %d %b %Y")}** | Regime: {regime_info["emoji"]} **{regime_info["label"]}** ({regime_info["exposure"]} exposure)')
    lines.append('---')

    # Bullish
    bullish = result.get('bullish')
    if bullish is not None and not bullish.empty:
        lines.append('## TOP BULLISH CANDIDATES')
        lines.append('')

        for i, (_, row) in enumerate(bullish.iterrows(), 1):
            confidence = row.get('adj_confidence', row.get('confidence', 0))
            close = row.get('close', 0)
            target = row.get('target_high', 0)
            stop = row.get('stop_loss', 0)
            atr = row.get('atr14', 0)

            lines.append(f'**{i}. {row["symbol"]}** - Confidence: **{confidence:.0f}/100**')
            lines.append(f'> CMP: Rs.{close:,.0f} | Target: Rs.{target:,.0f} | Stop: Rs.{stop:,.0f} | ATR14: Rs.{atr:,.0f}')
            lines.append('')
    else:
        lines.append('> No bullish candidates today.')

    lines.append('---')

    # Bearish
    bearish = result.get('bearish')
    if bearish is not None and not bearish.empty:
        lines.append('## BEARISH CANDIDATES')
        lines.append('')

        for i, (_, row) in enumerate(bearish.iterrows(), 1):
            lines.append(f'**{i}. {row["symbol"]}** - Score: **{row.get("bearish_score", 0):.0f}/100** | M-Score: {row.get("m_score", 0):.1f} | CCR: {row.get("ccr", 0):.2f}')

    lines.append('---')

    # Macro
    macro = result.get('macro_snapshot', {})
    lines.append('## MACRO SNAPSHOT')
    lines.append(f'Nifty 500: {macro.get("nifty_close", 0):,.0f} | VIX: {macro.get("india_vix", 0):.1f} | FII: Rs.{macro.get("fii_net", 0):,.0f} Cr | DII: Rs.{macro.get("dii_net", 0):,.0f} Cr')
    lines.append('')
    lines.append('*Generated by NSE Alpha System v2.0 | NOT financial advice*')

    return '\n'.join(lines)
