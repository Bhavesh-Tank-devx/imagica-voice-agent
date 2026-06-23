---
name: fastapi-ml-structure
description: >
  Apply this skill whenever writing, scaffolding, or reviewing code for the Ivana Jewels visual search
  FastAPI backend, or any production FastAPI + ML inference project with similar shape (SQS queues,
  multi-tier EC2, Qdrant vector DB, GPU embedding workers, Shopify webhooks). Covers: project layout,
  module responsibilities, naming rules, async/sync correctness for ML, Pydantic schema discipline,
  dependency injection patterns, SQS integration patterns, model lifecycle management, and the
  specific Ivana conventions from TDD v1.2. Use this skill before writing any new module, route,
  schema, service, or worker for this project — even for small tasks.
---

# FastAPI + ML Backend Structure Skill
## Ivana Jewels Visual Search — Production Conventions

Synthesized from: **TDD v1.2**, **zhanymkanov/fastapi-best-practices**, and production ML deployment patterns.

---

## 1. Repository Layout

```
ivana-visual-search/
├── src/                          # All application code lives here
│   ├── main.py                   # FastAPI app factory, lifespan, middleware, router mounts
│   ├── config.py                 # Global Config (pydantic BaseSettings)
│   ├── constants.py              # Global enums: Category, EmbedModel, SeverityLevel
│   ├── exceptions.py             # Global HTTP exception handlers
│   ├── dependencies.py           # Shared deps: get_qdrant_client, get_sqs_client, etc.
│   │
│   ├── search/                   # POST /search — customer visual search
│   │   ├── router.py
│   │   ├── schemas.py            # SearchRequest, SearchResponse, SearchResultItem
│   │   ├── service.py            # orchestrates quality_check → SQS enqueue → poll result
│   │   ├── dependencies.py       # validate_category, validate_file_size
│   │   ├── constants.py          # SCORE_THRESHOLD, MIN_RESULTS, TOP_K_DEFAULT
│   │   └── exceptions.py        # LowQualityImageError, SearchTimeoutError
│   │
│   ├── webhooks/                 # POST /webhooks/shopify/products
│   │   ├── router.py
│   │   ├── schemas.py            # ShopifyProductPayload, WebhookResponse
│   │   ├── service.py            # HMAC verify → SQS enqueue
│   │   ├── dependencies.py       # verify_shopify_hmac
│   │   ├── constants.py          # ALLOWED_TOPICS, WEBHOOK_SECRET_KEY
│   │   └── exceptions.py        # InvalidHMACError, UnknownTopicError
│   │
│   ├── quality/                  # quality_check.py logic, encapsulated
│   │   ├── checker.py            # QualityChecker class
│   │   ├── schemas.py            # QualityFlag, QualityReport
│   │   └── constants.py         # Blur/brightness/contrast/resolution thresholds
│   │
│   ├── segmentation/             # YOLO + SAM3 fallback
│   │   ├── yolo.py               # YOLOv12Segmenter class
│   │   ├── sam3.py               # SAM3Segmenter class (fallback, lazy-loaded)
│   │   ├── schemas.py            # CropResult, BoundingBox
│   │   └── constants.py         # SCORE_THRESHOLD, OVERLAP_THRESHOLD, INPUT_SIZE, MAX_ITEMS
│   │
│   ├── embedding/                # Qwen3 + ALIGN-base + model registry
│   │   ├── base.py               # BaseEmbedder ABC
│   │   ├── qwen.py               # Qwen3VLEmbedder
│   │   ├── align.py              # ALIGNBaseEmbedder
│   │   ├── registry.py           # get_embedder(model: EmbedModel) factory
│   │   ├── schemas.py            # EmbeddingVector, EmbedRequest
│   │   └── constants.py         # EMBED_DIM_MAP, INSTRUCTION_PREFIX, MAX_CROP_SIZE
│   │
│   ├── captioning/               # VLM captioner (ALIGN-base hybrid only)
│   │   ├── client.py             # HTTP client to localhost:8001
│   │   ├── schemas.py            # CaptionRequest, CaptionResponse
│   │   └── constants.py         # CAPTION_API_URL, CAPTION_TIMEOUT
│   │
│   ├── vector_store/             # Qdrant operations
│   │   ├── client.py             # QdrantClientWrapper (singleton)
│   │   ├── search.py             # search(), rrf_search()
│   │   ├── upsert.py             # upsert_product(), delete_product()
│   │   ├── schemas.py            # ProductPayload, VectorPoint, SearchCandidate
│   │   └── constants.py         # QDRANT_HOST, QDRANT_PORT, COLLECTION_SUFFIX_MAP
│   │
│   ├── sqs/                      # SQS client + message schemas
│   │   ├── client.py             # SQSClient wrapper (send, receive, delete)
│   │   ├── schemas.py            # SearchMessage, SyncMessage, DLQMessage
│   │   └── constants.py         # QUEUE_URLS, VISIBILITY_TIMEOUT, MAX_RETRIES
│   │
│   ├── workers/                  # Long-running background workers (run as separate ECS tasks)
│   │   ├── embedding_worker.py   # dequeue search SQS → YOLO → embed → Qdrant → deposit result
│   │   └── product_sync_worker.py# dequeue sync SQS → YOLO → embed → Qdrant upsert/delete
│   │
│   └── catalog/                  # Catalogue management (ingest pipeline)
│       ├── ingest.py             # Two-pass offline ingest (Pass1: YOLO crops, Pass2: embed)
│       ├── schemas.py            # CatalogProduct, IngestStats
│       └── utils.py             # sku_to_point_id(), get_embedded_skus()
│
├── tests/
│   ├── search/
│   ├── webhooks/
│   ├── quality/
│   ├── segmentation/
│   ├── embedding/
│   └── vector_store/
│
├── scripts/                      # One-off ops scripts (not part of the app)
│   ├── run_ingest.py
│   ├── validate_qdrant.py
│   └── benchmark_yolo.py
│
├── .github/
│   └── workflows/
│       ├── ci.yml                # lint + test on PR
│       └── deploy.yml            # build → ECR push → ECS rolling deploy
│
├── docker/
│   ├── Dockerfile.api            # c6i node: FastAPI + YOLO
│   ├── Dockerfile.worker         # g4dn node: embedding worker + Qdrant
│   └── docker-compose.dev.yml   # local dev stack
│
├── pyproject.toml                # ruff, black, mypy, pytest config
├── requirements/
│   ├── base.txt
│   ├── cpu.txt                   # c6i: opencv, ultralytics, openvino
│   └── gpu.txt                   # g4dn: torch+cuda, transformers, qdrant-client
├── .env.example
└── logging.ini
```

