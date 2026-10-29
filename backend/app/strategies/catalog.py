"""Strategy catalogue (metadata only).

Signal logic for each strategy is implemented in Phase 4. Templates provide
starting configurations for the Bot Builder.
"""

from __future__ import annotations

from typing import Any

STRATEGIES: dict[str, dict[str, Any]] = {
    "momentum": {
        "name": "Momentum",
        "version": "0.1.0",
        "summary": "Trades in the direction of a strong, established trend.",
        "timeframes": ["5m", "15m"],
        "regimes": ["TRENDING"],
        "research_only": False,
        "default_entry": [
            {"indicator": "EMA_CROSS", "params": {"fast": 9, "slow": 21}, "operator": "CROSS_ABOVE"},
            {"indicator": "RSI", "params": {"period": 14}, "operator": "GT", "value": 55},
        ],
    },
    "scalping": {
        "name": "Scalping",
        "version": "0.1.0",
        "summary": "Quick trades on small moves. Most sensitive to costs and slippage.",
        "timeframes": ["1m", "3m"],
        "regimes": ["TRENDING", "VOLATILE"],
        "research_only": False,
        "default_entry": [
            {"indicator": "VWAP", "params": {}, "operator": "PRICE_ABOVE"},
            {"indicator": "EMA_CROSS", "params": {"fast": 5, "slow": 13}, "operator": "CROSS_ABOVE"},
        ],
    },
    "breakout": {
        "name": "Breakout",
        "version": "0.1.0",
        "summary": "Enters when price closes beyond a confirmed level with volume.",
        "timeframes": ["5m", "15m"],
        "regimes": ["RANGE_EXPANSION", "TRENDING"],
        "research_only": False,
        "default_entry": [
            {"indicator": "RESISTANCE_BREAK", "params": {"lookback": 20}, "operator": "CLOSE_ABOVE"},
            {"indicator": "RELATIVE_VOLUME", "params": {"period": 20}, "operator": "GT", "value": 1.5},
        ],
    },
    "reversal": {
        "name": "Reversal",
        "version": "0.1.0",
        "summary": "Looks for exhaustion at confirmed support or resistance.",
        "timeframes": ["5m", "15m"],
        "regimes": ["RANGE"],
        "research_only": False,
        "default_entry": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "LT", "value": 30},
            {"indicator": "PATTERN", "params": {"name": "BULLISH_ENGULFING"}, "operator": "PRESENT"},
        ],
    },
    "smc": {
        "name": "Smart Money Concepts",
        "version": "0.1.0",
        "summary": "Uses BOS / CHOCH, order blocks and fair value gaps.",
        "timeframes": ["5m", "15m"],
        "regimes": ["TRENDING", "RANGE"],
        "research_only": False,
        "default_entry": [
            {"indicator": "SMC_CHOCH", "params": {}, "operator": "PRESENT"},
            {"indicator": "SMC_ORDER_BLOCK_RETEST", "params": {}, "operator": "PRESENT"},
        ],
    },
    "ai_adaptive": {
        "name": "AI Adaptive",
        "version": "0.1.0",
        "summary": "Routes between strategies by market regime. Needs a calibrated model.",
        "timeframes": ["5m", "15m"],
        "regimes": ["ANY"],
        "research_only": False,
        "default_entry": [
            {"indicator": "MODEL_PROBABILITY", "params": {"horizon": "30m"}, "operator": "GT",
             "value": 0.7},
        ],
    },
    "custom": {
        "name": "Custom Manual",
        "version": "0.1.0",
        "summary": "Your own entry and exit conditions.",
        "timeframes": ["1m", "3m", "5m", "15m", "30m", "1h"],
        "regimes": ["ANY"],
        "research_only": False,
        "default_entry": [],
    },
    "dca_grid": {
        "name": "DCA / Grid (research only)",
        "version": "0.1.0",
        "summary": "Research only. It cannot be activated until bounded-loss behaviour is "
                   "demonstrated. Martingale and unbounded averaging are never allowed.",
        "timeframes": ["15m", "1h"],
        "regimes": ["RANGE"],
        "research_only": True,
        "default_entry": [],
    },
}

TEMPLATE_ORDER = ["momentum", "scalping", "breakout", "reversal", "smc", "ai_adaptive",
                  "custom", "dca_grid"]
