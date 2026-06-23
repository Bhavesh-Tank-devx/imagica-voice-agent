"""FastAPI app factory for the ElevenLabs-native server.

Run:
    uvicorn src.elevenlabs_native.app:app --host 0.0.0.0 --port 8001 --reload
"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src.elevenlabs_native.router import router
from src.logging_setup import setup_logging
from src.persistence import init_db

logger = logging.getLogger("imagica-elevenlabs")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise logging and the database at startup."""
    setup_logging("elevenlabs")
    init_db()
    logger.info("ElevenLabs server started")
    yield


def create_app() -> FastAPI:
    """Build and configure the ElevenLabs-native FastAPI application."""
    app = FastAPI(title="Imagica ElevenLabs Webhook Server", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)