---

## 2. Module Responsibilities (What Lives Where)

This is the most important section. Wrong placement is the #1 source of spaghetti code.

| File | What goes here | What does NOT go here |
|---|---|---|
| `router.py` | HTTP route definitions, status codes, `Depends()` wiring | Business logic, ML inference calls |
| `service.py` | Business logic, orchestration between sub-components | Direct DB calls, HTTP calls, model inference |
| `schemas.py` | Pydantic request/response models only | DB models, ORM, constants |
| `dependencies.py` | FastAPI `Depends()` functions: auth, validation, resource injection | Business logic |
| `constants.py` | Module-scoped magic numbers and enums | Config read from env (use `config.py`) |
| `exceptions.py` | Domain-specific exception classes | Exception handlers (those go in `main.py` or global `exceptions.py`) |
| `client.py` | Thin wrapper around external service (SQS, Qdrant, caption API) | Business logic |

### The ML-specific split (critical)
The FastAPI app (`src/main.py`) runs on the **c6i node**. The embedding workers run as **separate ECS tasks** on the **g4dn node**. They share Python modules but they are NOT co-located processes. Never import `embedding/qwen.py` or load a CUDA model inside any code that runs in the FastAPI app.

```
# WRONG — loads 4 GB GPU model inside the API server process
from src.embedding.qwen import Qwen3VLEmbedder
embedder = Qwen3VLEmbedder()  # will OOM or fail on c6i (no GPU)

# RIGHT — API server enqueues to SQS; worker process does the model work
from src.sqs.client import SQSClient
await sqs.send(queue="search", message=SearchMessage(...))
```

---

## 3. The Async/Sync Rule for ML Workloads

This is where most FastAPI+ML code gets it wrong.

```
# Rule: if the work is CPU-bound (YOLO, embedding, image decode), it is SYNC.
# If it is I/O-bound (SQS, Qdrant, S3, HTTP), it is ASYNC.

# WRONG — blocks the entire event loop for ~30 ms
@router.post("/search")
async def search(image: UploadFile):
    report = quality_checker.check(await image.read())  # CPU work inside async

# CORRECT — delegate CPU work to threadpool
from fastapi.concurrency import run_in_threadpool

@router.post("/search")
async def search(image: UploadFile, service: SearchService = Depends(get_search_service)):
    image_bytes = await image.read()                        # async I/O — fine
    report = await run_in_threadpool(quality_checker.check, image_bytes)  # CPU → thread
    result = await service.enqueue_and_poll(image_bytes, ...)             # async SQS I/O
    return result
```

