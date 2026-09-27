"""'Ask your inventory': a Claude agent that answers questions with tool calls.

Claude never sees the database directly. It gets a small set of read-only tools backed by the
same forecast and risk engine the dashboard uses (plus a what-if order simulator), decides which
to call, and grounds its answer in their results. Every tool call is returned to the client as a
trace so the UI can show *how* an answer was reached.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import anthropic
import numpy as np
import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import Forecast
from . import inventory, pipeline

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are StockSense, an inventory analyst assistant embedded in an inventory \
forecasting dashboard. Answer questions about stock levels, demand forecasts, stockout and \
overstock risk, and what to order.

Use the tools to look up facts; never guess numbers. Figures from the tools (forecasts, reorder \
points, recommended quantities) are already computed, so quote them. Use simulate_order for \
what-if questions about order sizes or timing. Refer to items by name and SKU.

Keep answers short and scannable: lead with the answer, then the supporting numbers. Use short \
bullet lists or a compact markdown table when comparing several items. If a question is outside \
inventory and demand (e.g. general chit-chat), say briefly what you can help with instead."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_portfolio_summary",
        "description": "Portfolio-wide KPIs: item counts by risk status, total inventory value, excess (overstock) value, and value of recommended reorders.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_items",
        "description": "List items with their risk assessment, most urgent first. Optionally filter by risk status and/or category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": [s.value for s in inventory.RiskStatus], "description": "Only items with this risk status."},
                "category": {"type": "string", "description": "Only items in this category, e.g. 'Snacks'."},
                "limit": {"type": "integer", "description": "Max items to return (default 10, max 50)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_item",
        "description": "Full detail for one SKU: stock position, reorder point, safety stock, recommended order, weekly demand forecast (p10/p50/p90) and forecast accuracy. Accepts a SKU like 'SKU-0004' or an item name.",
        "input_schema": {
            "type": "object",
            "properties": {"sku_or_name": {"type": "string"}},
            "required": ["sku_or_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_category_trends",
        "description": "Per-category average daily demand: last 28 days of actuals vs the next 28 days of forecast, with percentage change.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_model_accuracy",
        "description": "Forecast model backtest results: WAPE, MAE, bias, prediction-interval coverage, and baseline comparisons.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "simulate_order",
        "description": "What-if: project daily stock for one SKU over the forecast horizon if an order of `order_qty` units arrives after `arrival_in_days` days (defaults to the supplier lead time). Reports whether and when a stockout still happens.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sku_or_name": {"type": "string"},
                "order_qty": {"type": "integer", "description": "Units to order (>= 0)."},
                "arrival_in_days": {"type": "integer", "description": "Days until the order arrives. Defaults to the item's lead time."},
            },
            "required": ["sku_or_name", "order_qty"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    pass


class InventoryTools:
    """Tool implementations. Risk assessment is computed once per chat request."""

    def __init__(self, session: Session):
        self.session = session
        self._risks: list[inventory.ItemRisk] | None = None

    @property
    def risks(self) -> list[inventory.ItemRisk]:
        if self._risks is None:
            self._risks = inventory.assess_all(self.session)
        return self._risks

    def _find(self, sku_or_name: str) -> inventory.ItemRisk:
        q = sku_or_name.strip().lower()
        for r in self.risks:
            if r.sku.lower() == q or r.name.lower() == q:
                return r
        matches = [r for r in self.risks if q in r.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ToolError(f"Ambiguous item '{sku_or_name}'. Candidates: " + ", ".join(f"{r.sku} ({r.name})" for r in matches[:8]))
        raise ToolError(f"No item matches '{sku_or_name}'.")

    def _forecast(self, sku: str) -> pd.DataFrame:
        rows = self.session.execute(
            select(Forecast.date, Forecast.p10, Forecast.p50, Forecast.p90).where(Forecast.sku == sku).order_by(Forecast.date)
        ).all()
        return pd.DataFrame(rows, columns=["date", "p10", "p50", "p90"])

    @staticmethod
    def _brief(r: inventory.ItemRisk) -> dict:
        return {
            "sku": r.sku, "name": r.name, "category": r.category, "status": r.status.value,
            "on_hand": r.on_hand, "days_of_cover": r.days_of_cover,
            "projected_stockout_date": r.projected_stockout_date, "recommended_order_qty": r.recommended_order_qty,
            "excess_value_usd": r.excess_value, "demand_trend_pct": r.trend_pct,
        }

    def get_portfolio_summary(self) -> dict:
        return inventory.summarize(self.risks)

    def list_items(self, status: str | None = None, category: str | None = None, limit: int = 10) -> dict:
        rows = [r for r in self.risks if (not status or r.status.value == status) and (not category or r.category.lower() == category.lower())]
        rows.sort(key=inventory.sort_key)
        limit = max(1, min(int(limit), 50))
        return {"total_matching": len(rows), "items": [self._brief(r) for r in rows[:limit]]}

    def get_item(self, sku_or_name: str) -> dict:
        r = self._find(sku_or_name)
        fc = self._forecast(r.sku)
        fc["week"] = np.arange(len(fc)) // 7 + 1
        weekly = fc.groupby("week").agg(start=("date", "first"), p10=("p10", "sum"), p50=("p50", "sum"), p90=("p90", "sum")).round(1)
        run = pipeline.latest_run(self.session)
        wape = (run.metrics.get("per_sku_wape", {}) if run else {}).get(r.sku)
        return {
            **r.to_dict(),
            "weekly_forecast_units": weekly.reset_index().to_dict("records"),
            "forecast_wape": round(wape, 3) if wape is not None else None,
        }

    def get_category_trends(self) -> dict:
        cats: dict[str, dict[str, float]] = {}
        for r in self.risks:
            c = cats.setdefault(r.category, {"last_28d_daily": 0.0, "next_28d_daily_forecast": 0.0})
            c["last_28d_daily"] += r.avg_daily_last_28
            c["next_28d_daily_forecast"] += r.avg_daily_forecast
        return {
            name: {**{k: round(v, 1) for k, v in c.items()}, "change_pct": round((c["next_28d_daily_forecast"] / c["last_28d_daily"] - 1) * 100, 1) if c["last_28d_daily"] else None}
            for name, c in cats.items()
        }

    def get_model_accuracy(self) -> dict:
        run = pipeline.latest_run(self.session)
        if run is None:
            raise ToolError("No trained model yet.")
        return {"trained_at": run.trained_at, **{k: v for k, v in run.metrics.items() if k != "per_sku_wape"}}

    def simulate_order(self, sku_or_name: str, order_qty: int, arrival_in_days: int | None = None) -> dict:
        r = self._find(sku_or_name)
        if order_qty < 0:
            raise ToolError("order_qty must be >= 0")
        arrival = r.lead_time_days if arrival_in_days is None else max(0, int(arrival_in_days))
        fc = self._forecast(r.sku)
        demand = fc["p50"].to_numpy()
        receipts = np.zeros(len(demand))
        if arrival < len(demand):
            receipts[arrival] = order_qty
        stock = r.on_hand + np.cumsum(receipts - demand)
        short = np.flatnonzero(stock < 0)
        return {
            "sku": r.sku, "name": r.name, "on_hand": r.on_hand, "order_qty": order_qty,
            "arrival_date": fc["date"].iloc[arrival] if arrival < len(fc) else "beyond forecast horizon",
            "stockout_date": fc["date"].iloc[short[0]] if short.size else None,
            "days_short": int(short.size),
            "lost_units_estimate": round(float(-np.clip(stock, None, 0).min()), 1) if short.size else 0.0,
            "min_stock": round(float(stock.min()), 1),
            "stock_at_horizon_end": round(float(stock[-1]), 1),
            "days_of_cover_at_horizon_end": round(float(stock[-1] / demand.mean()), 1) if demand.mean() > 0 and stock[-1] > 0 else 0.0,
            "horizon_days": len(demand),
        }

    def dispatch(self) -> dict[str, Callable[..., dict]]:
        return {t["name"]: getattr(self, t["name"]) for t in TOOLS}


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ToolCall(BaseModel):
    name: str
    input: dict
    is_error: bool = False


class ChatReply(BaseModel):
    answer: str
    tool_calls: list[ToolCall]
    model: str


def run_tool(tools: InventoryTools, name: str, args: dict) -> tuple[str, bool]:
    fn = tools.dispatch().get(name)
    if fn is None:
        return f"Unknown tool: {name}", True
    try:
        return json.dumps(fn(**args), default=str), False
    except ToolError as exc:
        return str(exc), True
    except TypeError as exc:  # bad arguments from the model
        return f"Invalid arguments for {name}: {exc}", True


def chat(session: Session, history: list[ChatMessage], client: anthropic.Anthropic | None = None) -> ChatReply:
    settings = get_settings()
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tools = InventoryTools(session)
    messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in history]
    trace: list[ToolCall] = []

    for _ in range(settings.chat_max_steps):
        response = client.beta.messages.create(
            model=settings.llm_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return ChatReply(answer="Sorry, I can't help with that request.", tool_calls=trace, model=response.model)

        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n_(response truncated)_"
            return ChatReply(answer=text or "I couldn't produce an answer.", tool_calls=trace, model=response.model)

        results = []
        for block in tool_uses:
            output, is_error = run_tool(tools, block.name, dict(block.input))
            trace.append(ToolCall(name=block.name, input=dict(block.input), is_error=is_error))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    return ChatReply(answer="I needed too many steps to answer that. Try a more specific question.", tool_calls=trace, model=settings.llm_model)
