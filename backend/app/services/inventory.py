"""Turns demand forecasts into inventory positions and risk flags.

For each SKU:
  * lead-time demand  = sum of p50 forecast over the supplier lead time
  * safety stock      = z * sqrt(sum of daily variance over the lead time), with the daily
                        std derived from the calibrated p10-p90 band (a normal approximation)
  * reorder point     = lead-time demand + safety stock
  * projected stockout = first day cumulative p50 demand exceeds stock on hand
"""

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import Forecast, Item, Sale

Z_P90 = 1.2816  # standard-normal quantile for p90


class RiskStatus(StrEnum):
    STOCKOUT = "stockout_risk"  # will run out before a new order could arrive
    REORDER = "reorder"  # below reorder point, order now to stay covered
    OVERSTOCK = "overstock"  # far more stock than forecast demand needs
    HEALTHY = "healthy"


@dataclass
class ItemRisk:
    sku: str
    name: str
    category: str
    on_hand: int
    unit_cost: float
    lead_time_days: int
    avg_daily_forecast: float
    avg_daily_last_28: float
    trend_pct: float
    days_of_cover: float | None
    projected_stockout_date: date | None
    reorder_point: float
    safety_stock: float
    recommended_order_qty: int
    excess_units: int
    excess_value: float
    status: RiskStatus

    def to_dict(self) -> dict:
        return asdict(self)


def assess_item(item: Item, forecast: pd.DataFrame, last_28_total: float) -> ItemRisk:
    s = get_settings()
    p50 = forecast["p50"].to_numpy()
    sigma = (forecast["p90"].to_numpy() - forecast["p10"].to_numpy()) / (2 * Z_P90)
    horizon = len(p50)
    lt = min(item.lead_time_days, horizon)

    avg_daily = float(p50.mean())
    lead_demand = float(p50[:lt].sum())
    safety = float(s.service_level_z * np.sqrt((sigma[:lt] ** 2).sum()))
    rop = lead_demand + safety

    cum = np.cumsum(p50)
    out_idx = int(np.argmax(cum > item.on_hand)) if (cum > item.on_hand).any() else None
    stockout_date = forecast["date"].iloc[out_idx] if out_idx is not None else None
    days_of_cover = item.on_hand / avg_daily if avg_daily > 0 else None

    # Order up to: demand over lead time + review period, plus safety stock.
    cover_days = lt + s.review_period_days
    order_up_to = avg_daily * cover_days + safety
    recommended = max(0, int(np.ceil(order_up_to - item.on_hand)))

    overstock_units = max(0, int(item.on_hand - avg_daily * s.overstock_days_threshold))

    if out_idx is not None and out_idx < lt:
        status = RiskStatus.STOCKOUT
    elif item.on_hand < rop:
        status = RiskStatus.REORDER
    elif overstock_units > 0:
        status = RiskStatus.OVERSTOCK
    else:
        status = RiskStatus.HEALTHY

    avg_last_28 = last_28_total / 28
    trend = (avg_daily - avg_last_28) / avg_last_28 if avg_last_28 > 0 else 0.0

    return ItemRisk(
        sku=item.sku,
        name=item.name,
        category=item.category,
        on_hand=item.on_hand,
        unit_cost=item.unit_cost,
        lead_time_days=item.lead_time_days,
        avg_daily_forecast=round(avg_daily, 2),
        avg_daily_last_28=round(avg_last_28, 2),
        trend_pct=round(trend * 100, 1),
        days_of_cover=round(days_of_cover, 1) if days_of_cover is not None else None,
        projected_stockout_date=stockout_date,
        reorder_point=round(rop, 1),
        safety_stock=round(safety, 1),
        recommended_order_qty=recommended if status in (RiskStatus.STOCKOUT, RiskStatus.REORDER) else 0,
        excess_units=overstock_units if status == RiskStatus.OVERSTOCK else 0,
        excess_value=round(overstock_units * item.unit_cost, 2) if status == RiskStatus.OVERSTOCK else 0.0,
        status=status,
    )


def assess_all(session: Session) -> list[ItemRisk]:
    items = session.scalars(select(Item).order_by(Item.sku)).all()
    fc = pd.read_sql(select(Forecast.sku, Forecast.date, Forecast.p10, Forecast.p50, Forecast.p90), session.connection())
    if fc.empty:
        return []
    fc = fc.sort_values(["sku", "date"])
    sales = pd.read_sql(select(Sale.sku, Sale.date, Sale.qty), session.connection())
    sales["date"] = pd.to_datetime(sales["date"])
    last_day = sales["date"].max()
    recent = sales[sales["date"] > last_day - pd.Timedelta(days=28)].groupby("sku")["qty"].sum()

    by_sku = dict(tuple(fc.groupby("sku")))
    return [
        assess_item(item, by_sku[item.sku].reset_index(drop=True), float(recent.get(item.sku, 0.0)))
        for item in items
        if item.sku in by_sku
    ]


SEVERITY = {RiskStatus.STOCKOUT: 0, RiskStatus.REORDER: 1, RiskStatus.OVERSTOCK: 2, RiskStatus.HEALTHY: 3}


def sort_key(r: ItemRisk) -> tuple:
    """Most urgent first: by status, then soonest stockout, then largest excess value."""
    return (SEVERITY[r.status], r.projected_stockout_date or date.max, -r.excess_value)


def summarize(risks: list[ItemRisk]) -> dict:
    counts = {status.value: 0 for status in RiskStatus}
    for r in risks:
        counts[r.status.value] += 1
    return {
        "total_items": len(risks),
        "counts": counts,
        "inventory_value": round(sum(r.on_hand * r.unit_cost for r in risks), 2),
        "excess_value": round(sum(r.excess_value for r in risks), 2),
        "reorder_value": round(sum(r.recommended_order_qty * r.unit_cost for r in risks), 2),
    }