**Decision table:**

| Operation | Use |
|---|---|
| `image.read()` | `await` — async I/O |
| `quality_checker.check(bytes)` — OpenCV, numpy | `run_in_threadpool` |
| `yolo_segmenter.detect(image)` — OpenVINO INT8 | `run_in_threadpool` (CPU) |
| `sqs_client.send_message(...)` — boto3 async | `await` |
| `qdrant_client.search(...)` | `await` |
| `qwen_embedder.encode(crop)` — GPU inference | Runs in **separate worker process** — never in FastAPI |

---

## 4. Pydantic Schema Discipline

### Always use `response_model=` on routes
```python
# router.py
@router.post("/search", response_model=SearchResponse, status_code=200)
async def search_endpoint(
    image: UploadFile,
    category: Category,
    top_k: int = Query(default=10, ge=1, le=50),
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    ...
```

### Schema naming convention
```
SearchRequest       # inbound — what the client sends
SearchResponse      # outbound — what the API returns
SearchResultItem    # nested response model
QualityFlag         # sub-model
ProductPayload      # Qdrant-stored metadata shape
SearchMessage       # SQS message schema (internal)
ShopifyProductPayload  # external webhook body (map from Shopify docs)
```

### Global custom base model (in `src/models.py`)
```python
# src/models.py
from pydantic import BaseModel, ConfigDict

class AppModel(BaseModel):
    """Base model for all schemas in this project."""
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )
```

### Category enum lives in `src/constants.py`
```python
from enum import StrEnum

class Category(StrEnum):
    RING = "ring"
    EARRING = "earring"
    ALL = "all"

class EmbedModel(StrEnum):
    QWEN = "qwen"
    ALIGN_BASE = "align_base"
    MARQO_FASHION_CLIP = "marqo_fashion_clip"
```

---

## 5. Configuration Pattern

Split configs by domain. Never one giant `.env` reader.

```python
# src/config.py — global
from pydantic_settings import BaseSettings
from src.constants import EmbedModel, Environment

class Config(BaseSettings):
    ENVIRONMENT: Environment = Environment.PRODUCTION
    EMBED_MODEL: EmbedModel = EmbedModel.QWEN
    CORS_ORIGINS: list[str] = []
    LOG_LEVEL: str = "INFO"

settings = Config()

# src/sqs/config.py — SQS-specific
class SQSConfig(BaseSettings):
    AWS_REGION: str = "ap-south-1"
    SEARCH_QUEUE_URL: str
    SYNC_QUEUE_URL: str
    SEARCH_VISIBILITY_TIMEOUT: int = 120
    SYNC_VISIBILITY_TIMEOUT: int = 300

sqs_settings = SQSConfig()

# src/vector_store/config.py — Qdrant-specific  
class QdrantConfig(BaseSettings):
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    SCORE_THRESHOLD: float = 0.60
    MIN_RESULTS: int = 5

qdrant_settings = QdrantConfig()
```

---

## 6. Dependency Injection Patterns

### Singleton clients (loaded once at startup, not per-request)
```python
# src/dependencies.py
from functools import lru_cache
from qdrant_client import AsyncQdrantClient
from src.vector_store.config import qdrant_settings

@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    """Singleton — called once, cached for app lifetime."""
    return AsyncQdrantClient(
        host=qdrant_settings.QDRANT_HOST,
        port=qdrant_settings.QDRANT_PORT,
    )

# In router — injected via Depends, cached across requests
@router.post("/search")
async def search(
    qdrant: AsyncQdrantClient = Depends(get_qdrant_client),
):
    ...
```

### ML model lifecycle — use `lifespan`, not `@app.on_event`
```python
# src/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.segmentation.yolo import YOLOv12Segmenter

_yolo: YOLOv12Segmenter | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP — warm models once at container boot
    global _yolo
    _yolo = YOLOv12Segmenter()
    _yolo.warmup()
    yield
    # SHUTDOWN — clean up
    _yolo = None

app = FastAPI(lifespan=lifespan)

def get_yolo_segmenter() -> YOLOv12Segmenter:
    if _yolo is None:
        raise RuntimeError("YOLO model not initialised. Lifespan not run.")
    return _yolo
```

