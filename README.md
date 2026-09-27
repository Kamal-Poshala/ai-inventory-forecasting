# StockSense — AI Inventory Forecasting & Insights

StockSense forecasts demand for each item, flags items at risk of **stocking out** or holding **excess stock**, and uses Claude to turn the numbers into a plain-language briefing for supply-chain decision-makers. You can also ask it questions in plain English ("If I order 800 units today, do I avoid a stockout?"), and a Claude agent answers by calling the app's own forecast and risk tools.

**Stack:** Python · FastAPI · scikit-learn · pandas · SQLAlchemy · PostgreSQL / SQLite · Anthropic Claude API (tool use, structured outputs) · React · TypeScript · TanStack Query · Tailwind CSS · Recharts · Docker · GitHub Actions

```
┌──────────────┐   /api    ┌──────────────────────────────── FastAPI ─────────────────────────────────┐
│ React + TS   │ ────────▶ │ routes ─▶ inventory risk engine ─┬─▶ AI briefing (Claude, JSON schema)   │
│ dashboard,   │           │              ▲                   └─▶ chat agent (Claude + 6 tools)       │
│ chat, import │           │   forecasts  │   train ▶ backtest ▶ calibrate ▶ forecast ▶ alert        │
└──────────────┘           │              └── global gradient-boosting model (p10 / p50 / p90)        │
                           │   CSV ingest ─▶ validate ─▶ upsert        nightly retrain scheduler       │
                           └──────────────────────────── PostgreSQL / SQLite ─────────────────────────┘
```

## What it does

| Layer | Details |
|---|---|
| **Forecasting** | A single *global* `HistGradientBoostingRegressor` is trained across all SKUs. It uses lag features, rolling means and standard deviations, calendar and seasonality terms, and item/category embeddings. It forecasts recursively over a 28-day horizon. A Poisson model gives the p50 forecast, and two quantile models give p10 and p90. |
| **Evaluation** | Every retrain backtests on a 28-day holdout and compares the model to seasonal-naive and 28-day moving-average baselines using WAPE, MAE, bias and interval coverage. On the demo data the model's error is about **13% lower than seasonal-naive** and **32% lower than the moving average**. |
| **Interval calibration** | Recursive forecasting makes the raw quantile band too narrow (about 68% coverage instead of 80%). The backtest finds a widening factor that restores 80% coverage, and that factor is applied to the production forecasts. |
| **Inventory risk** | Reorder point = lead-time demand + safety stock, where safety stock = z·√Σσ² over the lead time. Each item is marked *stockout risk* (runs out before an order could arrive), *reorder*, *overstock* (more than 90 days of cover) or *healthy*. The engine also recommends an order-up-to quantity. |
| **Chat agent** | "Ask your inventory" runs a tool-use loop with Claude. It has six read-only tools: portfolio summary, item search, item detail, category trends, model accuracy, and a **what-if order simulator**. Tool errors are passed back to the model so it can correct itself. The UI shows which tools were used for each answer. |
| **Data import** | CSV upload for item master data and daily sales. Each row is validated, and errors are reported by row number. Sales can be merged with existing data (upsert by SKU and date) or replace it entirely. The model retrains automatically after a sales upload. |
| **Operations** | Scheduled retraining (every 24 hours by default), Slack-compatible stockout alerts via webhook, inline stock-count updates that recompute risk immediately, and a lock that prevents two retrains running at once. |
| **GenAI briefing** | Claude receives a compact, pre-computed JSON context, so it never does arithmetic on raw data. Its reply is constrained to a JSON schema through structured outputs. Refusals are handled with server-side fallbacks, and each briefing is cached per model run. Without an API key, the app returns a rules-based briefing with the same structure. |

## Quick start

### Docker

```bash
cp backend/.env.example backend/.env   # optional: add ANTHROPIC_API_KEY
docker compose up --build
```

Open http://localhost:8080.

### Local development

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload                    # http://localhost:8000/docs

# frontend (Node 20+)
cd frontend
npm install && npm run dev                       # http://localhost:5173
```

On first start, the backend seeds two years of synthetic sales for 30 SKUs, trains the model and writes forecasts. Startup takes a few seconds. To start fresh with demo data ending yesterday, delete `backend/inventory.db`. To use your own data, click **Import data** and download the CSV templates.

The AI briefing and chat need `ANTHROPIC_API_KEY`. Without it, the dashboard still works fully, with a rules-based briefing.

### Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the AI briefing and chat |
| `LLM_MODEL` | `claude-opus-5` | Claude model |
| `DATABASE_URL` | SQLite file | For example `postgresql+psycopg://user:pass@host/db` |
| `RETRAIN_INTERVAL_HOURS` | `24` | Scheduled retraining; `0` disables it |
| `ALERT_WEBHOOK_URL` | — | Slack-compatible webhook for stockout alerts |

### Tests

```bash
cd backend && pytest -q
```

The tests cover:
- leakage-free features
- quantile ordering and calibration
- a check that the model beats the moving-average baseline
- inventory risk rules
- CSV validation
- webhook alerts
- the chat agent's tools and tool-use loop, with a mocked Claude client
- LLM output parsing, with a mocked Claude client
- the API end to end

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/summary` | Portfolio KPIs and risk counts |
| GET | `/api/items?status=&category=` | Per-item risk, sorted by severity |
| GET | `/api/items/{sku}` | History, forecast band and risk details |
| GET | `/api/model/metrics` | Backtest metrics against baselines |
| POST | `/api/model/retrain` | Retrain, backtest and re-forecast |
| GET | `/api/insights?refresh=` | AI briefing, cached per model run |
| POST | `/api/chat` | Ask a question; Claude answers using tools |
| PATCH | `/api/items/{sku}` | Update on-hand stock, lead time or unit cost |
| POST | `/api/data/items` | Upload the item CSV (upsert) |
| POST | `/api/data/sales?mode=append\|replace` | Upload the sales CSV, then retrain |
| GET | `/api/data/templates/{items,sales}.csv` | CSV templates |

Interactive API docs are served at `/docs`.

## Project structure

```
backend/app/
  ml/forecaster.py       global GBM, recursive forecasting, backtest, interval calibration
  ml/synthetic.py        demo data generator (trend, seasonality, promotions)
  services/inventory.py  reorder point, safety stock, risk classification
  services/insights.py   Claude briefing (structured output) + rules fallback
  services/agent.py      Claude tool-use agent + what-if simulator
  services/ingest.py     CSV validation and loading
  services/pipeline.py   train → backtest → forecast → alert
frontend/src/
  components/            dashboard, chart, chat panel, import dialog
```

## Possible extensions

- Promotions and price as forecast features
- An eval set that scores briefing and chat answer quality
- Authentication and multi-tenant inventories
- Streaming chat responses
