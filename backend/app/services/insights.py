"""Plain-language inventory insights.

The forecasting and risk engine produce numbers; this module turns them into a briefing a
supply-chain manager can act on. The LLM only sees a compact, pre-computed JSON context
(it never does arithmetic on raw sales), and its output is constrained to a JSON schema so
the frontend can render it reliably. With no API key, or on any API failure, a deterministic
rules-based briefing with the same shape is returned instead.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel

from ..config import get_settings
from .inventory import ItemRisk, RiskStatus, sort_key, summarize

log = logging.getLogger(__name__)


class RiskCallout(BaseModel):
    sku: str
    issue: str
    recommendation: str


class Action(BaseModel):
    priority: Literal["high", "medium", "low"]
    action: str
    rationale: str


class Briefing(BaseModel):
    headline: str
    summary: str
    key_risks: list[RiskCallout]
    trends: list[str]
    actions: list[Action]
    source: Literal["llm", "rules"] = "rules"
    model: str | None = None
    generated_at: datetime | None = None


# Hand-written so it satisfies structured-output requirements (closed objects, all fields required).
BRIEFING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "summary", "key_risks", "trends", "actions"],
    "properties": {
        "headline": {"type": "string", "description": "One sentence, under 15 words."},
        "summary": {"type": "string", "description": "2-4 sentence executive summary."},
        "key_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sku", "issue", "recommendation"],
                "properties": {"sku": {"type": "string"}, "issue": {"type": "string"}, "recommendation": {"type": "string"}},
            },
        },
        "trends": {"type": "array", "items": {"type": "string"}},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["priority", "action", "rationale"],
                "properties": {
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are an inventory analyst writing a briefing for supply-chain decision-makers.

You receive pre-computed JSON: portfolio totals, the highest-priority at-risk items (with forecasts, \
reorder points and recommended order quantities), demand trends by category, and forecast accuracy \
from a holdout backtest. All numbers are already calculated; quote them rather than recomputing.

Write for a busy operations manager: concrete, specific, no filler. Reference SKUs and item names, \
dollar amounts and dates where they matter. Include 3-6 key risks (most urgent first), 2-4 trends, \
and 3-5 prioritized actions. If forecast accuracy is weak for an item you mention, say so briefly \
so the reader calibrates their confidence."""


def build_context(risks: list[ItemRisk], metrics: dict | None) -> dict:
    ranked = sorted((r for r in risks if r.status != RiskStatus.HEALTHY), key=sort_key)
    per_sku_wape = (metrics or {}).get("per_sku_wape", {})

    categories: dict[str, dict[str, float]] = {}
    for r in risks:
        c = categories.setdefault(r.category, {"last_28_daily": 0.0, "forecast_daily": 0.0})
        c["last_28_daily"] += r.avg_daily_last_28
        c["forecast_daily"] += r.avg_daily_forecast
    trends = {
        name: {**{k: round(v, 1) for k, v in c.items()}, "change_pct": round((c["forecast_daily"] / c["last_28_daily"] - 1) * 100, 1) if c["last_28_daily"] else 0.0}
        for name, c in categories.items()
    }

    return {
        "portfolio": summarize(risks),
        "priority_items": [
            {
                "sku": r.sku,
                "name": r.name,
                "status": r.status.value,
                "on_hand": r.on_hand,
                "days_of_cover": r.days_of_cover,
                "lead_time_days": r.lead_time_days,
                "projected_stockout_date": r.projected_stockout_date.isoformat() if r.projected_stockout_date else None,
                "avg_daily_forecast": r.avg_daily_forecast,
                "demand_trend_pct": r.trend_pct,
                "recommended_order_qty": r.recommended_order_qty,
                "excess_value_usd": r.excess_value,
                "forecast_wape": round(per_sku_wape[r.sku], 3) if r.sku in per_sku_wape else None,
            }
            for r in ranked[:12]
        ],
        "category_trends_next_28d_vs_last_28d": trends,
        "forecast_accuracy": {k: v for k, v in (metrics or {}).items() if k != "per_sku_wape"},
    }


def rules_briefing(context: dict) -> Briefing:
    p = context["portfolio"]
    n_out, n_reorder, n_over = p["counts"]["stockout_risk"], p["counts"]["reorder"], p["counts"]["overstock"]
    risks = []
    for it in context["priority_items"][:5]:
        if it["status"] == "overstock":
            risks.append(RiskCallout(sku=it["sku"], issue=f"{it['name']}: ~{it['days_of_cover']} days of cover, ${it['excess_value_usd']:,.0f} tied up in excess stock.", recommendation="Pause replenishment and consider a promotion or transfer."))
        else:
            when = f" by {it['projected_stockout_date']}" if it["projected_stockout_date"] else ""
            risks.append(RiskCallout(sku=it["sku"], issue=f"{it['name']}: projected to stock out{when} with a {it['lead_time_days']}-day lead time.", recommendation=f"Order {it['recommended_order_qty']} units now."))
    trends = [
        f"{cat}: forecast demand {'up' if t['change_pct'] >= 0 else 'down'} {abs(t['change_pct'])}% vs the last 28 days."
        for cat, t in sorted(context["category_trends_next_28d_vs_last_28d"].items(), key=lambda kv: -abs(kv[1]["change_pct"]))[:3]
    ]
    actions = []
    if n_out:
        actions.append(Action(priority="high", action=f"Expedite orders for {n_out} items at stockout risk.", rationale="They will run out before a standard order can arrive."))
    if n_reorder:
        actions.append(Action(priority="medium", action=f"Place replenishment orders for {n_reorder} items below reorder point.", rationale=f"Total recommended order value is ${p['reorder_value']:,.0f}."))
    if n_over:
        actions.append(Action(priority="low", action=f"Review {n_over} overstocked items.", rationale=f"${p['excess_value']:,.0f} of working capital is tied up in excess inventory."))
    return Briefing(
        headline=f"{n_out + n_reorder} items need replenishment; {n_over} are overstocked.",
        summary=f"Of {p['total_items']} tracked items, {n_out} are at risk of stocking out before new stock could arrive and {n_reorder} are below their reorder point. Excess inventory totals ${p['excess_value']:,.0f}.",
        key_risks=risks,
        trends=trends,
        actions=actions,
        source="rules",
        generated_at=datetime.now(timezone.utc),
    )


def llm_briefing(context: dict, client: anthropic.Anthropic | None = None) -> Briefing:
    settings = get_settings()
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.beta.messages.create(
        model=settings.llm_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Inventory data:\n```json\n{json.dumps(context, default=str)}\n```\n\nWrite the briefing."}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": BRIEFING_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model declined the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Briefing was truncated (max_tokens)")
    text = next(b.text for b in response.content if b.type == "text")
    briefing = Briefing.model_validate_json(text)
    return briefing.model_copy(update={"source": "llm", "model": response.model, "generated_at": datetime.now(timezone.utc)})


def generate_briefing(risks: list[ItemRisk], metrics: dict | None) -> Briefing:
    context = build_context(risks, metrics)
    if not get_settings().anthropic_api_key:
        return rules_briefing(context)
    try:
        return llm_briefing(context)
    except (anthropic.APIError, RuntimeError, ValueError) as exc:
        log.warning("LLM briefing failed, using rules-based fallback: %s", exc)
        return rules_briefing(context)
