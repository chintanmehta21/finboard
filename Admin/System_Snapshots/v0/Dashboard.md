# Finboard v2.0 — Web Dashboard

**Snapshot Date**: 2026-03-17
**Source Files**: `dashboard/` (app/page.js, app/layout.js, app/globals.css, next.config.js, package.json)

---

## Overview

The dashboard is a static Next.js 14 web application deployed on Vercel. It reads `signals.json` (updated daily by the Python pipeline) and renders the current market regime, macro indicators, and ranked stock signals in a dark terminal-style interface.

---

## Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | Next.js (App Router) | 14.2.0 |
| UI Library | React | 18.2.0 |
| Styling | Custom CSS (no Tailwind) | CSS variables |
| Font | Montserrat 700 (Google Fonts) | via next/font |
| Build | Static export (SSG) | `output: 'export'` |
| Hosting | Vercel | Free tier |
| Data Source | `signals.json` (fetched client-side) | Updated daily |

---

## File Structure

```
dashboard/
├── app/
│   ├── page.js              Main dashboard component (all UI logic)
│   ├── layout.js            Root layout + metadata + font
│   └── globals.css          Global styles (544 lines, full theme)
├── public/
│   ├── data/
│   │   ├── signals.json     Current day signals (updated by pipeline)
│   │   └── signals_prev.json Previous day signals (auto-backup)
│   └── favicon.svg          Finboard "F" icon (green on dark)
├── package.json             Dependencies (Next.js, React, React-DOM)
├── next.config.js           Static export configuration
├── vercel.json              Vercel build settings
└── .env.local               NEXT_PUBLIC_CM_HYPERLINK
```

---

## Build & Deployment

### next.config.js
```javascript
const nextConfig = {
    output: 'export',           // Static HTML export (no server)
    trailingSlash: true,        // URLs end with /
    images: { unoptimized: true },  // No Image optimization (static)
    allowedDevOrigins: ['127.0.0.1', 'localhost'],
};
```

### vercel.json
```json
{
    "buildCommand": "npm run build",
    "outputDirectory": "out",
    "framework": "nextjs"
}
```

### Build Flow
```
Vercel detects git push to main
    → cd dashboard && npm install
    → npm run build (next build)
    → Output: dashboard/out/ (static HTML/CSS/JS)
    → Deploy to Vercel CDN
    → Available at finboard.vercel.app (or custom domain)
```

---

## Layout & Metadata — `app/layout.js`

### Root Layout
- **Font**: Montserrat 700 weight loaded via `next/font/google`
- **CSS Variable**: `--font-montserrat` applied to `<html>` element
- **Language**: `en`

### SEO Metadata
```javascript
export const metadata = {
    title: 'FinBoard — Market Signals',
    description: 'Daily quantitative signal dashboard for NSE 500 stocks',
    icons: { icon: '/favicon.svg' },
    openGraph: {
        title: 'FinBoard — Market Signals',
        description: 'Daily quantitative signal dashboard for NSE 500 stocks',
        type: 'website',
    },
};
```

---

## Main Page — `app/page.js`

### Component Architecture
```
Dashboard (page.js, 'use client')
    │
    ├─ Data Fetching (useEffect → fetch('/data/signals.json'))
    │
    ├─ Header — Title + last updated timestamp
    │
    ├─ Regime Banner — Color-coded regime + exposure %
    │
    ├─ Stats Bar — Universe → 1A → 1B → Scored funnel
    │
    ├─ Macro Grid (5 cards)
    │   ├─ MacroCard: Nifty 500
    │   ├─ MacroCard: India VIX
    │   ├─ MacroCard: USD/INR
    │   ├─ MacroCard: FII Net
    │   └─ MacroCard: DII Net
    │
    ├─ Bullish Section
    │   └─ BullishCard × 10 (max)
    │
    ├─ Bearish Section
    │   └─ BearishCard × 10 (max)
    │
    └─ Footer — Copyright, disclaimer, CM link
```

