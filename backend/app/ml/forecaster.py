"""Global gradient-boosted demand forecaster.

One model is trained across all SKUs (a "global" model), which lets sparse items
borrow strength from the rest of the catalog. Features are built from a dense
(n_sku x n_days) demand matrix so training and recursive inference share exactly
the same code path, which avoids train/serve skew.

Three models are fit: a Poisson point forecast (p50) and two quantile models
(p10 / p90) that give a prediction interval used for safety-stock sizing.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

LAGS = (1, 2, 3, 4, 5, 6, 7, 14, 21, 28)
WINDOWS = (7, 28)
MIN_HISTORY = max(max(LAGS), max(WINDOWS))
FEATURE_NAMES = (
    [f"lag_{k}" for k in LAGS]
    + [f"roll_mean_{w}" for w in WINDOWS]
    + ["roll_std_28", "dow", "month", "doy_sin", "doy_cos", "sku_code", "cat_code"]
)
CATEGORICAL = [FEATURE_NAMES.index("sku_code"), FEATURE_NAMES.index("cat_code")]


def build_features(
    Y: np.ndarray, positions: np.ndarray, dates: pd.DatetimeIndex, sku_codes: np.ndarray, cat_codes: np.ndarray
) -> np.ndarray:
    """Feature matrix for predicting Y[:, p] for every p in `positions`, using only Y[:, :p].

    `dates` are the calendar dates of the target positions. Rows are ordered SKU-major.
    """
    n_sku, n_pos = Y.shape[0], len(positions)
    csum = np.concatenate([np.zeros((n_sku, 1)), np.cumsum(Y, axis=1)], axis=1)
    csum2 = np.concatenate([np.zeros((n_sku, 1)), np.cumsum(Y**2, axis=1)], axis=1)

    cols = [Y[:, positions - k] for k in LAGS]
    for w in WINDOWS:
        cols.append((csum[:, positions] - csum[:, positions - w]) / w)
    mean28 = cols[-1]
    var28 = (csum2[:, positions] - csum2[:, positions - 28]) / 28 - mean28**2
    cols.append(np.sqrt(np.clip(var28, 0, None)))

    doy = dates.dayofyear.to_numpy()
    for cal in (dates.dayofweek.to_numpy(), dates.month.to_numpy(), np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)):
        cols.append(np.broadcast_to(cal, (n_sku, n_pos)))
    cols.append(np.broadcast_to(sku_codes[:, None], (n_sku, n_pos)))
    cols.append(np.broadcast_to(cat_codes[:, None], (n_sku, n_pos)))

    return np.stack([c.reshape(-1) for c in cols], axis=1).astype(np.float64)


@dataclass
class Forecaster:
    max_iter: int = 300
    learning_rate: float = 0.05
    random_state: int = 0
    models: dict[str, HistGradientBoostingRegressor] = field(default_factory=dict)

    def _make(self, **loss) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            categorical_features=CATEGORICAL,
            random_state=self.random_state,
            **loss,
        )

    def fit(self, Y: np.ndarray, dates: pd.DatetimeIndex, sku_codes: np.ndarray, cat_codes: np.ndarray) -> "Forecaster":
        if Y.shape[1] <= MIN_HISTORY:
            raise ValueError(f"Need more than {MIN_HISTORY} days of history, got {Y.shape[1]}")
        positions = np.arange(MIN_HISTORY, Y.shape[1])
        X = build_features(Y, positions, dates[positions], sku_codes, cat_codes)
        y = Y[:, positions].reshape(-1)
        self.models = {
            "p50": self._make(loss="poisson").fit(X, y),
            "p10": self._make(loss="quantile", quantile=0.1).fit(X, y),
            "p90": self._make(loss="quantile", quantile=0.9).fit(X, y),
        }
        return self

    def predict(
        self, Y: np.ndarray, last_date: pd.Timestamp, horizon: int, sku_codes: np.ndarray, cat_codes: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Recursive multi-step forecast. Returns arrays of shape (n_sku, horizon) for p10/p50/p90."""
        if not self.models:
            raise RuntimeError("Forecaster is not fitted")
        n_sku, T = Y.shape
        work = np.concatenate([Y.astype(np.float64), np.zeros((n_sku, horizon))], axis=1)
        out = {q: np.zeros((n_sku, horizon)) for q in ("p10", "p50", "p90")}
        for h in range(horizon):
            pos = T + h
            target_date = pd.DatetimeIndex([last_date + pd.Timedelta(days=h + 1)])
            X = build_features(work[:, : pos + 1], np.array([pos]), target_date, sku_codes, cat_codes)
            preds = {q: np.clip(m.predict(X), 0, None) for q, m in self.models.items()}
            lo, mid, hi = np.sort(np.stack([preds["p10"], preds["p50"], preds["p90"]]), axis=0)
            out["p10"][:, h], out["p50"][:, h], out["p90"][:, h] = lo, mid, hi
            work[:, pos] = mid  # feed the point forecast back in as history
        return out


