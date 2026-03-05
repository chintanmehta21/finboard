'use client';

import { useState, useEffect } from 'react';

const REGIME_CONFIG = {
  BULL:     { css: 'bull',     label: 'STRUCTURAL BULL',    icon: '\u{1F7E2}' },
  DIP:      { css: 'dip',      label: 'RISK-ON DIP',        icon: '\u{1F7E1}' },
  SIDEWAYS: { css: 'sideways', label: 'VOLATILE SIDEWAYS',  icon: '\u{1F7E0}' },
  BEAR:     { css: 'bear',     label: 'BEAR / FII FLIGHT',  icon: '\u{1F534}' },
};

const MAX_DISPLAY = 10;
const CM_HYPERLINK = 'https://www.linkedin.com/in/mr-chintanmehta/';

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
  if (!data) return <div className="loading">Loading signals...</div>;

  const regime = data.regime || {};
  const regimeKey = regime.name || 'SIDEWAYS';
  const rc = REGIME_CONFIG[regimeKey] || REGIME_CONFIG.SIDEWAYS;
  const macro = data.macro || {};
  const stats = data.pipeline_stats || {};

  const bullish = (data.bullish || [])
    .sort((a, b) => (b.adj_confidence || b.defensive_score || b.confidence || 0) - (a.adj_confidence || a.defensive_score || a.confidence || 0))
    .slice(0, MAX_DISPLAY);

  const bearish = (data.bearish || [])
    .sort((a, b) => (b.bearish_score || 0) - (a.bearish_score || 0))
    .slice(0, MAX_DISPLAY);

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="header">
        <h1>FinBoard</h1>
        <div className="date">Last Updated: {data.display_date || data.date}</div>
      </header>

      {/* Regime Banner */}
      <div className={`regime-banner ${rc.css}`}>
        <span>{rc.icon}</span>
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
        <MacroCard label="USD/INR 30d" value={`${macro.usdinr_30d_move > 0 ? '+' : ''}${macro.usdinr_30d_move || 0}%`} change={Math.abs(macro.usdinr_30d_move || 0) > 2 ? 'FII Flight Risk' : 'Stable'} positive={Math.abs(macro.usdinr_30d_move || 0) < 2} />
        <MacroCard label="FII Net" value={`${fmt(macro.fii_net)} Cr`} change="" positive={macro.fii_net > 0} />
        <MacroCard label="DII Net" value={`${fmt(macro.dii_net)} Cr`} change="" positive={macro.dii_net > 0} />
      </div>

      {/* Bullish Candidates */}
      <div className="section">
        <h2 className="section-title bullish">
          {'\u{1F4C8}'} Bullish Candidates
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
          {'\u{1F4C9}'} Bearish Candidates
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
        FinBoard v2.0 | NOT financial advice<br />
        Data updated daily at 9:00 AM IST (Mon-Fri)<br />
        Made by <a href={CM_HYPERLINK} target="_blank" rel="noopener noreferrer" className="cm-link">CM</a>
      </footer>
    </div>
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
  const confidence = Math.min(100, stock.adj_confidence || stock.defensive_score || stock.confidence || 0);
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
          {stock.target_high && <div><div className="metric-label">Target</div>{'\u20B9'}{fmt(stock.target_high)}</div>}
          {stock.stop_loss && <div><div className="metric-label">Stop</div>{'\u20B9'}{fmt(stock.stop_loss)}</div>}
          {stock.atr14 && <div><div className="metric-label">ATR14</div>{'\u20B9'}{fmt(stock.atr14)}</div>}
          {stock.rs_slope != null && <div><div className="metric-label">RS Slope</div>{stock.rs_slope > 0 ? '+' : ''}{stock.rs_slope || 0}%</div>}
          {stock.deliv_pct > 0 && <div><div className="metric-label">Delivery%</div>{(stock.deliv_pct || 0).toFixed(1)}%</div>}
          {stock.ccr != null && !stock.target_high && <div><div className="metric-label">CCR</div>{(stock.ccr || 0).toFixed(2)}</div>}
          {stock.debt_equity != null && !stock.target_high && <div><div className="metric-label">D/E</div>{(stock.debt_equity || 0).toFixed(2)}</div>}
          {stock.return_3m != null && !stock.target_high && <div><div className="metric-label">3M Ret</div><span className={stock.return_3m > 0 ? 'positive' : 'negative'}>{(stock.return_3m || 0).toFixed(1)}%</span></div>}
        </div>
        {regimeScalar != null && regimeScalar < 1.0 && (
          <div className="regime-warning">
            {'\u26A0\uFE0F'} Size at {Math.round(regimeScalar * 100)}% of normal ({regimeName})
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
    <div className="dashboard">
      <header className="header">
        <h1>FinBoard</h1>
      </header>
      <div style={{ textAlign: 'center', padding: 64, color: 'var(--text-secondary)' }}>
        <p style={{ fontSize: 18, marginBottom: 12 }}>No signal data available yet</p>
        <p style={{ fontSize: 13 }}>{message}</p>
        <p style={{ fontSize: 13, marginTop: 8 }}>The pipeline runs daily at 9:00 AM IST (Mon-Fri).</p>
      </div>
    </div>
  );
}

function fmt(n) {
  if (n == null || isNaN(n)) return '\u2014';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
