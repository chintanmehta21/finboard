# Finboard v1 — Web Dashboard Layout

Snapshot as of 2026-03-05. The dashboard is a Next.js 14 (App Router) static site deployed on Vercel. It reads `signals.json` from `/data/signals.json` on page load.

**Tech stack**: Next.js 14, React, vanilla CSS (no component library), static export
**File**: `dashboard/app/page.js` (all components), `dashboard/app/globals.css` (all styles)

---

## Overall Structure (top to bottom)

```
+--------------------------------------------------+
|                   HEADER                          |
|              "Finboard" + date                    |
+--------------------------------------------------+
|              REGIME BANNER                        |
|     Regime: LABEL     XX% Exposure                |
+--------------------------------------------------+
|               PIPELINE STATS BAR                  |
|   Universe: N   Stage 1A: N   Stage 1B: N   ...  |
+--------------------------------------------------+
|                 MACRO GRID (5 cards)              |
| Nifty 500 | India VIX | USD/INR | FII Net | DII |
+--------------------------------------------------+
|          BULLISH CANDIDATES SECTION               |
|  Card 1, Card 2, Card 3, ... (max 10)            |
+--------------------------------------------------+
|          BEARISH CANDIDATES SECTION               |
|  Card 1, Card 2, Card 3, ... (max 10)            |
+--------------------------------------------------+
|                  FOOTER                           |
+--------------------------------------------------+
```

Max width: 1400px, centered. Background: #0a0e17 (very dark blue-gray).

---

## Element-by-Element Breakdown

### 1. Header Block

