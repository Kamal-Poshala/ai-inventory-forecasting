from datetime import date, datetime
from typing import Literal

import anthropic
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import Forecast, Item, Sale, get_session
from ..services import agent, ingest, insights, inventory, pipeline

router = APIRouter(prefix="/api")

# Briefings cost an LLM call, so cache one per model run.
_briefing_cache: dict[int, insights.Briefing] = {}


class ItemRiskOut(BaseModel):
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
    status: inventory.RiskStatus


class Point(BaseModel):
    date: date
    qty: float


class ForecastPoint(BaseModel):
    date: date
    p10: float
    p50: float
    p90: float


class ItemDetailOut(BaseModel):
    risk: ItemRiskOut
    history: list[Point]
    forecast: list[ForecastPoint]
    forecast_wape: float | None


class SummaryOut(BaseModel):
    total_items: int
    counts: dict[str, int]
    inventory_value: float
    excess_value: float
    reorder_value: float
    last_trained_at: datetime | None


class ModelMetricsOut(BaseModel):
    run_id: int
    trained_at: datetime
    metrics: dict


def _require_run(session: Session):
    run = pipeline.latest_run(session)
    if run is None:
        raise HTTPException(503, "No trained model yet. POST /api/model/retrain first.")
    return run


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_enabled": bool(get_settings().anthropic_api_key)}


@router.get("/summary", response_model=SummaryOut)
def get_summary(session: Session = Depends(get_session)):
    run = _require_run(session)
    return {**inventory.summarize(inventory.assess_all(session)), "last_trained_at": run.trained_at}


@router.get("/items", response_model=list[ItemRiskOut])
def list_items(
    status: inventory.RiskStatus | None = None,
    category: str | None = None,
    session: Session = Depends(get_session),
):
    _require_run(session)
    risks = inventory.assess_all(session)
    risks = [r for r in risks if (status is None or r.status == status) and (category is None or r.category == category)]
    risks.sort(key=inventory.sort_key)
    return [r.to_dict() for r in risks]


@router.get("/items/{sku}", response_model=ItemDetailOut)
def get_item(sku: str, history_days: int = Query(120, ge=14, le=730), session: Session = Depends(get_session)):
    run = _require_run(session)
    if session.get(Item, sku) is None:
        raise HTTPException(404, f"Unknown SKU {sku}")
    risk = next((r for r in inventory.assess_all(session) if r.sku == sku), None)
    if risk is None:
        raise HTTPException(409, f"No forecast for {sku} yet. Retrain the model.")
    sales = session.execute(select(Sale.date, Sale.qty).where(Sale.sku == sku).order_by(Sale.date.desc()).limit(history_days)).all()
    fc = session.execute(select(Forecast.date, Forecast.p10, Forecast.p50, Forecast.p90).where(Forecast.sku == sku).order_by(Forecast.date)).all()
    return {
        "risk": risk.to_dict(),
        "history": [{"date": d, "qty": q} for d, q in reversed(sales)],
        "forecast": [{"date": d, "p10": lo, "p50": mid, "p90": hi} for d, lo, mid, hi in fc],
        "forecast_wape": run.metrics.get("per_sku_wape", {}).get(sku),
    }


@router.get("/model/metrics", response_model=ModelMetricsOut)
def model_metrics(session: Session = Depends(get_session)):
    run = _require_run(session)
    return {"run_id": run.id, "trained_at": run.trained_at, "metrics": {k: v for k, v in run.metrics.items() if k != "per_sku_wape"}}


def _retrain(session: Session):
    try:
        run = pipeline.retrain(session)
    except pipeline.RetrainInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:  # e.g. not enough history to train
        raise HTTPException(422, f"Training failed: {exc}") from exc
    _briefing_cache.clear()
    return run


def _metrics_out(run) -> dict:
    return {"run_id": run.id, "trained_at": run.trained_at, "metrics": {k: v for k, v in run.metrics.items() if k != "per_sku_wape"}}


@router.post("/model/retrain", response_model=ModelMetricsOut)
def retrain(session: Session = Depends(get_session)):
    return _metrics_out(_retrain(session))


