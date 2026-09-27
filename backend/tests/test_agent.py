import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.services import agent


@pytest.fixture(scope="module")
def session():
    with TestClient(app):  # seed + train
        with SessionLocal() as s:
            yield s


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


class FakeClient:
    """Replays scripted responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def test_tools_return_grounded_data(session):
    tools = agent.InventoryTools(session)
    stockouts = tools.list_items(status="stockout_risk", limit=3)
    assert stockouts["total_matching"] > 0 and len(stockouts["items"]) <= 3
    assert all(i["status"] == "stockout_risk" for i in stockouts["items"])

    sku = stockouts["items"][0]["sku"]
    detail = tools.get_item(sku)
    assert detail["sku"] == sku and len(detail["weekly_forecast_units"]) == 4
    # Lookup by (partial) name works too.
    assert tools.get_item(detail["name"])["sku"] == sku

    assert set(tools.get_category_trends()) == {"Beverages", "Snacks", "Household", "Personal Care", "Seasonal"}
    assert tools.get_model_accuracy()["model"]["wape"] > 0


def test_simulate_order_prevents_stockout(session):
    tools = agent.InventoryTools(session)
    item = tools.list_items(status="stockout_risk", limit=50)["items"][-1]
    none = tools.simulate_order(item["sku"], order_qty=0)
    assert none["stockout_date"] is not None and none["days_short"] > 0
    big = tools.simulate_order(item["sku"], order_qty=100_000, arrival_in_days=0)
    assert big["stockout_date"] is None and big["min_stock"] > 0


def test_run_tool_reports_errors_to_model(session):
    tools = agent.InventoryTools(session)
    out, is_error = agent.run_tool(tools, "get_item", {"sku_or_name": "does-not-exist"})
    assert is_error and "No item matches" in out
    out, is_error = agent.run_tool(tools, "get_item", {"wrong_arg": 1})
    assert is_error and "Invalid arguments" in out
    out, is_error = agent.run_tool(tools, "drop_tables", {})
    assert is_error


def test_chat_loop_executes_tools_and_returns_answer(session):
    client = FakeClient(
        [
            SimpleNamespace(stop_reason="tool_use", model="claude-opus-5", content=[_tool_use("t1", "get_portfolio_summary", {}), _tool_use("t2", "list_items", {"status": "overstock", "limit": 2})]),
            SimpleNamespace(stop_reason="end_turn", model="claude-opus-5", content=[_text("You have 7 overstocked items.")]),
        ]
    )
    reply = agent.chat(session, [agent.ChatMessage(role="user", content="How much overstock?")], client=client)

    assert reply.answer == "You have 7 overstocked items."
    assert [c.name for c in reply.tool_calls] == ["get_portfolio_summary", "list_items"]
    # Both tool results go back in a single user message, matched by id.
    results = client.requests[1]["messages"][-1]
    assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert json.loads(results["content"][1]["content"])["items"][0]["status"] == "overstock"
    assert client.requests[0]["fallbacks"] == "default"


def test_chat_handles_refusal(session):
    client = FakeClient([SimpleNamespace(stop_reason="refusal", model="claude-opus-5", content=[])])
    reply = agent.chat(session, [agent.ChatMessage(role="user", content="hi")], client=client)
    assert "can't help" in reply.answer
