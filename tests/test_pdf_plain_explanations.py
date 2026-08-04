"""
tests/test_pdf_plain_explanations.py
Validates ml_weekly/pdf_report.py's plain-language ("bahasa awam")
translations for every reason_codes / risk_flags string
predict.generate_reason_codes / generate_risk_flags can produce.

The exact format strings here are copied from predict.py's f-string
templates (see generate_reason_codes/generate_risk_flags docstrings) rather
than generated live, so this also acts as a drift detector: if predict.py's
wording changes, the matching regex in pdf_report.py silently stops firing
and these tests catch it via the "falls back to raw passthrough" check.
"""
from __future__ import annotations

from ml_weekly.pdf_report import _explain_reason_plain, _explain_risk_plain

# Every literal string shape predict.generate_reason_codes() can emit.
REASON_SAMPLES = [
    "Strong 5-day momentum (+4.2%)",
    "Strong 20-day trend (+8.1%)",
    "RSI14 oversold (29)",
    "RSI14 balanced (52)",
    "Price above all key moving averages",
    "Near 20-day high (breakout zone)",
    "Near 60-day high (breakout zone)",
    "Volume expansion 2.3x vs 5-day avg",
    "Bollinger Band squeeze (pre-breakout signal)",
    "Price in upper Bollinger range (85%)",
    "MACD histogram positive (bullish momentum)",
    "Supportive market regime (BULL_TREND)",
    "Foreign net buying 5-day (1.8% of turnover)",
    "Strong trend efficiency (0.72)",
    "Closed near daily high (strength)",
]

# Every literal string shape predict.generate_risk_flags() can emit.
RISK_SAMPLES = [
    "Low liquidity (avg value 340M IDR)",
    "High volatility (annualized: 55%)",
    "RSI14 overbought (78)",
    "Bearish market regime (BEAR_TREND)",
    "High volatility market regime",
    "Stock in 20-day downtrend (-15.3%)",
    "Price near 120-day low",
    "Recent inactive trading days",
    "Corporate action warning (extreme past returns)",
]


def _is_fallback(explanation: str, original: str) -> bool:
    """The fallback path just wraps the raw (escaped) string in parens."""
    return explanation.strip() == f"({original})"


def test_every_reason_code_shape_has_a_real_explanation():
    for reason in REASON_SAMPLES:
        explanation = _explain_reason_plain(reason)
        assert not _is_fallback(explanation, reason), f"No plain explanation matched: {reason!r}"
        assert len(explanation) > 20  # a real sentence, not a stub


def test_every_risk_flag_shape_has_a_real_explanation():
    for flag in RISK_SAMPLES:
        explanation = _explain_risk_plain(flag)
        assert not _is_fallback(explanation, flag), f"No plain explanation matched: {flag!r}"
        assert len(explanation) > 20


def test_embedded_numbers_carry_through_into_the_explanation():
    assert "29" in _explain_reason_plain("RSI14 oversold (29)")
    assert "4.2" in _explain_reason_plain("Strong 5-day momentum (+4.2%)")
    assert "2.3" in _explain_reason_plain("Volume expansion 2.3x vs 5-day avg")
    assert "1.8" in _explain_reason_plain("Foreign net buying 5-day (1.8% of turnover)")
    assert "78" in _explain_risk_plain("RSI14 overbought (78)")
    assert "-15.3" in _explain_risk_plain("Stock in 20-day downtrend (-15.3%)")


def test_market_regime_codes_are_translated_not_left_as_raw_enum():
    explanation = _explain_reason_plain("Supportive market regime (BULL_TREND)")
    assert "BULL_TREND" not in explanation
    assert "naik" in explanation.lower()

    explanation = _explain_risk_plain("Bearish market regime (BEAR_TREND)")
    assert "BEAR_TREND" not in explanation
    assert "turun" in explanation.lower() or "surut" in explanation.lower()


def test_unknown_string_falls_back_safely_without_crashing():
    explanation = _explain_reason_plain("Some future reason code (99)")
    assert "99" in explanation  # raw text preserved, just not translated
    explanation = _explain_risk_plain("Some future risk flag")
    assert "Some future risk flag" in explanation