- **Container**: `.header` — Centered text, dark secondary background (#111827), 1px border, 12px border-radius
- **Title**: `<h1>` "Finboard" — Montserrat font, 28px, bold, letter-spacing 0.5px
- **Date**: `.date` — "Last Updated: {display_date}" — muted gray (#6b7280), 13px
- **Data source**: `data.display_date` from signals.json (e.g., "Thursday, 05 Mar 2026")
- **Margin bottom**: 6px to regime banner

### 2. Regime Banner

- **Container**: `.regime-banner` — Flexbox row, centered, 7px vertical padding, 8px border-radius
- **Content**: Two spans on one line:
  - Left: "Regime: {REGIME_LABEL}" (e.g., "Regime: BEAR / FII FLIGHT")
  - Right: `.regime-badge` — "{exposure_pct}% Exposure" (e.g., "0% Exposure")
- **Color coding** (CSS class applied dynamically):
  - `.bull` — Green background (rgba(16,185,129,0.15)), green border and text (#10b981)
  - `.dip` — Yellow background, yellow border and text (#f59e0b)
  - `.sideways` — Orange background, orange border and text (#f97316)
  - `.bear` — Red background (rgba(239,68,68,0.15)), red border and text (#ef4444)
- **Regime label mapping** (from `REGIME_CONFIG` in page.js):
  - BULL -> "STRUCTURAL BULL"
  - DIP -> "RISK-ON DIP"
  - SIDEWAYS -> "VOLATILE SIDEWAYS"
  - BEAR -> "BEAR / FII FLIGHT"
- **Exposure badge**: 12px bold text, 4px border-radius, letter-spacing 1px
- **No icon/emoji**: Previously had a red dot, now removed
- **Data source**: `data.regime.name` and `data.regime.exposure_pct`

### 3. Pipeline Stats Bar

- **Container**: `.stats-bar` — Flexbox row, centered, 6px padding, dark secondary background, 8px border-radius
- **Items**: 4 stat items displayed inline with 24px gap:
  - "Universe: {total_universe}" — Total symbols analyzed
  - "Stage 1A: {stage_1a_pass}" — Passed forensic filter
  - "Stage 1B: {stage_1b_pass}" — Passed liquidity filter
  - "Scored: {stage_2_scored}" — Passed all gates, received scores
- **Style**: 12px font, muted text, white values
- **Data source**: `data.pipeline_stats`

### 4. Macro Grid

- **Container**: `.macro-grid` — CSS Grid, 5 columns (desktop), 12px gap, 16px bottom margin
- **Responsive**: 3 columns at 768px, 2 columns at 480px
- **Each card** (`.macro-card`):
  - Background: #1a2332, 1px border #2a3548, 8px border-radius, 16px padding, centered text
  - **Label** (`.label`): 11px uppercase, letter-spacing 1px, muted gray
  - **Value** (`.value`): 22px bold, colored green (positive) or red (negative)
  - **Change** (`.change`): 12px, colored green/red

**Card details**:

| # | Label | Value | Change | Green when |
|---|-------|-------|--------|------------|
| 1 | NIFTY 500 | `nifty_close` formatted with Indian comma grouping | `"{nifty_dma_pct}% vs 200 DMA"` | dma_pct > 0 |
| 2 | INDIA VIX | `india_vix` raw number | "Elevated" if > 20, else "Normal" | vix < 20 |
| 3 | USD/INR | `usdinr` to 2 decimal places | `"30d: {usdinr_30d_move}%"` | abs(move) < 2 |
| 4 | FII NET | `fii_net` formatted + " Cr" | (none) | fii_net > 0 |
| 5 | DII NET | `dii_net` formatted + " Cr" | (none) | dii_net > 0 |

**Number formatting**: `fmt()` function uses `toLocaleString('en-IN')` for Indian number formatting (e.g., 22,698 not 22698).

### 5. Bullish Candidates Section

- **Section title**: `.section-title.bullish` — 18px bold, green bottom border (2px), flex row with:
  - Text: "Bullish Candidates"
  - Count badge: `.section-count` — small rounded pill showing count (e.g., "3")
- **Always labeled "Bullish Candidates"** regardless of regime (even in BEAR when showing defensive rotation picks)

**Signal cards** (`.signal-grid`): Vertical stack, 10px gap between cards. Max 10 displayed (`MAX_DISPLAY = 10`).

**Each Bullish Card** (`.signal-card`):
- Layout: CSS Grid with 2 columns — `1fr auto` (content left, score right)
- Hover: Background darkens to #1f2b3d
- Padding: 16px 20px

**Card left side** (`.card-content`):

- **Header row** (`.card-header`): Flex row with:
  - Rank: `#1`, `#2`, etc. — 12px muted gray
  - Symbol: `RELIANCE` — 18px bold white
  - Sector tag: `.sector-tag` — 10px muted, secondary bg, small pill border

- **Metrics row** (`.metrics`): Flex wrap, 16px gap, 13px font, secondary gray text. Each metric:
  - Label (`.metric-label`): 11px muted text above the value
  - Value: 13px, colored if applicable

  **Metric display order** (conditionally shown):
  1. **CMP** — Current Market Price. Always shown. Format: `Rs.{close}` with Indian commas
  2. **3M Ret** — 3-month return %. Shown if `return_3m != null`. Green if positive, red if negative. Format: `{return_3m}%`
  3. **1W Ret** — 1-week return %. Shown if `return_1w != null`. Green/red colored. Format: `{return_1w}%`
  4. **Target** — Price target (entry + 3*ATR). Shown if `target_high` exists. Format: `Rs.{target_high}`
  5. **S/L** — Stop loss (entry - 2*ATR). Shown if `stop_loss` exists. Format: `Rs.{stop_loss}`
  6. **ATR14** — 14-period Average True Range. Shown if exists. Format: `Rs.{atr14}`
  7. **Delivery%** — Delivery percentage from bhavcopy. Shown if `deliv_pct > 0`. Format: `{deliv_pct}%`
  8. **CCR** — Cash Conversion Ratio. Shown if exists. Format: `{ccr}` (2 decimal places)
  9. **D/E** — Debt to Equity ratio. Shown if exists. Format: `{debt_equity}` (2 decimal places)

- **Regime warning** (conditional): Shown ONLY when regime scalar is between 0 and 1 (exclusive) — i.e., DIP (60%) or SIDEWAYS (30%). Text: `"Regime: {scalar*100}% exp. ({regime_name})"`. Yellow text (#f59e0b). NOT shown in BULL (100%) or BEAR (0%).

**Card right side** (`.confidence`):
- Score: `.confidence-score.positive` — 28px bold green number (e.g., "78")
- Label: `/100` — 11px muted text below the score
- The score is `adj_confidence` (or `defensive_score` in BEAR, or `confidence` as final fallback), capped at 100

**Empty state**: When no bullish candidates exist, a single card with centered muted text: "No bullish candidates passed all stages today."

### 6. Bearish Candidates Section

- **Section title**: `.section-title.bearish` — Same layout as bullish, but red bottom border
- **Text**: "Bearish Candidates" with count badge

**Each Bearish Card** (`.signal-card`):
- Same grid layout as bullish cards

**Card left side metrics** (in order):
1. **CMP** — Current price
2. **3M Ret** — 3-month return (shown if exists)
3. **1W Ret** — 1-week return (shown if exists)
4. **M-Score** — Beneish M-Score. Red text. Higher (less negative) = more suspicious
5. **CCR** — Cash Conversion Ratio
6. **RS** — Mansfield Relative Strength (negative = underperforming)
7. **LVGI** — Leverage Index. Shows upward arrow if `lvgi_rising` is true

**Card right side**: Bearish score (0-100). `.confidence-score.negative` — 28px bold RED number.

**Empty state**: "No bearish candidates identified today."

### 7. Footer

- **Container**: `.footer` — Centered, 24px padding, 12px font, muted gray, top border 1px
- **Content** (3 lines separated by `<br />`):
  - "Finboard v2.0 | NOT financial advice"
  - "Data updated daily at 9:00 AM IST (Mon-Fri)"
  - "Made by [CM](linkedin link)" — CM is a blue link (#3b82f6), turns green on hover

---

## Loading & Error States

**Loading state**: While `signals.json` is being fetched, displays centered "Loading signals..." in secondary text color.

**Error state** (`NoDataView` component): When fetch fails (non-200 response):
- Shows "Finboard" header only
- Centered message: "No signal data available yet"
- Sub-message: The specific error text
- "The pipeline runs daily at 9:00 AM IST (Mon-Fri)."

---

## Responsive Breakpoints

| Breakpoint | Changes |
|-----------|---------|
| **Desktop** (> 768px) | 5-column macro grid, full-size text |
| **Tablet** (768px) | 3-column macro grid, smaller fonts, tighter padding |
| **Mobile** (480px) | 2-column macro grid, signal cards with 56px score column |
| **Very small** (360px) | Regime banner stacks vertically, 48px score column, 10px metrics |

---

## Color Scheme (CSS Variables)

| Variable | Value | Usage |
|----------|-------|-------|
| `--bg-primary` | #0a0e17 | Page background |
| `--bg-secondary` | #111827 | Header, stats bar |
| `--bg-card` | #1a2332 | Macro cards, signal cards |
| `--bg-card-hover` | #1f2b3d | Card hover state |
| `--border` | #2a3548 | All borders |
| `--text-primary` | #e5e7eb | Main text (light gray) |
| `--text-secondary` | #9ca3af | Secondary text |
| `--text-muted` | #6b7280 | Labels, ranks |
| `--accent-green` | #10b981 | Positive values, bull regime |
| `--accent-red` | #ef4444 | Negative values, bear regime |
| `--accent-yellow` | #f59e0b | DIP regime, warnings |
| `--accent-blue` | #3b82f6 | Links |
| `--accent-orange` | #f97316 | SIDEWAYS regime |
