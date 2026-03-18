"""
SystemTest — Validation Functions

Validates pipeline output structure, data integrity, and stage results.
Each validator returns (passed: bool, message: str).
"""

import pandas as pd


def validate_result_structure(result: dict) -> list[tuple[bool, str]]:
    """Validate the top-level pipeline result has all required keys."""
    checks = []

    required_keys = [
        'bullish', 'bearish', 'regime_name', 'regime_scalar',
        'macro_snapshot', 'pipeline_stats', 'factor_weights',
    ]
    for key in required_keys:
        present = key in result
        checks.append((present, f"Result key '{key}' present: {present}"))

    return checks


def validate_regime(result: dict) -> list[tuple[bool, str]]:
    """Validate regime detection output."""
    checks = []

    regime_name = result.get('regime_name')
    valid_regimes = {'BULL', 'DIP', 'SIDEWAYS', 'BEAR'}
    checks.append((
        regime_name in valid_regimes,
        f"Regime '{regime_name}' is valid (one of {valid_regimes})"
    ))

    scalar = result.get('regime_scalar')
    valid_scalars = {0.0, 0.1, 0.3, 0.6, 1.0}  # v0.2: BEAR uses 0.1
    checks.append((
        scalar in valid_scalars,
        f"Regime scalar {scalar} is valid (one of {valid_scalars})"
    ))

    return checks


def validate_macro_snapshot(macro: dict) -> list[tuple[bool, str]]:
    """Validate the macro snapshot has required fields with reasonable values."""
    checks = []

    required_fields = [
        'nifty_close', 'nifty_200dma', 'nifty_dma_pct',
        'india_vix', 'usdinr',
    ]
    for field in required_fields:
        present = field in macro and macro[field] is not None
        checks.append((present, f"Macro field '{field}' present: {present}"))

    # Nifty should be a positive number
    nifty = macro.get('nifty_close', 0)
    checks.append((
        isinstance(nifty, (int, float)) and nifty > 0,
        f"Nifty close is positive: {nifty}"
    ))

    # VIX should be between 5 and 80
    vix = macro.get('india_vix', 0)
    checks.append((
        isinstance(vix, (int, float)) and 5 <= vix <= 80,
        f"India VIX in range [5, 80]: {vix}"
    ))

    # USD/INR should be between 60 and 120
    usdinr = macro.get('usdinr', 0)
    checks.append((
        isinstance(usdinr, (int, float)) and 60 <= usdinr <= 120,
        f"USD/INR in range [60, 120]: {usdinr}"
    ))

    return checks


def validate_pipeline_stats(stats: dict) -> list[tuple[bool, str]]:
    """Validate pipeline filtering funnel statistics."""
    checks = []

    checks.append((
        'total_universe' in stats and stats['total_universe'] > 0,
        f"Universe size: {stats.get('total_universe', 0)}"
    ))

    # Funnel should be monotonically decreasing (v0.2: includes 1C)
    funnel = [
        stats.get('total_universe', 0),
        stats.get('stage_1a_pass', 0),
        stats.get('stage_1b_pass', 0),
        stats.get('stage_1c_pass', 0),
    ]
    monotonic = all(funnel[i] >= funnel[i + 1] for i in range(len(funnel) - 1))
    checks.append((
        monotonic,
        f"Pipeline funnel is monotonically decreasing: {funnel}"
    ))

    checks.append((
        'regime' in stats,
        f"Stats include regime: {stats.get('regime', 'MISSING')}"
    ))

    return checks


def validate_factor_weights(weights: dict, regime_name: str) -> list[tuple[bool, str]]:
    """Validate factor weights are consistent with regime."""
    checks = []

    # v0.21: FQ ('for') removed from factor weights — negative IC confirmed
    if regime_name == 'BEAR':
        expected_keys = {'rs', 'del', 'vam', 'rev'}
        present = expected_keys.issubset(set(weights.keys()))
        checks.append((present, f"BEAR regime: factor weight keys present: {set(weights.keys())}"))
    else:
        expected_keys = {'rs', 'del', 'vam', 'rev'}
        present = expected_keys.issubset(set(weights.keys()))
        checks.append((present, f"Factor weight keys present: {set(weights.keys())}"))

        if weights:
            total = sum(weights.values())
            close_to_one = abs(total - 1.0) < 0.01
            checks.append((close_to_one, f"Factor weights sum to ~1.0: {total:.4f}"))

            all_non_negative = all(v >= 0 for v in weights.values())
            checks.append((all_non_negative, f"All weights non-negative: {weights}"))

    return checks