### Chained validation dependencies (webhooks example)
```python
# src/webhooks/dependencies.py
import hmac, hashlib
from fastapi import Header, Request, HTTPException

async def verify_shopify_hmac(
    request: Request,
    x_shopify_hmac_sha256: str = Header(...),
) -> None:
    """Chain this first in any Shopify webhook route."""
    body = await request.body()
    secret = webhooks_settings.SHOPIFY_WEBHOOK_SECRET.encode()
    expected = hmac.new(secret, body, hashlib.sha256).digest()
    import base64
    if not hmac.compare_digest(base64.b64encode(expected).decode(), x_shopify_hmac_sha256):
        raise InvalidHMACError()

async def valid_shopify_topic(
    x_shopify_topic: str = Header(...),
) -> str:
    if x_shopify_topic not in ALLOWED_TOPICS:
        raise UnknownTopicError(topic=x_shopify_topic)
    return x_shopify_topic

# src/webhooks/router.py
@router.post("/webhooks/shopify/products", response_model=WebhookResponse)
async def receive_product_webhook(
    payload: ShopifyProductPayload,
    _: None = Depends(verify_shopify_hmac),         # security first
    topic: str = Depends(valid_shopify_topic),       # then validation
    service: WebhookService = Depends(get_webhook_service),
):
    ...
```

---

## 7. SQS Integration Pattern

```python
# src/sqs/client.py
import boto3
import json
from src.sqs.config import sqs_settings

class SQSClient:
    def __init__(self):
        self._client = boto3.client("sqs", region_name=sqs_settings.AWS_REGION)

    async def send(self, queue_url: str, message: dict) -> str:
        """Fire-and-forget enqueue. Returns message_id."""
        from fastapi.concurrency import run_in_threadpool
        response = await run_in_threadpool(
            self._client.send_message,
            QueueUrl=queue_url,
            MessageBody=json.dumps(message),
        )
        return response["MessageId"]
```

**SQS message schema discipline** — every message type has a Pydantic schema:
```python
# src/sqs/schemas.py
import uuid
from src.models import AppModel
from src.constants import Category, EmbedModel

class SearchMessage(AppModel):
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    image_bytes_b64: str         # base64-encoded bytes
    category: Category
    top_k: int
    embed_model: EmbedModel

class SyncMessage(AppModel):
    event_type: str              # products/create | products/update | products/delete
    product_handle: str
    shopify_payload: dict
```

---

## 8. Ivana-Specific Constants (Never Hardcode These)

All tuning values live in `constants.py` of their owning module:

```python
# src/quality/constants.py
BLUR_LAPLACIAN_MIN = 7          # full image
BLUR_LAPLACIAN_MIN_CROP = 15   # crop
BRIGHTNESS_MIN = 35
BRIGHTNESS_MAX = 255
CONTRAST_STD_MIN = 9
RESOLUTION_MIN = (300, 240)    # (width, height)
MAX_FILE_MB = 10
CROP_MIN_AREA_RATIO = 0.006
CROP_MAX_ASPECT_RATIO = 15.0

# src/segmentation/constants.py
SCORE_THRESHOLD = 0.40
OVERLAP_THRESHOLD = 0.60
MAX_ITEMS = 7
INPUT_SIZE = 384                # optimal for c6i latency/accuracy tradeoff
PADDING_FACTOR = 0.10           # 10% bbox padding before crop

# src/embedding/constants.py
EMBED_DIM_MAP = {
    "qwen": 2048,
    "align_base": 640,
    "marqo_fashion_clip": 512,
    "marqo_ecomm_l": 768,
}
INSTRUCTION_PREFIX = "Represent this jewellery product image for visual similarity retrieval."
MAX_CROP_SIZE = 448

# src/vector_store/constants.py
SCORE_THRESHOLD = 0.60
MIN_RESULTS = 5
CANDIDATES_MULTIPLIER = 6       # fetch top_k × 6 then deduplicate
COLLECTION_SUFFIX = {
    "align_base": "_align",
    "marqo_fashion_clip": "_marqo",
}
```

---

## 9. `sku_to_point_id` — Lives in `catalog/utils.py`

```python
# src/catalog/utils.py
import hashlib

def sku_to_point_id(sku: str) -> int:
    """Deterministic SKU → Qdrant point ID. Ensures idempotent upserts.

    Args:
        sku: Product SKU string (e.g., 'RING-001').

    Returns:
        A non-negative int64 suitable as a Qdrant point ID.
    """
    return int(hashlib.sha1(sku.encode()).hexdigest(), 16) % (2**63)
```

This function is imported by both `catalog/ingest.py` and `workers/product_sync_worker.py`. It does not live in either — it lives in `catalog/utils.py` and both import from there.

---

## 10. Worker Entry Points

Workers are separate Python processes (separate ECS tasks). They share the `src/` package but are invoked via their own entry point:

