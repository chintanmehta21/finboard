'use client';

import { useState, useEffect } from 'react';

const REGIME_CONFIG = {
  BULL:     { css: 'bull',     label: 'STRUCTURAL BULL' },
  DIP:      { css: 'dip',      label: 'RISK-ON DIP' },
  SIDEWAYS: { css: 'sideways', label: 'VOLATILE SIDEWAYS' },
  BEAR:     { css: 'bear',     label: 'BEAR / FII FLIGHT' },
};

const MAX_DISPLAY = 10;
const CM_HYPERLINK = process.env.NEXT_PUBLIC_CM_HYPERLINK || 'https://www.linkedin.com/in/mr-chintanmehta/';

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/data/signals.json')
      .then(res => {
        if (!res.ok) throw new Error('Signal data not yet available');
        return res.json();
      })
      .then(setData)
      .catch(e => setError(e.message));
  }, []);

  if (error) return <NoDataView message={error} />;
  if (!data) return <div className="loading" role="status" aria-live="polite">Loading signals...</div>;

  const regime = data.regime || {};
  const regimeKey = regime.name || 'SIDEWAYS';
  const rc = REGIME_CONFIG[regimeKey] || REGIME_CONFIG.SIDEWAYS;
  const macro = data.macro || {};
  const stats = data.pipeline_stats || {};

  const bullish = (data.bullish || [])
    .sort((a, b) => (b.adj_confidence || b.bullish_score || b.confidence || 0) - (a.adj_confidence || a.bullish_score || a.confidence || 0))
    .slice(0, MAX_DISPLAY);

  const bearish = (data.bearish || [])
    .sort((a, b) => (b.bearish_score || 0) - (a.bearish_score || 0))
    .slice(0, MAX_DISPLAY);

  return (
    <main className="dashboard">
      {/* Header */}
      <header className="header">
        <h1>Finboard</h1>
        <div className="date">Last Updated: {data.display_date || data.date}</div>
      </header>

      {/* Regime Banner — single row, no icon */}
      <div className={`regime-banner ${rc.css}`}>
        <span>Regime: {rc.label}</span>
        <span className="regime-badge">{regime.exposure_pct || 0}% Exposure</span>
      </div>

      {/* Pipeline Stats */}
      <div className="stats-bar">
        <div className="stat-item">Universe: <span>{stats.total_universe || 0}</span></div>
        <div className="stat-item">Stage 1A: <span>{stats.stage_1a_pass || 0}</span></div>
        <div className="stat-item">Stage 1B: <span>{stats.stage_1b_pass || 0}</span></div>
        <div className="stat-item">Scored: <span>{stats.stage_2_scored || 0}</span></div>
      </div>

      {/* Macro Snapshot */}
      <div className="macro-grid">
        <MacroCard label="Nifty 500" value={fmt(macro.nifty_close)} change={`${macro.nifty_dma_pct > 0 ? '+' : ''}${macro.nifty_dma_pct || 0}% vs 200 DMA`} positive={macro.nifty_dma_pct > 0} />
        <MacroCard label="India VIX" value={macro.india_vix || '\u2014'} change={macro.india_vix > 20 ? 'Elevated' : 'Normal'} positive={macro.india_vix < 20} />
        <MacroCard label="USD/INR" value={(macro.usdinr || 0).toFixed(2)} change={`30d: ${macro.usdinr_30d_move > 0 ? '+' : ''}${(macro.usdinr_30d_move || 0).toFixed(2)}%`} positive={Math.abs(macro.usdinr_30d_move || 0) < 2} />
        <MacroCard label="FII Net" value={`${fmt(macro.fii_net)} Cr`} change="" positive={macro.fii_net > 0} />
        <MacroCard label="DII Net" value={`${fmt(macro.dii_net)} Cr`} change="" positive={macro.dii_net > 0} />
      </div>

      {/* Bullish Candidates — always called Bullish regardless of regime */}
      <div className="section">
        <h2 className="section-title bullish">
          Bullish Candidates
          <span className="section-count">{bullish.length}</span>
        </h2>
        <div className="signal-grid">
          {bullish.length > 0 ? (
            bullish.map((stock, i) => (
              <BullishCard key={stock.symbol} stock={stock} rank={i + 1} regimeScalar={regime.scalar} regimeName={regimeKey} />
            ))
          ) : (
            <div className="signal-card empty-card"><div>No bullish candidates passed all stages today.</div></div>
          )}
        </div>
      </div>

      {/* Bearish Candidates */}
      <div className="section">
        <h2 className="section-title bearish">
          Bearish Candidates
          <span className="section-count">{bearish.length}</span>
        </h2>
        <div className="signal-grid">
          {bearish.length > 0 ? (
            bearish.map((stock, i) => (
              <BearishCard key={stock.symbol} stock={stock} rank={i + 1} />
            ))
          ) : (
            <div className="signal-card empty-card"><div>No bearish candidates identified today.</div></div>
          )}
        </div>
      </div>

      {/* Footer */}
      <footer className="footer">
        Finboard v2.0 | NOT financial advice<br />
        Data updated daily before market opens (Mon-Fri)<br />
        Made by <a href={CM_HYPERLINK} target="_blank" rel="noopener noreferrer" className="cm-link">CM</a>
      </footer>
    </main>
  );
}

