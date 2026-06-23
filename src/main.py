"""FastAPI app factory for the live webhook server (ElevenLabs + Twilio).

Receives cart-abandonment / lead events, dials customers via Twilio, and bridges
audio to ElevenLabs Conversational AI over a WebSocket media stream.

Run:
    uvicorn src.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src.dashboard import router as dashboard_router
from src.logging_setup import setup_logging
from src.persistence import init_db
from src.queue_worker import queue_worker
from src.telephony.router import router as telephony_router
from src.webhooks import router as webhooks_router

logger = logging.getLogger("imagica-webhook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise logging and DB, then run the background queue worker."""
    setup_logging("webhook")
    init_db()
    logger.info("Imagica webhook server started (ElevenLabs + Twilio mode)")
    worker_task = asyncio.create_task(queue_worker())
    try:
        yield
    finally:
        worker_task.cancel()
        logger.info("Imagica webhook server shutting down")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(title="Imagica Voice Agent Webhook", lifespan=lifespan)
    app.include_router(webhooks_router, tags=["webhooks"])
    app.include_router(telephony_router, tags=["telephony"])
    app.include_router(dashboard_router, tags=["dashboard"])
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
