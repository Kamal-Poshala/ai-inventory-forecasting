"""Synthetic retail sales generator.

Produces realistic daily demand per SKU: a base level, linear trend, weekly and
yearly seasonality, random promotions, and Poisson noise. Starting stock levels
are deliberately spread so the demo contains stockout, overstock and healthy items.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

CATALOG: dict[str, list[str]] = {
    "Beverages": ["Sparkling Water 12pk", "Cold Brew Coffee", "Green Tea 6pk", "Orange Juice 1L", "Energy Drink 4pk", "Oat Milk 1L"],
    "Snacks": ["Sea Salt Chips", "Trail Mix", "Protein Bar Box", "Dark Chocolate Bar", "Pretzel Twists", "Rice Crackers"],
    "Household": ["Paper Towels 6pk", "Dish Soap", "Laundry Pods 42ct", "Trash Bags 30ct", "Disinfecting Wipes", "Sponges 3pk"],
    "Personal Care": ["Toothpaste", "Shampoo 400ml", "Body Wash", "Hand Sanitizer", "Sunscreen SPF50", "Lip Balm"],
    "Seasonal": ["Hot Cocoa Mix", "Sunflower Seeds", "Ice Pops 12ct", "Pumpkin Spice Coffee", "Bug Spray", "Holiday Cookies"],
}

# Peak day-of-year for seasonal items; others get a mild summer bump.
SEASONAL_PEAKS = {
    "Hot Cocoa Mix": 355, "Sunflower Seeds": 200, "Ice Pops 12ct": 195,
    "Pumpkin Spice Coffee": 290, "Bug Spray": 185, "Holiday Cookies": 350, "Sunscreen SPF50": 180,
}


@dataclass
class SyntheticData:
    items: pd.DataFrame
    sales: pd.DataFrame


def generate(days: int = 730, end: date | None = None, seed: int = 42) -> SyntheticData:
    rng = np.random.default_rng(seed)
    end = end or date.today() - timedelta(days=1)
    dates = pd.date_range(end=pd.Timestamp(end), periods=days, freq="D")
    t = np.arange(days)
    doy = dates.dayofyear.to_numpy()
    dow = dates.dayofweek.to_numpy()

    item_rows, sales_frames = [], []
    idx = 0
    for category, names in CATALOG.items():
        for name in names:
            idx += 1
            sku = f"SKU-{idx:04d}"
            base = rng.uniform(4, 60)
            trend = rng.normal(0, 0.35) / days  # relative change per day
            weekly = 1 + rng.uniform(0.05, 0.35) * np.where(dow >= 4, 1.0, -0.4)

            if name in SEASONAL_PEAKS:
                dist = np.minimum(np.abs(doy - SEASONAL_PEAKS[name]), 365 - np.abs(doy - SEASONAL_PEAKS[name]))
                yearly = 0.25 + 2.2 * np.exp(-((dist / 35.0) ** 2))
            else:
                yearly = 1 + 0.12 * np.sin(2 * np.pi * (doy - 100) / 365.25)

            promo = np.ones(days)
            for start in rng.choice(days - 7, size=rng.integers(3, 8), replace=False):
                promo[start : start + rng.integers(3, 8)] *= rng.uniform(1.4, 2.2)

            lam = np.clip(base * (1 + trend * t) * weekly * yearly * promo, 0.05, None)
            qty = rng.poisson(lam)
            sales_frames.append(pd.DataFrame({"sku": sku, "date": dates.date, "qty": qty}))

            recent_daily = qty[-28:].mean()
            lead_time = int(rng.choice([3, 5, 7, 10, 14, 21]))
            # Mix of under-, over- and well-stocked positions.
            cover_days = rng.choice([rng.uniform(1, lead_time), rng.uniform(120, 220), rng.uniform(25, 70)], p=[0.3, 0.2, 0.5])
            item_rows.append(
                {
                    "sku": sku,
                    "name": name,
                    "category": category,
                    "unit_cost": round(float(rng.uniform(1.5, 25)), 2),
                    "lead_time_days": lead_time,
                    "on_hand": int(max(0, round(recent_daily * cover_days))),
                }
            )

    return SyntheticData(items=pd.DataFrame(item_rows), sales=pd.concat(sales_frames, ignore_index=True))