def scale_interval(fc: dict[str, np.ndarray], scale: float) -> dict[str, np.ndarray]:
    """Widen (or shrink) the p10/p90 band around p50 by `scale`."""
    return {
        "p10": np.clip(fc["p50"] - scale * (fc["p50"] - fc["p10"]), 0, None),
        "p50": fc["p50"],
        "p90": fc["p50"] + scale * (fc["p90"] - fc["p50"]),
    }


def calibrate_interval_scale(actual: np.ndarray, fc: dict[str, np.ndarray], target: float = 0.8) -> float:
    """Smallest band scale that reaches `target` empirical coverage on held-out data.

    Recursive forecasting under-states uncertainty at longer horizons (errors compound but the
    quantile models only see one-step-ahead errors), so the raw p10-p90 band is usually too narrow.
    """
    for scale in np.arange(1.0, 4.01, 0.05):
        band = scale_interval(fc, scale)
        if ((actual >= band["p10"]) & (actual <= band["p90"])).mean() >= target:
            return round(float(scale), 2)
    return 4.0


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / max(actual.sum(), 1e-9))


def backtest(
    Y: np.ndarray, dates: pd.DatetimeIndex, sku_codes: np.ndarray, cat_codes: np.ndarray, horizon: int, **model_kwargs
) -> dict:
    """Hold out the last `horizon` days, forecast them, and compare against simple baselines."""
    train, test = Y[:, :-horizon], Y[:, -horizon:]
    model = Forecaster(**model_kwargs).fit(train, dates[:-horizon], sku_codes, cat_codes)
    fc = model.predict(train, dates[-horizon - 1], horizon, sku_codes, cat_codes)

    reps = int(np.ceil(horizon / 7))
    seasonal_naive = np.tile(train[:, -7:], reps)[:, :horizon]
    moving_avg = np.repeat(train[:, -28:].mean(axis=1, keepdims=True), horizon, axis=1)

    per_sku_wape = [_wape(test[i], fc["p50"][i]) for i in range(Y.shape[0])]
    scale = calibrate_interval_scale(test, fc)
    return {
        "interval_scale": scale,
        "horizon_days": horizon,
        "holdout_start": str(dates[-horizon].date()),
        "model": {
            "wape": _wape(test, fc["p50"]),
            "mae": float(np.abs(test - fc["p50"]).mean()),
            "bias": float((fc["p50"].sum() - test.sum()) / max(test.sum(), 1e-9)),
            "interval_coverage_p10_p90": float(((test >= fc["p10"]) & (test <= fc["p90"])).mean()),
        },
        "baselines": {
            "seasonal_naive_wape": _wape(test, seasonal_naive),
            "moving_average_28d_wape": _wape(test, moving_avg),
        },
        "per_sku_wape": per_sku_wape,
    }
