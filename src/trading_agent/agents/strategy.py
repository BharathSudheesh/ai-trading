"""Strategy Agent: checks a candidate trade against the user's trading style."""

from __future__ import annotations

from collections.abc import Mapping

from ..schemas import Action, Check, StrategyResult, TradingStyle

STYLE_LABEL = {
    TradingStyle.momentum: "Momentum",
    TradingStyle.swing: "Swing",
    TradingStyle.mean_reversion: "Mean reversion",
    TradingStyle.long_term: "Long-term",
}


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


class StrategyAgent:
    """Each style is a small, transparent rule set over technical indicators.

    ``require_all`` rules need every check to pass; the others need at least one.
    """

    def evaluate(
        self, style: TradingStyle, action: Action, f: Mapping[str, float]
    ) -> StrategyResult:
        checks, require_all = self._rules(style, action, f)
        passed = [c.passed for c in checks]
        matched = all(passed) if require_all else any(passed)
        return StrategyResult(
            matched=matched,
            score=sum(passed) / len(passed),
            rule=f"{STYLE_LABEL[style]} · {action.value.lower()}",
            checks=checks,
        )

    @staticmethod
    def _rules(style: TradingStyle, action: Action, f: Mapping[str, float]):
        buy = action == Action.BUY
        rsi, pctb = f["rsi_14"], f["bb_pctb"]

        if style == TradingStyle.momentum:
            if buy:
                return [
                    Check(name="Price above 20-day average", passed=f["sma_20_ratio"] > 0,
                          detail=f"Price is {_pct(f['sma_20_ratio'])} vs its 20-day average"),
                    Check(name="20-day average above 50-day", passed=f["sma_20_50"] > 0,
                          detail=f"20-day average is {_pct(f['sma_20_50'])} vs the 50-day"),
                    Check(name="Positive 20-day momentum", passed=f["ret_20"] > 0,
                          detail=f"20-day return is {_pct(f['ret_20'])}"),
                ], True
            return [
                Check(name="Price below 20-day average", passed=f["sma_20_ratio"] < 0,
                      detail=f"Price is {_pct(f['sma_20_ratio'])} vs its 20-day average"),
                Check(name="Negative 20-day momentum", passed=f["ret_20"] < 0,
                      detail=f"20-day return is {_pct(f['ret_20'])}"),
            ], True

        if style == TradingStyle.swing:
            if buy:
                return [
                    Check(name="Uptrend intact", passed=f["sma_50_ratio"] > 0,
                          detail=f"Price is {_pct(f['sma_50_ratio'])} vs its 50-day average"),
                    Check(name="Pullback, not overheated", passed=30 <= rsi <= 55,
                          detail=f"RSI is {rsi:.0f} (looking for 30 to 55)"),
                ], True
            return [
                Check(name="RSI stretched", passed=rsi >= 68, detail=f"RSI is {rsi:.0f} (68 or more)"),
                Check(name="Near upper Bollinger band", passed=pctb >= 0.95,
                      detail=f"Band position is {pctb:.2f} (0.95 or more)"),
            ], False

        if style == TradingStyle.mean_reversion:
            if buy:
                return [
                    Check(name="Oversold RSI", passed=rsi <= 35, detail=f"RSI is {rsi:.0f} (35 or less)"),
                    Check(name="Near lower Bollinger band", passed=pctb <= 0.15,
                          detail=f"Band position is {pctb:.2f} (0.15 or less)"),
                ], False
            return [
                Check(name="Overbought RSI", passed=rsi >= 65, detail=f"RSI is {rsi:.0f} (65 or more)"),
                Check(name="Near upper Bollinger band", passed=pctb >= 0.85,
                      detail=f"Band position is {pctb:.2f} (0.85 or more)"),
            ], False

        # long term
        if buy:
            return [
                Check(name="Not far below 50-day average", passed=f["sma_50_ratio"] > -0.03,
                      detail=f"Price is {_pct(f['sma_50_ratio'])} vs its 50-day average"),
                Check(name="No deep drawdown", passed=f["drawdown_60"] > -0.25,
                      detail=f"Price is {_pct(f['drawdown_60'])} from its 60-day high"),
            ], True
        return [
            Check(name="Well below 50-day average", passed=f["sma_50_ratio"] < -0.08,
                  detail=f"Price is {_pct(f['sma_50_ratio'])} vs its 50-day average"),
            Check(name="Sustained decline", passed=f["ret_60"] < -0.10,
                  detail=f"60-day return is {_pct(f['ret_60'])}"),
        ], True
