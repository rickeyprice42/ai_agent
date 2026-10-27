from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter
import asyncio
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.logging_config import configure_logging
from backend.routes import router
from backend.service import agent_service


configure_logging(ROOT)
logger = logging.getLogger("avelin.api")


async def _automation_scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            queued = await asyncio.to_thread(agent_service.tick_automations)
            if queued:
                logger.info("automation scheduler queued=%s", len(queued))
        except Exception:
            logger.exception("automation scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    scheduler = asyncio.create_task(_automation_scheduler(stop))
    try:
        yield
    finally:
        stop.set()
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler


app = FastAPI(title="Avelin API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "tauri://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    started_at = perf_counter()
    method = request.method
    path = request.url.path

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (perf_counter() - started_at) * 1000
        logger.exception(
            "request failed method=%s path=%s duration_ms=%.2f",
            method,
            path,
            duration_ms,
        )
        raise

    duration_ms = (perf_counter() - started_at) * 1000
    logger.info(
        "request completed method=%s path=%s status=%s duration_ms=%.2f",
        method,
        path,
        response.status_code,
        duration_ms,
    )
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
