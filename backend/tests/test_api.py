import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs lifespan: seed + train
        yield c


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "llm_enabled": False}


def test_summary_counts_add_up(client):
    s = client.get("/api/summary").json()
    assert s["total_items"] == 30
    assert sum(s["counts"].values()) == 30
    assert s["counts"]["stockout_risk"] > 0 and s["counts"]["overstock"] > 0


def test_items_sorted_by_severity_and_filterable(client):
    items = client.get("/api/items").json()
    order = ["stockout_risk", "reorder", "overstock", "healthy"]
    ranks = [order.index(i["status"]) for i in items]
    assert ranks == sorted(ranks)
    only = client.get("/api/items", params={"status": "overstock"}).json()
    assert only and all(i["status"] == "overstock" for i in only)


def test_item_detail(client):
    sku = client.get("/api/items").json()[0]["sku"]
    d = client.get(f"/api/items/{sku}", params={"history_days": 60}).json()
    assert len(d["history"]) == 60
    assert len(d["forecast"]) == 28
    assert d["history"][-1]["date"] < d["forecast"][0]["date"]
    assert client.get("/api/items/NOPE").status_code == 404


def test_metrics_and_insights(client):
    m = client.get("/api/model/metrics").json()["metrics"]
    assert m["model"]["wape"] < m["baselines"]["moving_average_28d_wape"]
    b = client.get("/api/insights").json()
    assert b["source"] == "rules" and b["headline"]


def test_patch_item_recomputes_risk(client):
    item = client.get("/api/items", params={"status": "stockout_risk"}).json()[0]
    r = client.patch(f"/api/items/{item['sku']}", json={"on_hand": 1_000_000})
    assert r.status_code == 200 and r.json()["status"] == "overstock"
    client.patch(f"/api/items/{item['sku']}", json={"on_hand": item["on_hand"]})
    assert client.patch(f"/api/items/{item['sku']}", json={"on_hand": -1}).status_code == 422


def test_upload_items_and_sales(client):
    r = client.post("/api/data/items", files={"file": ("items.csv", b"sku,name,category,unit_cost,lead_time_days,on_hand\nSKU-9999,New Thing,Snacks,2.5,5,100\n")})
    assert r.status_code == 200 and r.json()["inserted"] == 1

    r = client.post("/api/data/sales", params={"retrain": "false"}, files={"file": ("s.csv", b"sku,date,qty\nSKU-9999,2026-01-01,4\nSKU-9999,2026-01-02,6\n")})
    assert r.status_code == 200 and r.json() == {"rows": 2, "inserted": 2, "updated": 0, "warnings": [], "retrained": False, "run_id": None}

    # Re-uploading the same days updates instead of duplicating.
    r = client.post("/api/data/sales", params={"retrain": "false"}, files={"file": ("s.csv", b"sku,date,qty\nSKU-9999,2026-01-01,5\n")})
    assert r.json()["updated"] == 1 and r.json()["inserted"] == 0

    r = client.post("/api/data/sales", files={"file": ("s.csv", b"sku,date,qty\nNOPE,2026-01-01,1\n")})
    assert r.status_code == 422 and "Unknown SKU" in r.json()["detail"]["errors"][0]


def test_csv_templates(client):
    r = client.get("/api/data/templates/sales.csv")
    assert r.status_code == 200 and r.text.startswith("sku,date,qty")


def test_chat_requires_api_key(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_retrain_endpoint_creates_new_run(client):
    before = client.get("/api/model/metrics").json()["run_id"]
    r = client.post("/api/model/retrain")
    assert r.status_code == 200 and r.json()["run_id"] > before