### Data Fetching
```javascript
useEffect(() => {
    fetch('/data/signals.json')
        .then(res => {
            if (!res.ok) throw new Error('Signal data not yet available');
            return res.json();
        })
        .then(setData)
        .catch(e => setError(e.message));
}, []);
```
- Client-side fetch on mount (no SSR data fetching)
- No polling or refresh interval
- Error state shows `NoDataView` component

### Data Processing
```javascript
const regime = data.regime || {};
const regimeKey = regime.name || 'SIDEWAYS';
const rc = REGIME_CONFIG[regimeKey] || REGIME_CONFIG.SIDEWAYS;
const macro = data.macro || {};
const stats = data.pipeline_stats || {};

// Sort by confidence, take top 10
const bullish = (data.bullish || [])
    .sort((a, b) => (b.adj_confidence || b.bullish_score || b.confidence || 0) -
                    (a.adj_confidence || a.bullish_score || a.confidence || 0))
    .slice(0, 10);

const bearish = (data.bearish || [])
    .sort((a, b) => (b.bearish_score || 0) - (a.bearish_score || 0))
    .slice(0, 10);
```

---

## Sub-Components

### MacroCard
```javascript
function MacroCard({ label, value, change, positive })
```
Renders a single macro indicator card with:
- **Label**: Uppercase label (e.g., "NIFTY 500")
- **Value**: Large bold number (e.g., "22,500")
- **Change**: Secondary text (e.g., "+3.2% vs 200 DMA")
- **Color**: Green if `positive=true`, red if false

