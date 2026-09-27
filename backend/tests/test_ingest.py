from datetime import date

import pytest

from app.services import alerts, ingest
from app.services.inventory import RiskStatus
from tests.test_insights import _risk


def test_parse_sales_sums_duplicates_and_normalizes_headers():
    raw = b" SKU , Date ,QTY\nA,2026-01-01,3\nA,2026-01-01,2\nB,2026-01-02,0\n"
    df = ingest.parse_sales(raw)
    assert df.to_dict("records") == [{"sku": "A", "date": date(2026, 1, 1), "qty": 5}, {"sku": "B", "date": date(2026, 1, 2), "qty": 0}]


def test_parse_sales_reports_row_errors():
    raw = b"sku,date,qty\nA,not-a-date,1\nA,2026-01-02,-3\nA,2026-01-03,1.5\n"
    with pytest.raises(ingest.ValidationFailed) as exc:
        ingest.parse_sales(raw)
    msgs = " | ".join(exc.value.errors)
    assert "Row 2" in msgs and "Row 3" in msgs and "Row 4" in msgs


def test_parse_rejects_missing_columns_and_empty():
    with pytest.raises(ingest.ValidationFailed, match="Missing required column"):
        ingest.parse_sales(b"sku,qty\nA,1\n")
    with pytest.raises(ingest.ValidationFailed):
        ingest.parse_sales(b"")


def test_parse_items_validates_types_and_duplicates():
    good = ingest.parse_items(ingest.items_template().encode())
    assert good.iloc[0]["lead_time_days"] == 7
    bad = b"sku,name,category,unit_cost,lead_time_days,on_hand\nX,a,b,-1,0,5\nX,a,b,1,3,2.5\n"
    with pytest.raises(ingest.ValidationFailed) as exc:
        ingest.parse_items(bad)
    msgs = " | ".join(exc.value.errors)
    assert "unit_cost" in msgs and "lead_time_days" in msgs and "on_hand" in msgs and "duplicate sku" in msgs


def test_alert_lists_only_stockouts(monkeypatch):
    risks = [_risk("C", RiskStatus.STOCKOUT), _risk("B", RiskStatus.OVERSTOCK, projected_stockout_date=None)]
    text = alerts.format_alert(risks)
    assert "1 item(s) at stockout risk" in text and "(C)" in text and "(B)" not in text
    assert alerts.format_alert([risks[1]]) is None

    sent = {}
    monkeypatch.setattr(alerts, "get_settings", lambda: type("S", (), {"alert_webhook_url": "https://hooks.example/x"})())
    monkeypatch.setattr(alerts.httpx, "post", lambda url, json, timeout: sent.update(url=url, json=json) or type("R", (), {"raise_for_status": lambda self: None})())
    assert alerts.send_stockout_alert(risks) is True
    assert sent["url"] == "https://hooks.example/x" and "stockout" in sent["json"]["text"]