function MacroCard({ label, value, change, positive }) {
  return (
    <div className="macro-card">
      <div className="label">{label}</div>
      <div className={`value ${positive ? 'positive' : 'negative'}`}>{value}</div>
      {change && <div className={`change ${positive ? 'positive' : 'negative'}`}>{change}</div>}
    </div>
  );
}

function BullishCard({ stock, rank, regimeScalar, regimeName }) {
  const confidence = Math.min(100, stock.adj_confidence || stock.bullish_score || stock.confidence || 0);
  return (
    <div className="signal-card">
      <div className="card-content">
        <div className="card-header">
          <div className="rank">#{rank}</div>
          <div className="symbol">{stock.symbol}</div>
          {stock.sector && <div className="sector-tag">{stock.sector}</div>}
        </div>
        <div className="metrics">
          <div><div className="metric-label">CMP</div>{'\u20B9'}{fmt(stock.close)}</div>
          {stock.return_1d != null && <div><div className="metric-label">Today</div><span className={stock.return_1d > 0 ? 'positive' : 'negative'}>{stock.return_1d > 0 ? '+' : ''}{(stock.return_1d || 0).toFixed(1)}%</span></div>}
          {stock.return_3m != null && <div><div className="metric-label">3M Ret</div><span className={stock.return_3m > 0 ? 'positive' : 'negative'}>{(stock.return_3m || 0).toFixed(1)}%</span></div>}
          {stock.return_1w != null && <div><div className="metric-label">1W Ret</div><span className={stock.return_1w > 0 ? 'positive' : 'negative'}>{(stock.return_1w || 0).toFixed(1)}%</span></div>}
          {stock.target_high ? <div><div className="metric-label">Target</div>{'\u20B9'}{fmt(stock.target_high)}</div> : null}
          {stock.stop_loss ? <div><div className="metric-label">S/L</div>{'\u20B9'}{fmt(stock.stop_loss)}</div> : null}
          {stock.atr14 ? <div><div className="metric-label">ATR14</div>{'\u20B9'}{fmt(stock.atr14)}</div> : null}
          {stock.deliv_pct > 0 && <div><div className="metric-label">Delivery%</div>{(stock.deliv_pct || 0).toFixed(1)}%</div>}
          {stock.ccr != null && <div><div className="metric-label">CCR</div>{(stock.ccr || 0).toFixed(2)}</div>}
          {stock.debt_equity != null && <div><div className="metric-label">D/E</div>{(stock.debt_equity || 0).toFixed(2)}</div>}
        </div>
        {regimeScalar != null && regimeScalar > 0 && regimeScalar < 1.0 && (
          <div className="regime-warning">
            Regime: {Math.round(regimeScalar * 100)}% exp. ({regimeName})
          </div>
        )}
      </div>
      <div className="confidence">
        <div className="confidence-score positive">{confidence.toFixed(0)}</div>
        <div className="confidence-label">/100</div>
      </div>
    </div>
  );
}

function BearishCard({ stock, rank }) {
  return (
    <div className="signal-card">
      <div className="card-content">
        <div className="card-header">
          <div className="rank">#{rank}</div>
          <div className="symbol">{stock.symbol}</div>
          {stock.sector && <div className="sector-tag">{stock.sector}</div>}
        </div>
        <div className="metrics">
          <div><div className="metric-label">CMP</div>{'\u20B9'}{fmt(stock.close)}</div>
          {stock.return_1d != null && <div><div className="metric-label">Today</div><span className={stock.return_1d > 0 ? 'positive' : 'negative'}>{stock.return_1d > 0 ? '+' : ''}{(stock.return_1d || 0).toFixed(1)}%</span></div>}
          {stock.return_3m != null && <div><div className="metric-label">3M Ret</div><span className={stock.return_3m > 0 ? 'positive' : 'negative'}>{(stock.return_3m || 0).toFixed(1)}%</span></div>}
          {stock.return_1w != null && <div><div className="metric-label">1W Ret</div><span className={stock.return_1w > 0 ? 'positive' : 'negative'}>{(stock.return_1w || 0).toFixed(1)}%</span></div>}
          <div><div className="metric-label">M-Score</div><span className="negative">{stock.m_score}</span></div>
          <div><div className="metric-label">CCR</div>{stock.ccr}</div>
          <div><div className="metric-label">RS</div>{stock.mansfield_rs}</div>
          <div><div className="metric-label">LVGI</div>{stock.lvgi}{stock.lvgi_rising ? ' \u2191' : ''}</div>
        </div>
      </div>
      <div className="confidence">
        <div className="confidence-score negative">{(stock.bearish_score || 0).toFixed(0)}</div>
        <div className="confidence-label">/100</div>
      </div>
    </div>
  );
}

function NoDataView({ message }) {
  return (
    <main className="dashboard">
      <header className="header">
        <h1>Finboard</h1>
      </header>
      <div style={{ textAlign: 'center', padding: 64, color: 'var(--text-secondary)' }}>
        <p style={{ fontSize: 18, marginBottom: 12 }}>No signal data available yet</p>
        <p style={{ fontSize: 13 }}>{message}</p>
        <p style={{ fontSize: 13, marginTop: 8 }}>The pipeline runs daily before market opens (Mon-Fri).</p>
      </div>
    </main>
  );
}

function fmt(n) {
  if (n == null || isNaN(n)) return '\u2014';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