### BullishCard
```javascript
function BullishCard({ stock, rank, regimeScalar, regimeName })
```
Renders a bullish signal card with:
- **Header**: Rank (#1-#10), symbol, sector tag
- **Metrics**: CMP, Today%, 3M Ret%, 1W Ret%, Target, S/L, ATR14, Delivery%, CCR, D/E
- **Regime Warning**: Shows "60% exp. (DIP)" if scalar < 1.0
- **Confidence Score**: 0-100, large green number

### BearishCard
```javascript
function BearishCard({ stock, rank })
```
Renders a bearish signal card with:
- **Header**: Rank, symbol, sector tag
- **Metrics**: CMP, Today%, 3M Ret%, 1W Ret%, M-Score, CCR, RS, LVGI (with arrow if rising)
- **Bearish Score**: 0-100, large red number

### NoDataView
```javascript
function NoDataView({ message })
```
Error state: centered card with "No signal data available yet" + error message + info about pipeline schedule.

### fmt() Utility
```javascript
function fmt(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
```
Formats numbers with Indian locale (lakhs/crores separators). Returns '—' for null/NaN.

---

## Styling — `app/globals.css`

### Color Theme (CSS Variables)
```css
:root {
    --bg-primary:    #0a0e17;     /* Dark navy background */
    --bg-secondary:  #111827;     /* Card container background */
    --bg-card:       #1a2332;     /* Individual card background */
    --bg-card-hover: #1f2b3d;     /* Card hover state */
    --border:        #2a3548;     /* Border color */
    --text-primary:  #e5e7eb;     /* Main text (light gray) */
    --text-secondary:#9ca3af;     /* Secondary text */
    --text-muted:    #8a919c;     /* Muted text */
    --accent-green:  #10b981;     /* Bullish / positive */
    --accent-red:    #ef4444;     /* Bearish / negative */
    --accent-yellow: #f59e0b;     /* DIP regime / warnings */
    --accent-blue:   #3b82f6;     /* Links / focus */
    --accent-orange: #f97316;     /* SIDEWAYS regime */
}
```

### Regime Color Coding
| Regime | CSS Class | Background | Border | Text |
|--------|-----------|------------|--------|------|
| BULL | `.regime-banner.bull` | rgba(green, 0.15) | Green | Green |
| DIP | `.regime-banner.dip` | rgba(yellow, 0.15) | Yellow | Yellow |
| SIDEWAYS | `.regime-banner.sideways` | rgba(orange, 0.15) | Orange | Orange |
| BEAR | `.regime-banner.bear` | rgba(red, 0.15) | Red | Red |

### Layout
- **Max width**: 1400px, centered
- **Container padding**: 12px 16px
- **Card border-radius**: 8px
- **Macro grid**: 5 equal columns (CSS Grid)
- **Signal cards**: 2-column grid (content + confidence score)

### Typography
| Element | Font | Size | Weight |
|---------|------|------|--------|
| Header h1 | Montserrat | 28px | 700 |
| Regime banner | System | 15px | 600 |
| Section titles | System | 18px | 700 |
| Card symbol | System | 18px | 700 |
| Macro value | System | 22px | 700 |
| Confidence score | System | 28px | 700 |
| Metric labels | System | 11px | 400 |
| Body text | System stack | 14px | 400 |

### Responsive Breakpoints

| Breakpoint | Target | Key Changes |
|-----------|--------|-------------|
| Default (> 768px) | Desktop | 5-column macro grid, full layout |
| 768px | Tablet | 3-column macro grid, reduced fonts |
| 480px | Mobile | 2-column macro grid, compact cards |
| 360px | Small mobile | Column regime banner, minimal spacing |

### Accessibility
- `focus-visible` outlines (2px solid blue, 2px offset)
- `prefers-reduced-motion`: disables card hover transitions
- Semantic HTML structure

---

## Signals.json → Dashboard Mapping

| JSON Field | Dashboard Location | Display |
|-----------|-------------------|---------|
| `regime.name` | Regime Banner | Color-coded label + exposure % |
| `macro.nifty_close` | Macro Card 1 | Formatted with Indian locale |
| `macro.nifty_dma_pct` | Macro Card 1 (change) | "+X.X% vs 200 DMA" |
| `macro.india_vix` | Macro Card 2 | "Elevated" if > 20, "Normal" if < 20 |
| `macro.usdinr` | Macro Card 3 | 2 decimal places |
| `macro.usdinr_30d_move` | Macro Card 3 (change) | "30d: +X.XX%" |
| `macro.fii_net` | Macro Card 4 | "{value} Cr", green/red |
| `macro.dii_net` | Macro Card 5 | "{value} Cr", green/red |
| `pipeline_stats.*` | Stats Bar | "Universe: X → Stage 1A: Y → ..." |
| `bullish[].symbol` | BullishCard header | Bold, 18px |
| `bullish[].adj_confidence` | BullishCard score | Large green number |
| `bullish[].close` | BullishCard CMP | Indian locale formatted |
| `bullish[].target_high` | BullishCard Target | Price level |
| `bullish[].stop_loss` | BullishCard S/L | Price level |
| `bearish[].bearish_score` | BearishCard score | Large red number |
| `bearish[].m_score` | BearishCard metric | Red text |
| `bearish[].lvgi_rising` | BearishCard LVGI | Arrow indicator ↑ |

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `NEXT_PUBLIC_CM_HYPERLINK` | Footer "Made by CM" link | LinkedIn profile URL |

---

## Key Behaviors

| Feature | Behavior |
|---------|----------|
| Data loading | Client-side fetch on mount, no SSR |
| Error handling | Shows NoDataView with error message |
| Empty state | Shows "No candidates in current regime" card |
| Sorting | Bullish by adj_confidence desc, Bearish by bearish_score desc |
| Max display | Top 10 per section (MAX_DISPLAY = 10) |
| Number formatting | Indian locale (`en-IN`), '—' for null |
| Regime warning | Shows exposure % on each card if scalar < 1.0 |
| Static export | No server-side code, pure CDN delivery |
| Update trigger | Git push to main → Vercel auto-rebuild |
