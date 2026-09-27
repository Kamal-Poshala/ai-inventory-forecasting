"""Stockout alerts posted to a Slack-compatible incoming webhook after each retrain."""

import logging

import httpx

from ..config import get_settings
from .inventory import ItemRisk, RiskStatus, sort_key

log = logging.getLogger(__name__)


def format_alert(risks: list[ItemRisk], limit: int = 10) -> str | None:
    urgent = sorted((r for r in risks if r.status == RiskStatus.STOCKOUT), key=sort_key)
    if not urgent:
        return None
    lines = [f":rotating_light: *{len(urgent)} item(s) at stockout risk* (will run out before a new order can arrive)"]
    for r in urgent[:limit]:
        lines.append(
            f"• *{r.name}* ({r.sku}): {r.on_hand} on hand, runs out {r.projected_stockout_date}, "
            f"lead time {r.lead_time_days}d, order {r.recommended_order_qty} units"
        )
    if len(urgent) > limit:
        lines.append(f"…and {len(urgent) - limit} more")
    return "\n".join(lines)


def send_stockout_alert(risks: list[ItemRisk]) -> bool:
    url = get_settings().alert_webhook_url
    text = format_alert(risks)
    if not url or not text:
        return False
    try:
        httpx.post(url, json={"text": text}, timeout=10).raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("Stockout alert failed: %s", exc)
        return False