```python
# src/workers/embedding_worker.py
"""
Entry point: python -m src.workers.embedding_worker
Runs on: g4dn.xlarge (GPU node)
"""
import asyncio
from src.sqs.client import SQSClient
from src.segmentation.yolo import YOLOv12Segmenter
from src.embedding.registry import get_embedder
from src.vector_store.search import search_similar
from src.config import settings

async def run():
    sqs = SQSClient()
    yolo = YOLOv12Segmenter()
    embedder = get_embedder(settings.EMBED_MODEL)  # loaded once
    embedder.warmup()

    while True:
        messages = await sqs.receive(queue_url=sqs_settings.SEARCH_QUEUE_URL)
        for msg in messages:
            await process_search_message(msg, yolo, embedder, sqs)

if __name__ == "__main__":
    asyncio.run(run())
```

---

## 11. main.py — What Goes Here

```python
# src/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.search.router import router as search_router
from src.webhooks.router import router as webhooks_router
from src.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm YOLO model (CPU node only)
    # NOTE: Never load GPU models here — this is the c6i API node
    from src.segmentation.yolo import get_yolo_segmenter
    get_yolo_segmenter()           # triggers model load + warmup
    yield

app = FastAPI(
    title="Ivana Visual Search API",
    version="1.2.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,   # NEVER ["*"] in production
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(search_router, tags=["search"])
app.include_router(webhooks_router, tags=["webhooks"])
app.mount("/static", StaticFiles(directory="data/images"), name="static")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

---

## 12. Naming Cheat Sheet

| Thing | Convention | Example |
|---|---|---|
| FastAPI router file | `router.py` always | `search/router.py` |
| Service class | `{Domain}Service` | `SearchService`, `WebhookService` |
| Pydantic schema | `{Domain}{Purpose}` | `SearchResponse`, `QualityFlag` |
| Dependency function | `get_{thing}` or `valid_{thing}` | `get_qdrant_client`, `verify_shopify_hmac` |
| ML model class | `{ModelName}Embedder` / `{Model}Segmenter` | `Qwen3VLEmbedder`, `YOLOv12Segmenter` |
| Constants | `UPPER_SNAKE_CASE` | `SCORE_THRESHOLD`, `MAX_ITEMS` |
| Worker module | `{name}_worker.py` | `embedding_worker.py` |
| SQS message schema | `{Domain}Message` | `SearchMessage`, `SyncMessage` |
| Collection name | `{category}` (default) or `{category}{_suffix}` | `ring`, `ring_align` |

---

## 13. What Never To Do (Anti-Patterns for This Project)

```python
# 1. NEVER load a GPU model in the FastAPI app process
from src.embedding.qwen import Qwen3VLEmbedder   # wrong on c6i — will crash

# 2. NEVER block the event loop with CPU work
async def search(...):
    cv2.imread(...)                # sync CPU I/O inside async — blocks event loop

# 3. NEVER hardcode thresholds inline
if score >= 0.60: ...              # use SCORE_THRESHOLD from constants

# 4. NEVER duplicate sku_to_point_id
# ingest.py and product_sync_worker.py must import from catalog/utils.py

# 5. NEVER allow_origins=["*"] in production config
# Must be the Shopify storefront origin only

# 6. NEVER use BackgroundTasks for ML inference
# BackgroundTasks is fire-and-forget with NO result channel — use SQS instead

# 7. NEVER call Qdrant directly from a router
# router → service → vector_store/search.py

# 8. NEVER skip HMAC verification on webhook routes
# Even in dev — verify_shopify_hmac must always be the first Depends()

# 9. NEVER use assert for runtime validation
# assert category in VALID_CATEGORIES    # disabled by -O flag in prod
# Use: if category not in VALID_CATEGORIES: raise ValueError(...)

# 10. NEVER put SAM3 on the import path in production containers
# SAM3 is lazy-loaded only. Its HuggingFace download happens on first use.
```

---

## 14. Quick Reference: Which Node Runs What

| Code | Node | Why |
|---|---|---|
| `src/main.py` | c6i.xlarge | FastAPI app |
| `src/quality/checker.py` | c6i | CPU OpenCV |
| `src/segmentation/yolo.py` | c6i | OpenVINO INT8 |
| `src/sqs/client.py` | Both | Shared |
| `src/workers/embedding_worker.py` | g4dn.xlarge | Needs T4 GPU |
| `src/workers/product_sync_worker.py` | g4dn | GPU embedding |
| `src/embedding/qwen.py` | g4dn | CUDA model |
| `src/embedding/align.py` | Either | CPU-only |
| `src/vector_store/` | g4dn | Co-located with Qdrant |
| `src/catalog/ingest.py` | SageMaker | Offline batch |
