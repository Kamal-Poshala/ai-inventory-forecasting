from datetime import date, timedelta

import pandas as pd

from app.db import Item
from app.services.inventory import RiskStatus, assess_item


def _forecast(daily: float, days: int = 28, spread: float = 2.0) -> pd.DataFrame:
    start = date(2026, 1, 1)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(days)],
            "p10": [max(0, daily - spread)] * days,
            "p50": [daily] * days,
            "p90": [daily + spread] * days,
        }
    )


def _item(on_hand: int, lead_time: int = 7) -> Item:
    return Item(sku="SKU-1", name="Test", category="Snacks", unit_cost=2.0, lead_time_days=lead_time, on_hand=on_hand)


def test_stockout_within_lead_time():
    r = assess_item(_item(on_hand=30, lead_time=7), _forecast(10), last_28_total=280)
    assert r.status == RiskStatus.STOCKOUT
    assert r.projected_stockout_date == date(2026, 1, 4)  # cumulative demand first exceeds 30 on day 4
    assert r.recommended_order_qty > 0


def test_reorder_when_below_reorder_point_but_covered_through_lead_time():
    # 7-day lead demand = 70, safety stock > 0, so 75 units is below ROP but lasts past lead time.
    r = assess_item(_item(on_hand=75, lead_time=7), _forecast(10), last_28_total=280)
    assert r.status == RiskStatus.REORDER
    assert r.reorder_point > 70


def test_overstock():
    r = assess_item(_item(on_hand=5000), _forecast(10), last_28_total=280)
    assert r.status == RiskStatus.OVERSTOCK
    assert r.excess_units == 5000 - 10 * 90
    assert r.excess_value == r.excess_units * 2.0
    assert r.recommended_order_qty == 0


def test_healthy_and_trend():
    r = assess_item(_item(on_hand=400), _forecast(10), last_28_total=28 * 8)
    assert r.status == RiskStatus.HEALTHY
    assert r.trend_pct == 25.0
    assert r.days_of_cover == 40.0
