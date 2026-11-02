"""Walk-forward backtesting of the Phase 2 baselines on real historical games.

Every historical prediction is built from the same pure functions the live
``predict()`` service uses (features, models, variance, distribution families), fed
only with games strictly before the target game.
"""

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.backtest.engine import BacktestResult, run_backtest

__all__ = ["BacktestConfig", "BacktestResult", "run_backtest"]
