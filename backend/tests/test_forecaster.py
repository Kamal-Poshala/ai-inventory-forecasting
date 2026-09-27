import numpy as np
import pandas as pd
import pytest

from app.ml import synthetic
from app.ml.forecaster import MIN_HISTORY, Forecaster, backtest, build_features, calibrate_interval_scale


@pytest.fixture(scope="module")
def panel():
    data = synthetic.generate(days=400, seed=7)
    wide = data.sales.pivot(index="sku", columns="date", values="qty").sort_index()
    cats = pd.Categorical(data.items.set_index("sku").loc[wide.index, "category"]).codes
    return wide.to_numpy(float), pd.DatetimeIndex(pd.to_datetime(wide.columns)), np.arange(len(wide)), np.asarray(cats)


def test_features_only_use_past_values(panel):
    Y, dates, sku, cat = panel
    pos = np.array([100])
    X1 = build_features(Y, pos, dates[pos], sku, cat)
    Y2 = Y.copy()
    Y2[:, 100:] = 9999  # tamper with the target and the future
    X2 = build_features(Y2, pos, dates[pos], sku, cat)
    np.testing.assert_array_equal(X1, X2)


def test_predict_shapes_and_quantile_order(panel):
    Y, dates, sku, cat = panel
    model = Forecaster(max_iter=50).fit(Y, dates, sku, cat)
    fc = model.predict(Y, dates[-1], 14, sku, cat)
    for q in ("p10", "p50", "p90"):
        assert fc[q].shape == (Y.shape[0], 14)
        assert (fc[q] >= 0).all()
    assert (fc["p10"] <= fc["p50"]).all() and (fc["p50"] <= fc["p90"]).all()


def test_backtest_beats_moving_average(panel):
    Y, dates, sku, cat = panel
    m = backtest(Y, dates, sku, cat, horizon=28, max_iter=100)
    assert m["model"]["wape"] < m["baselines"]["moving_average_28d_wape"]
    assert m["interval_scale"] >= 1.0


def test_fit_rejects_short_history(panel):
    Y, dates, sku, cat = panel
    with pytest.raises(ValueError):
        Forecaster().fit(Y[:, :MIN_HISTORY], dates[:MIN_HISTORY], sku, cat)


def test_calibration_reaches_target_coverage():
    rng = np.random.default_rng(0)
    actual = rng.normal(10, 3, size=(5, 100))
    narrow = {"p10": np.full_like(actual, 9.0), "p50": np.full_like(actual, 10.0), "p90": np.full_like(actual, 11.0)}
    scale = calibrate_interval_scale(actual, narrow, target=0.8)
    assert 3.0 < scale < 4.5  # ~1.28 sigma needed, band half-width is 1 -> scale ~3.8
