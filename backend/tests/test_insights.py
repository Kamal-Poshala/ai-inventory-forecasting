import json
from datetime import date
from types import SimpleNamespace

from app.services.insights import Briefing, build_context, llm_briefing, rules_briefing
from app.services.inventory import ItemRisk, RiskStatus


def _risk(sku: str, status: RiskStatus, **kw) -> ItemRisk:
    base = dict(
        sku=sku, name=f"Item {sku}", category="Snacks", on_hand=10, unit_cost=2.0, lead_time_days=7,
        avg_daily_forecast=5.0, avg_daily_last_28=4.0, trend_pct=25.0, days_of_cover=2.0,
        projected_stockout_date=date(2026, 1, 3), reorder_point=40.0, safety_stock=5.0,
        recommended_order_qty=60, excess_units=0, excess_value=0.0, status=status,
    )
    return ItemRisk(**{**base, **kw})


RISKS = [
    _risk("A", RiskStatus.HEALTHY, projected_stockout_date=None, recommended_order_qty=0),
    _risk("B", RiskStatus.OVERSTOCK, projected_stockout_date=None, excess_units=500, excess_value=1000.0, recommended_order_qty=0),
    _risk("C", RiskStatus.STOCKOUT),
]


def test_context_ranks_stockouts_first_and_skips_healthy():
    ctx = build_context(RISKS, {"per_sku_wape": {"C": 0.2}, "model": {"wape": 0.2}})
    assert [i["sku"] for i in ctx["priority_items"]] == ["C", "B"]
    assert ctx["priority_items"][0]["forecast_wape"] == 0.2
    assert "per_sku_wape" not in ctx["forecast_accuracy"]
    json.dumps(ctx, default=str)  # must be serializable for the prompt


def test_rules_briefing_shape():
    b = rules_briefing(build_context(RISKS, None))
    assert b.source == "rules"
    assert b.key_risks[0].sku == "C"
    assert {a.priority for a in b.actions} == {"high", "low"}


def test_llm_briefing_parses_structured_output():
    payload = {
        "headline": "One item will stock out this week.",
        "summary": "Summary.",
        "key_risks": [{"sku": "C", "issue": "Stockout Jan 3", "recommendation": "Order 60"}],
        "trends": ["Snacks up 25%"],
        "actions": [{"priority": "high", "action": "Expedite C", "rationale": "Lead time 7d"}],
    }
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5", content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    b = llm_briefing(build_context(RISKS, None), client=client)

    assert isinstance(b, Briefing) and b.source == "llm" and b.model == "claude-opus-5"
    assert b.key_risks[0].sku == "C"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["fallbacks"] == "default"
