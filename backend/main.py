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
from backend.push_routes import router as push_router
from backend.profile_routes import router as profile_router
from backend.service import agent_service
from ai_agent.web_push import push_service_for
from ai_agent.chat_titles import ChatTitleService
from ai_agent.llm.registry import build_provider
from dataclasses import replace


configure_logging(ROOT)
logger = logging.getLogger("avelin.api")


async def _automation_scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            queued = await asyncio.to_thread(agent_service.tick_automations)
            if queued:
                logger.info("automation scheduler queued=%s", len(queued))
            await asyncio.to_thread(agent_service.process_automation_queue)
        except Exception:
            logger.exception("automation scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


async def _push_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(push_service_for(agent_service).deliver_pending)
        except Exception:
            # No endpoint URLs, subscription keys, payloads or upstream bodies.
            logger.error("Web Push delivery worker failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


async def _title_worker(stop: asyncio.Event) -> None:
    def provider_for(user_id):
        settings = replace(agent_service.settings_for_user(user_id), llm_timeout_seconds=12,
            llm_max_retries=0, reasoning_fallback_enabled=False)
        return build_provider(settings)
    while not stop.is_set():
        try:
            await asyncio.to_thread(ChatTitleService(agent_service.database).process_queue, provider_for, limit=1)
        except Exception:
            logger.error("Chat title worker failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=3)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    scheduler = asyncio.create_task(_automation_scheduler(stop))
    push_worker = asyncio.create_task(_push_worker(stop))
    title_worker = asyncio.create_task(_title_worker(stop))
    try:
        yield
    finally:
        stop.set()
        scheduler.cancel()
        push_worker.cancel()
        title_worker.cancel()
        for worker in (scheduler, push_worker, title_worker):
            with suppress(asyncio.CancelledError):
                await worker


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
app.include_router(push_router)
app.include_router(profile_router)


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