def validate_bullish_candidates(bullish, regime_name: str) -> list[tuple[bool, str]]:
    """Validate bullish candidate list structure and values."""
    checks = []

    if isinstance(bullish, pd.DataFrame):
        is_df = True
        count = len(bullish)
    elif isinstance(bullish, list):
        is_df = False
        count = len(bullish)
    else:
        checks.append((False, f"Bullish is unexpected type: {type(bullish)}"))
        return checks

    checks.append((True, f"Bullish candidates: {count}"))

    # Max 10 candidates
    checks.append((
        count <= 10,
        f"Bullish count <= 10: {count}"
    ))

    if count > 0 and is_df:
        # Check required columns
        required_cols = ['symbol', 'close']
        for col in required_cols:
            present = col in bullish.columns
            checks.append((present, f"Bullish column '{col}' present: {present}"))

        # All closes should be positive
        if 'close' in bullish.columns:
            all_positive = (bullish['close'] > 0).all()
            checks.append((all_positive, f"All bullish close prices positive: {all_positive}"))

        # Confidence scores
        conf_col = None
        for c in ['adj_confidence', 'bullish_score', 'confidence']:
            if c in bullish.columns:
                conf_col = c
                break
        if conf_col:
            in_range = ((bullish[conf_col] >= 0) & (bullish[conf_col] <= 100)).all()
            checks.append((in_range, f"Confidence scores ({conf_col}) in [0, 100]: {in_range}"))

    return checks


def validate_bearish_candidates(bearish) -> list[tuple[bool, str]]:
    """Validate bearish candidate list structure."""
    checks = []

    if isinstance(bearish, pd.DataFrame):
        count = len(bearish)
    elif isinstance(bearish, list):
        count = len(bearish)
    else:
        checks.append((False, f"Bearish is unexpected type: {type(bearish)}"))
        return checks

    checks.append((True, f"Bearish candidates: {count}"))

    if count > 0 and isinstance(bearish, pd.DataFrame):
        if 'symbol' in bearish.columns:
            checks.append((True, "Bearish has 'symbol' column"))
        if 'close' in bearish.columns:
            all_positive = (bearish['close'] > 0).all()
            checks.append((all_positive, f"All bearish close prices positive: {all_positive}"))

    return checks


def validate_json_export(json_data: dict) -> list[tuple[bool, str]]:
    """Validate the exported JSON structure matches dashboard expectations."""
    checks = []

    required_keys = [
        'generated_at', 'date', 'display_date', 'regime',
        'macro', 'pipeline_stats', 'factor_weights',
        'bullish', 'bearish',
    ]
    for key in required_keys:
        present = key in json_data
        checks.append((present, f"JSON key '{key}' present: {present}"))

    # Regime sub-structure
    regime = json_data.get('regime', {})
    for field in ['name', 'scalar', 'exposure_pct']:
        checks.append((
            field in regime,
            f"JSON regime.{field} present: {field in regime}"
        ))

    # Bullish/bearish should be lists
    checks.append((
        isinstance(json_data.get('bullish'), list),
        f"JSON bullish is list: {isinstance(json_data.get('bullish'), list)}"
    ))
    checks.append((
        isinstance(json_data.get('bearish'), list),
        f"JSON bearish is list: {isinstance(json_data.get('bearish'), list)}"
    ))

    return checks


def validate_data_sources(ohlcv_data: dict, bhavcopy_df, fundamentals: dict,
                          index_data: dict) -> list[tuple[bool, str]]:
    """Validate that data sources loaded correctly."""
    checks = []

    # OHLCV
    checks.append((
        len(ohlcv_data) > 0,
        f"OHLCV symbols loaded: {len(ohlcv_data)}"
    ))

    # Check a sample OHLCV DataFrame
    if ohlcv_data:
        sample_sym = next(iter(ohlcv_data))
        sample_df = ohlcv_data[sample_sym]
        required_cols = {'open', 'high', 'low', 'close', 'volume'}
        has_cols = required_cols.issubset(set(sample_df.columns))
        checks.append((has_cols, f"OHLCV columns present ({sample_sym}): {set(sample_df.columns)}"))
        checks.append((
            len(sample_df) >= 100,
            f"OHLCV has >=100 rows ({sample_sym}): {len(sample_df)}"
        ))

    # Bhavcopy
    if isinstance(bhavcopy_df, pd.DataFrame):
        checks.append((
            not bhavcopy_df.empty,
            f"Bhavcopy loaded: {len(bhavcopy_df)} records"
        ))
    else:
        checks.append((False, "Bhavcopy is not a DataFrame"))

    # Fundamentals
    checks.append((
        len(fundamentals) > 0,
        f"Fundamentals loaded: {len(fundamentals)} symbols"
    ))

    # Index data
    for key in ['nifty_df', 'vix_df', 'usdinr_df']:
        df = index_data.get(key, pd.DataFrame())
        checks.append((
            isinstance(df, pd.DataFrame) and not df.empty,
            f"Index data '{key}' loaded: {len(df) if isinstance(df, pd.DataFrame) else 0} rows"
        ))

    return checks
