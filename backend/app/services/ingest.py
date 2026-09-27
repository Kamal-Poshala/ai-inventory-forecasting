"""CSV ingestion for item master data and daily sales, with row-level validation."""

import io
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import Forecast, Item, Sale

ITEM_COLUMNS = ["sku", "name", "category", "unit_cost", "lead_time_days", "on_hand"]
SALES_COLUMNS = ["sku", "date", "qty"]
MAX_REPORTED_ERRORS = 20


class ValidationFailed(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:3]))
        self.errors = errors[:MAX_REPORTED_ERRORS]


@dataclass
class IngestResult:
    rows: int
    inserted: int = 0
    updated: int = 0
    warnings: list[str] = field(default_factory=list)


def _read_csv(raw: bytes, required: list[str]) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype=str, skipinitialspace=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ValidationFailed([f"Could not parse CSV: {exc}"]) from exc
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValidationFailed([f"Missing required column(s): {', '.join(missing)}. Expected: {', '.join(required)}"])
    df = df[required].apply(lambda s: s.str.strip())
    if df.empty:
        raise ValidationFailed(["CSV has no data rows"])
    return df


def _bad_rows(mask: pd.Series, message: str) -> list[str]:
    # +2: 1-based rows plus the header line.
    return [f"Row {i + 2}: {message}" for i in mask[mask].index[:MAX_REPORTED_ERRORS]]


def parse_items(raw: bytes) -> pd.DataFrame:
    df = _read_csv(raw, ITEM_COLUMNS)
    errors: list[str] = []
    for col in ("sku", "name", "category"):
        errors += _bad_rows(df[col].isna() | (df[col] == ""), f"'{col}' is empty")
    df["unit_cost"] = pd.to_numeric(df["unit_cost"], errors="coerce")
    errors += _bad_rows(df["unit_cost"].isna() | (df["unit_cost"] < 0), "'unit_cost' must be a non-negative number")
    for col in ("lead_time_days", "on_hand"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        errors += _bad_rows(df[col].isna() | (df[col] < 0) | (df[col] % 1 != 0), f"'{col}' must be a non-negative integer")
    errors += _bad_rows(df["lead_time_days"] < 1, "'lead_time_days' must be at least 1")
    errors += _bad_rows(df["sku"].duplicated(keep=False), "duplicate sku")
    if errors:
        raise ValidationFailed(errors)
    return df.astype({"lead_time_days": int, "on_hand": int})


def parse_sales(raw: bytes) -> pd.DataFrame:
    df = _read_csv(raw, SALES_COLUMNS)
    errors = _bad_rows(df["sku"].isna() | (df["sku"] == ""), "'sku' is empty")
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    errors += _bad_rows(df["date"].isna(), "'date' is not a valid date (use YYYY-MM-DD)")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    errors += _bad_rows(df["qty"].isna() | (df["qty"] < 0) | (df["qty"] % 1 != 0), "'qty' must be a non-negative integer")
    if errors:
        raise ValidationFailed(errors)
    df["date"] = df["date"].dt.date
    # Multiple rows for the same sku/day (e.g. per-transaction exports) are summed.
    return df.groupby(["sku", "date"], as_index=False)["qty"].sum().astype({"qty": int})


def upsert_items(session: Session, df: pd.DataFrame) -> IngestResult:
    existing = {i.sku: i for i in session.scalars(select(Item).where(Item.sku.in_(df["sku"].tolist())))}
    result = IngestResult(rows=len(df))
    for row in df.to_dict("records"):
        if item := existing.get(row["sku"]):
            for k, v in row.items():
                setattr(item, k, v)
            result.updated += 1
        else:
            session.add(Item(**row))
            result.inserted += 1
    session.commit()
    return result


def load_sales(session: Session, df: pd.DataFrame, mode: Literal["append", "replace"]) -> IngestResult:
    known = set(session.scalars(select(Item.sku)))
    unknown = sorted(set(df["sku"]) - known)
    if unknown:
        raise ValidationFailed([f"Unknown SKU(s): {', '.join(unknown[:10])}{' …' if len(unknown) > 10 else ''}. Upload them in the items CSV first."])

    result = IngestResult(rows=len(df))
    if mode == "replace":
        session.execute(delete(Forecast))
        session.execute(delete(Sale))
    else:
        # Uploaded rows win over existing rows for the same sku/day.
        for sku, dates in df.groupby("sku")["date"]:
            result.updated += session.execute(delete(Sale).where(Sale.sku == sku, Sale.date.in_(dates.tolist()))).rowcount or 0
    session.bulk_insert_mappings(Sale, df.to_dict("records"))
    result.inserted = len(df) - result.updated
    session.commit()

    span = (max(df["date"]) - min(df["date"])).days + 1
    if mode == "replace" and span < 90:
        result.warnings.append(f"Only {span} days of history; forecasts need at least ~90 days to be reliable.")
    return result


def items_template() -> str:
    return "sku,name,category,unit_cost,lead_time_days,on_hand\nSKU-1001,Cold Brew Coffee,Beverages,3.50,7,420\n"


def sales_template() -> str:
    return "sku,date,qty\nSKU-1001,2026-01-01,34\nSKU-1001,2026-01-02,29\n"
