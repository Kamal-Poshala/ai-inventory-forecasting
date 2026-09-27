"""Data loading, seeding and the train -> backtest -> forecast pipeline."""

import logging
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import Forecast, Item, ModelRun, Sale
from ..ml import synthetic
from ..ml.forecaster import Forecaster, backtest, scale_interval
from . import alerts, inventory

log = logging.getLogger(__name__)
_retrain_lock = threading.Lock()


class RetrainInProgress(RuntimeError):
    pass


@dataclass
class DemandPanel:
    """Dense daily demand matrix aligned with item metadata."""

    Y: np.ndarray  # (n_sku, n_days)
    dates: pd.DatetimeIndex
    skus: list[str]
    sku_codes: np.ndarray
    cat_codes: np.ndarray


def seed_if_empty(session: Session) -> bool:
    if session.scalar(select(func.count()).select_from(Item)):
        return False
    data = synthetic.generate()
    session.bulk_insert_mappings(Item, data.items.to_dict("records"))
    session.bulk_insert_mappings(Sale, data.sales.to_dict("records"))
    session.commit()
    log.info("Seeded %d items and %d sales rows", len(data.items), len(data.sales))
    return True


def load_panel(session: Session) -> DemandPanel:
    items = pd.read_sql(select(Item.sku, Item.category).order_by(Item.sku), session.connection())
    sales = pd.read_sql(select(Sale.sku, Sale.date, Sale.qty), session.connection())
    if sales.empty:
        raise ValueError("No sales data loaded")
    sales["date"] = pd.to_datetime(sales["date"])
    dates = pd.date_range(sales["date"].min(), sales["date"].max(), freq="D")
    wide = (
        sales.pivot_table(index="sku", columns="date", values="qty", aggfunc="sum")
        .reindex(index=items["sku"], columns=dates)
        .fillna(0.0)
    )
    return DemandPanel(
        Y=wide.to_numpy(dtype=np.float64),
        dates=dates,
        skus=items["sku"].tolist(),
        sku_codes=np.arange(len(items)),
        cat_codes=pd.Categorical(items["category"]).codes.astype(np.int64),
    )


def train_and_forecast(session: Session) -> ModelRun:
    settings = get_settings()
    horizon = settings.forecast_horizon_days
    panel = load_panel(session)

    metrics = backtest(panel.Y, panel.dates, panel.sku_codes, panel.cat_codes, horizon)
    metrics["per_sku_wape"] = dict(zip(panel.skus, metrics["per_sku_wape"], strict=True))

    model = Forecaster().fit(panel.Y, panel.dates, panel.sku_codes, panel.cat_codes)
    fc = model.predict(panel.Y, panel.dates[-1], horizon, panel.sku_codes, panel.cat_codes)
    fc = scale_interval(fc, metrics["interval_scale"])

    run = ModelRun(metrics=metrics)
    session.add(run)
    session.flush()
    future = pd.date_range(panel.dates[-1] + pd.Timedelta(days=1), periods=horizon, freq="D").date
    rows = [
        {"run_id": run.id, "sku": sku, "date": d, "p10": float(fc["p10"][i, h]), "p50": float(fc["p50"][i, h]), "p90": float(fc["p90"][i, h])}
        for i, sku in enumerate(panel.skus)
        for h, d in enumerate(future)
    ]
    session.execute(delete(Forecast))  # only the latest run's forecasts are kept
    session.bulk_insert_mappings(Forecast, rows)
    session.commit()
    log.info("Model run %d: WAPE %.3f (seasonal naive %.3f)", run.id, metrics["model"]["wape"], metrics["baselines"]["seasonal_naive_wape"])
    return run


def retrain(session: Session) -> ModelRun:
    """Train + forecast, then send stockout alerts. Only one retrain runs at a time."""
    if not _retrain_lock.acquire(blocking=False):
        raise RetrainInProgress("A retrain is already running")
    try:
        run = train_and_forecast(session)
        alerts.send_stockout_alert(inventory.assess_all(session))
        return run
    finally:
        _retrain_lock.release()


def latest_run(session: Session) -> ModelRun | None:
    return session.scalar(select(ModelRun).order_by(ModelRun.id.desc()).limit(1))
