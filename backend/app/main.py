import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from .db import SessionLocal, init_db
from .services import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def _scheduled_retrain() -> None:
    with SessionLocal() as session:
        pipeline.retrain(session)


async def retrain_periodically(interval_hours: float) -> None:
    """Retrain on a fixed interval (e.g. nightly after the day's sales land)."""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            await asyncio.to_thread(_scheduled_retrain)
            log.info("Scheduled retrain complete")
        except pipeline.RetrainInProgress:
            log.info("Scheduled retrain skipped: another retrain is running")
        except Exception:
            log.exception("Scheduled retrain failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    if settings.seed_on_startup:
        with SessionLocal() as session:
            pipeline.seed_if_empty(session)
            if pipeline.latest_run(session) is None:
                pipeline.train_and_forecast(session)

    task = asyncio.create_task(retrain_periodically(settings.retrain_interval_hours)) if settings.retrain_interval_hours > 0 else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="AI Inventory Forecasting & Insights", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
