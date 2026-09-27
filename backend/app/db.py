from collections.abc import Iterator
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    sku: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), index=True)
    unit_cost: Mapped[float] = mapped_column(Float)
    lead_time_days: Mapped[int] = mapped_column(Integer)
    on_hand: Mapped[int] = mapped_column(Integer)


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(ForeignKey("items.sku"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[int] = mapped_column(Integer)


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("items.sku"), index=True)
    date: Mapped[date] = mapped_column(Date)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    metrics: Mapped[dict] = mapped_column(JSON)


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