class ItemUpdate(BaseModel):
    on_hand: int | None = Field(None, ge=0)
    lead_time_days: int | None = Field(None, ge=1, le=365)
    unit_cost: float | None = Field(None, ge=0)


@router.patch("/items/{sku}", response_model=ItemRiskOut)
def update_item(sku: str, body: ItemUpdate, session: Session = Depends(get_session)):
    """Update a stock position (e.g. after a cycle count or a delivery). Risk is recomputed immediately."""
    _require_run(session)
    item = session.get(Item, sku)
    if item is None:
        raise HTTPException(404, f"Unknown SKU {sku}")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(item, k, v)
    session.commit()
    _briefing_cache.clear()
    risk = next((r for r in inventory.assess_all(session) if r.sku == sku), None)
    if risk is None:
        raise HTTPException(409, f"No forecast for {sku} yet. Retrain the model.")
    return risk.to_dict()


class UploadOut(BaseModel):
    rows: int
    inserted: int
    updated: int
    warnings: list[str]
    retrained: bool
    run_id: int | None = None


async def _read_upload(file: UploadFile) -> bytes:
    raw = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if len(raw) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {get_settings().max_upload_mb} MB")
    return raw


def _validation_error(exc: ingest.ValidationFailed) -> HTTPException:
    return HTTPException(422, {"message": "CSV validation failed", "errors": exc.errors})


@router.post("/data/items", response_model=UploadOut)
async def upload_items(file: UploadFile = File(...), session: Session = Depends(get_session)):
    """Upsert item master data: sku,name,category,unit_cost,lead_time_days,on_hand."""
    try:
        result = ingest.upsert_items(session, ingest.parse_items(await _read_upload(file)))
    except ingest.ValidationFailed as exc:
        raise _validation_error(exc) from exc
    _briefing_cache.clear()
    return {**result.__dict__, "retrained": False}


@router.post("/data/sales", response_model=UploadOut)
async def upload_sales(
    file: UploadFile = File(...),
    mode: Literal["append", "replace"] = Query("append", description="append: upsert by sku+date; replace: wipe all sales first"),
    retrain: bool = True,
    session: Session = Depends(get_session),
):
    """Load daily sales: sku,date,qty. Retrains the model afterwards by default."""
    try:
        result = ingest.load_sales(session, ingest.parse_sales(await _read_upload(file)), mode)
    except ingest.ValidationFailed as exc:
        raise _validation_error(exc) from exc
    run = _retrain(session) if retrain else None
    return {**result.__dict__, "retrained": run is not None, "run_id": run.id if run else None}


@router.get("/data/templates/{kind}.csv", response_class=PlainTextResponse)
def csv_template(kind: Literal["items", "sales"]):
    body = ingest.items_template() if kind == "items" else ingest.sales_template()
    return PlainTextResponse(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}_template.csv"'})


class ChatIn(BaseModel):
    messages: list[agent.ChatMessage] = Field(..., min_length=1, max_length=40)


@router.post("/chat", response_model=agent.ChatReply)
def chat(body: ChatIn, session: Session = Depends(get_session)):
    """Ask a question about inventory; Claude answers using read-only tools over the forecast and risk engine."""
    if not get_settings().anthropic_api_key:
        raise HTTPException(503, "Chat needs an Anthropic API key. Set ANTHROPIC_API_KEY on the backend.")
    if body.messages[-1].role != "user":
        raise HTTPException(422, "The last message must be from the user")
    _require_run(session)
    try:
        return agent.chat(session, body.messages)
    except anthropic.APIError as exc:
        raise HTTPException(502, f"LLM request failed: {exc.__class__.__name__}") from exc


@router.get("/insights", response_model=insights.Briefing)
def get_insights(refresh: bool = False, session: Session = Depends(get_session)):
    run = _require_run(session)
    if refresh or run.id not in _briefing_cache:
        _briefing_cache[run.id] = insights.generate_briefing(inventory.assess_all(session), run.metrics)
    return _briefing_cache[run.id]
