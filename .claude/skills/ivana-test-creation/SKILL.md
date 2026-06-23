---
name: ivana-test-creation
description: >
  Apply this skill whenever writing, reviewing, or scaffolding tests for the Ivana Jewels visual
  search backend — or any production FastAPI + ML project with the same shape (SQS, Qdrant, GPU
  workers, Shopify webhooks, YOLO/embedding inference). Covers: pytest conventions, what to test
  per module (quality check, segmentation, embedding, vector search, search service, webhooks),
  how to mock GPU models and AWS services, async test setup, CI/CD with GitHub Actions, and the
  `make check` quality gate. Use this skill before writing any test file — even a single test
  function — to ensure consistent structure, correct mock strategy, and coverage of the
  Ivana-specific logic called out in TDD v1.2 (thresholds, min-5 fallback, SKU dedup, HMAC).
---

# Test Creation Skill — Ivana Jewels Visual Search
## Production pytest + CI/CD Conventions

Synthesized from: **TDD v1.2**, **zhanymkanov/fastapi-best-practices**, **pytest docs**, and
production ML testing patterns.

---

## 1. Test Directory Layout

Mirror `src/` exactly. One test file per source module.

```
tests/
├── conftest.py                   # shared fixtures: app client, sample images, mock models
├── fixtures/
│   ├── images/
│   │   ├── sharp_ring.jpg        # good quality, passes all checks
│   │   ├── blurry_ring.jpg       # Laplacian < 7 → blur error
│   │   ├── dark_ring.jpg         # mean brightness < 35 → brightness warning
│   │   ├── tiny_ring.jpg         # < 300×240 px → resolution error
│   │   ├── large_file.bin        # > 10 MB → file size error
│   │   └── no_jewellery.jpg      # YOLO returns 0 detections → fallback crop
│   └── payloads/
│       ├── shopify_product.json  # sample Shopify webhook body
│       └── qdrant_results.json   # canned Qdrant search response
│
├── quality/
│   └── test_checker.py
├── segmentation/
│   └── test_yolo.py
├── embedding/
│   └── test_registry.py
├── vector_store/
│   ├── test_search.py
│   └── test_upsert.py
├── search/
│   ├── test_service.py
│   └── test_router.py
├── webhooks/
│   ├── test_service.py
│   └── test_router.py
├── catalog/
│   └── test_utils.py
└── sqs/
    └── test_client.py
```

---

## 2. conftest.py — Shared Fixtures

```python
# tests/conftest.py
import base64
import hashlib
import hmac
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

FIXTURES = Path(__file__).parent / "fixtures" / "images"


# ── App client ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create app with ML models mocked — never load real GPU models in tests."""
    with (
        patch("src.segmentation.yolo.YOLOv12Segmenter", autospec=True),
        patch("src.segmentation.yolo.get_yolo_segmenter") as mock_yolo,
    ):
        mock_yolo.return_value = MagicMock()
        from src.main import app as fastapi_app
        yield fastapi_app


@pytest.fixture(scope="session")
def client(app) -> TestClient:
    """Sync test client for simple route tests."""
    return TestClient(app)


@pytest.fixture
async def async_client(app) -> AsyncClient:
    """Async client for routes that require async context."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Sample images ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def sharp_ring_bytes() -> bytes:
    return (FIXTURES / "sharp_ring.jpg").read_bytes()


@pytest.fixture(scope="session")
def blurry_ring_bytes() -> bytes:
    return (FIXTURES / "blurry_ring.jpg").read_bytes()


@pytest.fixture(scope="session")
def dark_ring_bytes() -> bytes:
    return (FIXTURES / "dark_ring.jpg").read_bytes()


@pytest.fixture(scope="session")
def tiny_ring_bytes() -> bytes:
    return (FIXTURES / "tiny_ring.jpg").read_bytes()


# ── Shopify HMAC helper ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def shopify_secret() -> str:
    return "test_shopify_secret_abc123"


def make_shopify_hmac(body: bytes, secret: str) -> str:
    """Compute the correct X-Shopify-Hmac-Sha256 header for test requests."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@pytest.fixture
def shopify_product_payload() -> dict:
    import json
    return json.loads(
        (Path(__file__).parent / "fixtures" / "payloads" / "shopify_product.json")
        .read_text()
    )


# ── Qdrant mock ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_qdrant_client():
    with patch("src.dependencies.get_qdrant_client") as mock:
        client = AsyncMock()
        mock.return_value = client
        yield client


# ── SQS mock ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_sqs_client():
    with patch("src.sqs.client.SQSClient") as mock:
        sqs = AsyncMock()
        sqs.send.return_value = "test-message-id-123"
        mock.return_value = sqs
        yield sqs
```

---

## 3. Quality Checker Tests

These are purely CPU/algorithmic — no mocks needed. Test every threshold branch.

```python
# tests/quality/test_checker.py
"""
Coverage targets:
  - Each error threshold returns the correct flag code + severity
  - Good image returns empty flags
  - Crop quality thresholds (area ratio, blur, aspect ratio)
"""
import io
import numpy as np
import pytest
from PIL import Image

from src.quality.checker import QualityChecker
from src.quality.constants import (
    BLUR_LAPLACIAN_MIN,
    BRIGHTNESS_MIN,
    CONTRAST_STD_MIN,
    RESOLUTION_MIN,
    MAX_FILE_MB,
    CROP_MIN_AREA_RATIO,
    CROP_MAX_ASPECT_RATIO,
    BLUR_LAPLACIAN_MIN_CROP,
)


def make_image_bytes(
    width: int = 400,
    height: int = 400,
    mode: str = "RGB",
    color: tuple = (128, 128, 128),
    blur_sigma: float = 0.0,
) -> bytes:
    """Programmatically generate test image bytes."""
    img = Image.new(mode, (width, height), color)
    if blur_sigma > 0:
        import cv2
        arr = np.array(img)
        k = int(blur_sigma * 6) | 1  # must be odd
        arr = cv2.GaussianBlur(arr, (k, k), blur_sigma)
        img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def checker() -> QualityChecker:
    return QualityChecker()


class TestFullImageChecks:
    def test_good_image_has_no_errors(self, checker, sharp_ring_bytes):
        report = checker.check(sharp_ring_bytes)
        errors = [f for f in report.flags if f.severity == "error"]
        assert errors == []

    def test_blur_returns_error_flag(self, checker):
        blurry = make_image_bytes(blur_sigma=15.0)
        report = checker.check(blurry)
        codes = [f.code for f in report.flags]
        assert "BLUR" in codes
        blur_flag = next(f for f in report.flags if f.code == "BLUR")
        assert blur_flag.severity == "error"

    def test_low_brightness_returns_warning(self, checker):
        dark = make_image_bytes(color=(10, 10, 10))
        report = checker.check(dark)
        codes = [f.code for f in report.flags]
        assert "BRIGHTNESS" in codes
        flag = next(f for f in report.flags if f.code == "BRIGHTNESS")
        assert flag.severity == "warning"

    def test_low_resolution_returns_error(self, checker):
        tiny = make_image_bytes(width=100, height=100)
        report = checker.check(tiny)
        codes = [f.code for f in report.flags]
        assert "RESOLUTION" in codes

    def test_oversized_file_returns_error(self, checker):
        # 11 MB of bytes — no need to be a valid image for file-size check
        big_bytes = b"x" * (11 * 1024 * 1024)
        report = checker.check(big_bytes)
        codes = [f.code for f in report.flags]
        assert "FILE_SIZE" in codes

    def test_multiple_flags_can_coexist(self, checker):
        """Blurry AND dark image should return both flags."""
        bad = make_image_bytes(blur_sigma=15.0, color=(10, 10, 10))
        report = checker.check(bad)
        codes = [f.code for f in report.flags]
        assert "BLUR" in codes
        assert "BRIGHTNESS" in codes

    @pytest.mark.parametrize("width,height", [
        (300, 240),   # exactly at limit — should pass
        (299, 240),   # one pixel under — should fail
        (300, 239),   # one pixel under height — should fail
    ])
    def test_resolution_boundary_conditions(self, checker, width, height):
        img_bytes = make_image_bytes(width=width, height=height)
        report = checker.check(img_bytes)
        codes = [f.code for f in report.flags]
        if width < RESOLUTION_MIN[0] or height < RESOLUTION_MIN[1]:
            assert "RESOLUTION" in codes
        else:
            assert "RESOLUTION" not in codes


class TestCropQualityChecks:
    def test_tiny_crop_relative_area_returns_error(self, checker, sharp_ring_bytes):
        """Crop covering < 0.006 of image area should return CROP_AREA error."""
        from PIL import Image
        import io
        full_img = Image.open(io.BytesIO(sharp_ring_bytes))
        w, h = full_img.size
        # Crop a 1×1 pixel region — clearly below 0.006
        tiny_crop = full_img.crop((0, 0, 1, 1))
        buf = io.BytesIO()
        tiny_crop.save(buf, format="JPEG")
        result = checker.check_crop(
            crop_bytes=buf.getvalue(),
            original_size=(w, h),
        )
        codes = [f.code for f in result.flags]
        assert "CROP_AREA" in codes

    def test_extreme_aspect_ratio_returns_error(self, checker):
        """Crop with aspect ratio > 15:1 should return CROP_ASPECT error."""
        # 160×10 → ratio = 16 > 15
        sliver = make_image_bytes(width=160, height=10)
        result = checker.check_crop(sliver, original_size=(400, 400))
        codes = [f.code for f in result.flags]
        assert "CROP_ASPECT" in codes
```

---

## 4. Vector Store / Search Logic Tests

These cover the most critical TDD-specified business rules.

```python
# tests/vector_store/test_search.py
"""
TDD §4.5, §10.1 business rules under test:
  1. Score threshold = 0.60: results below are filtered out
  2. Min-5 fallback: if fewer than 5 pass threshold, return top-5 regardless
  3. Deduplicate by product_handle: keep highest-score image per product
  4. Return top_k sorted by score descending
"""
import pytest
from unittest.mock import AsyncMock, patch
from src.vector_store.search import apply_search_filters
from src.vector_store.schemas import SearchCandidate
from src.vector_store.constants import SCORE_THRESHOLD, MIN_RESULTS


def make_candidates(scores_and_handles: list[tuple[float, str]]) -> list[SearchCandidate]:
    """Build a list of SearchCandidate from (score, product_handle) tuples."""
    return [
        SearchCandidate(
            score=score,
            sku=f"SKU-{i:03d}",
            product_handle=handle,
            name=f"Product {i}",
            category="ring",
            image_url=f"/static/{i}.jpg",
            price=10000,
        )
        for i, (score, handle) in enumerate(scores_and_handles)
    ]


class TestScoreThreshold:
    def test_results_below_threshold_are_filtered(self):
        candidates = make_candidates([
            (0.92, "ring-a"),
            (0.75, "ring-b"),
            (0.59, "ring-c"),   # just below 0.60 → must be filtered
            (0.40, "ring-d"),
        ])
        results = apply_search_filters(candidates, top_k=10)
        scores = [r.score for r in results]
        assert all(s >= SCORE_THRESHOLD for s in scores)
        assert len(results) == 2

    def test_results_at_exact_threshold_are_kept(self):
        """Boundary: 0.60 exactly must pass, not be filtered."""
        candidates = make_candidates([
            (0.60, "ring-a"),
            (0.59, "ring-b"),
        ])
        results = apply_search_filters(candidates, top_k=10)
        assert len(results) == 1
        assert results[0].score == 0.60


class TestMinFiveFallback:
    """TDD §4.5: if fewer than 5 results meet threshold, return top-5 regardless."""

    def test_fewer_than_5_above_threshold_triggers_fallback(self):
        candidates = make_candidates([
            (0.92, "ring-a"),
            (0.80, "ring-b"),
            (0.75, "ring-c"),
            (0.30, "ring-d"),   # below threshold
            (0.25, "ring-e"),   # below threshold
            (0.20, "ring-f"),   # below threshold
        ])
        results = apply_search_filters(candidates, top_k=10)
        # Only 3 pass threshold → fallback kicks in → returns 5
        assert len(results) == MIN_RESULTS

    def test_exactly_5_above_threshold_does_not_trigger_fallback(self):
        candidates = make_candidates([
            (0.92, f"ring-{i}") for i in range(5)
        ] + [(0.30, "ring-low")])
        results = apply_search_filters(candidates, top_k=10)
        assert len(results) == 5
        assert all(r.score >= SCORE_THRESHOLD for r in results)

    def test_fallback_preserves_score_order(self):
        """Fallback results must still be sorted by score descending."""
        candidates = make_candidates([
            (0.92, "ring-a"),
            (0.50, "ring-b"),   # fallback
            (0.40, "ring-c"),   # fallback
            (0.30, "ring-d"),   # fallback
            (0.20, "ring-e"),   # fallback
        ])
        results = apply_search_filters(candidates, top_k=10)
        assert len(results) == MIN_RESULTS
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_candidates_returns_empty(self):
        results = apply_search_filters([], top_k=10)
        assert results == []


class TestDeduplicateByProductHandle:
    """TDD §4.5: keep highest-scoring image per product_handle."""

    def test_duplicates_by_handle_are_collapsed(self):
        candidates = make_candidates([
            (0.92, "ring-solitaire"),
            (0.85, "ring-solitaire"),   # same product, lower score — must be dropped
            (0.80, "ring-halo"),
            (0.70, "ring-halo"),        # same product, lower score — must be dropped
        ])
        results = apply_search_filters(candidates, top_k=10)
        handles = [r.product_handle for r in results]
        assert len(handles) == len(set(handles))   # no duplicates

    def test_highest_score_per_handle_is_kept(self):
        candidates = make_candidates([
            (0.85, "ring-solitaire"),
            (0.92, "ring-solitaire"),   # same product, higher score — must be KEPT
        ])
        results = apply_search_filters(candidates, top_k=10)
        assert len(results) == 1
        assert results[0].score == 0.92

    def test_top_k_applied_after_deduplicate(self):
        """top_k is the final cap, applied after dedup and threshold."""
        candidates = make_candidates([
            (0.90 - i * 0.01, f"ring-{i}") for i in range(20)
        ])
        results = apply_search_filters(candidates, top_k=5)
        assert len(results) == 5


class TestResultOrdering:
    def test_results_sorted_by_score_descending(self):
        candidates = make_candidates([
            (0.70, "ring-a"),
            (0.92, "ring-b"),
            (0.85, "ring-c"),
        ])
        results = apply_search_filters(candidates, top_k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
```

---

## 5. Catalog Utility Tests

```python
# tests/catalog/test_utils.py
"""
sku_to_point_id:
  - Deterministic: same SKU always → same int
  - Unique: different SKUs → different ints (no collision for known SKUs)
  - Range: output is a valid int64 (0 to 2^63 - 1)
  - Idempotent: re-ingesting same SKU overwrites in Qdrant (property test)
"""
import pytest
from src.catalog.utils import sku_to_point_id

KNOWN_SKUS = [
    "RING-001", "RING-002", "EARRING-100",
    "EARRING-101", "RING-999", "EARR-001-GOLD",
]


class TestSkuToPointId:
    def test_deterministic(self):
        """Same input must always produce same output."""
        assert sku_to_point_id("RING-001") == sku_to_point_id("RING-001")

    def test_different_skus_produce_different_ids(self):
        ids = [sku_to_point_id(sku) for sku in KNOWN_SKUS]
        assert len(ids) == len(set(ids)), "Hash collision among known SKUs"

    def test_output_is_valid_int64_range(self):
        for sku in KNOWN_SKUS:
            point_id = sku_to_point_id(sku)
            assert isinstance(point_id, int)
            assert 0 <= point_id < 2**63

    @pytest.mark.parametrize("sku", KNOWN_SKUS)
    def test_idempotent_on_repeated_calls(self, sku):
        ids = [sku_to_point_id(sku) for _ in range(5)]
        assert len(set(ids)) == 1

    def test_empty_string_does_not_crash(self):
        result = sku_to_point_id("")
        assert isinstance(result, int)

    def test_unicode_sku_does_not_crash(self):
        result = sku_to_point_id("अंगूठी-001")
        assert isinstance(result, int)
```

---

## 6. Webhook Router Tests — HMAC & Topic Validation

```python
# tests/webhooks/test_router.py
"""
Security-critical tests:
  - Valid HMAC → 200 + queued
  - Invalid HMAC → 401
  - Unknown topic → 400
  - Shopify 5-second contract: endpoint returns immediately (async SQS)
"""
import hashlib
import hmac
import base64
import json
import pytest
from fastapi.testclient import TestClient


WEBHOOK_BODY = json.dumps({
    "id": 12345678,
    "handle": "diamond-solitaire-ring",
    "title": "Diamond Solitaire Ring",
    "variants": [{"sku": "RING-001"}],
    "images": [{"src": "https://cdn.shopify.com/test.jpg"}],
}).encode()


def _hmac_header(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class TestWebhookHMAC:
    def test_valid_hmac_returns_200(self, client, mock_sqs_client, shopify_secret, monkeypatch):
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", shopify_secret)
        hmac_header = _hmac_header(WEBHOOK_BODY, shopify_secret)
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Hmac-Sha256": hmac_header,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    def test_invalid_hmac_returns_401(self, client, shopify_secret, monkeypatch):
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", shopify_secret)
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Hmac-Sha256": "aGVsbG8gd29ybGQ=",   # wrong
            },
        )
        assert response.status_code == 401

    def test_missing_hmac_header_returns_422(self, client):
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "products/create",
                # no X-Shopify-Hmac-Sha256
            },
        )
        assert response.status_code == 422   # FastAPI missing header


class TestWebhookTopicValidation:
    def test_unknown_topic_returns_400(self, client, shopify_secret, monkeypatch):
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", shopify_secret)
        hmac_header = _hmac_header(WEBHOOK_BODY, shopify_secret)
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",   # not in allowlist
                "X-Shopify-Hmac-Sha256": hmac_header,
            },
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("topic", [
        "products/create",
        "products/update",
        "products/delete",
    ])
    def test_all_allowed_topics_return_200(self, client, mock_sqs_client, shopify_secret, monkeypatch, topic):
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", shopify_secret)
        hmac_header = _hmac_header(WEBHOOK_BODY, shopify_secret)
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": topic,
                "X-Shopify-Hmac-Sha256": hmac_header,
            },
        )
        assert response.status_code == 200


class TestWebhookResponseContract:
    def test_endpoint_returns_message_id_in_response(self, client, mock_sqs_client, shopify_secret, monkeypatch):
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", shopify_secret)
        hmac_header = _hmac_header(WEBHOOK_BODY, shopify_secret)
        response = client.post(
            "/webhooks/shopify/products",
            content=WEBHOOK_BODY,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Hmac-Sha256": hmac_header,
            },
        )
        body = response.json()
        assert "message_id" in body
        assert body["message_id"] == "test-message-id-123"
```

---

## 7. Search Router Tests

```python
# tests/search/test_router.py
"""
Route-level tests — mock everything below the router:
  - Bad category → 400
  - File too large → 422 / 400
  - Quality error image → 200 with empty items + quality_flags with errors
  - Good image → 200 with items
  - Response schema matches SearchResponse
"""
import io
import pytest
from unittest.mock import patch, AsyncMock
from PIL import Image


def make_jpeg_bytes(width=400, height=400, color=(128, 128, 128)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


class TestSearchRouteValidation:
    def test_unknown_category_returns_400(self, client):
        response = client.post(
            "/search",
            data={"category": "necklace"},   # not in allowlist
            files={"image": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 400

    def test_missing_image_returns_422(self, client):
        response = client.post("/search", data={"category": "ring"})
        assert response.status_code == 422

    def test_non_image_content_type_returns_415(self, client):
        response = client.post(
            "/search",
            data={"category": "ring"},
            files={"image": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert response.status_code == 415


class TestSearchQualityGate:
    def test_quality_error_returns_empty_items(self, client):
        """If quality check has errors, items must be [] and search is not performed."""
        from src.quality.schemas import QualityFlag, QualityReport
        mock_report = QualityReport(flags=[
            QualityFlag(code="BLUR", severity="error", message="Image too blurry")
        ])
        with patch("src.search.service.SearchService.check_quality", return_value=mock_report):
            response = client.post(
                "/search",
                data={"category": "ring"},
                files={"image": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert any(f["severity"] == "error" for f in body["quality_flags"])

    def test_quality_warning_does_not_block_search(self, client, mock_sqs_client):
        """Warnings should not prevent search — only errors do."""
        from src.quality.schemas import QualityFlag, QualityReport
        mock_report = QualityReport(flags=[
            QualityFlag(code="BRIGHTNESS", severity="warning", message="Dark image")
        ])
        with (
            patch("src.search.service.SearchService.check_quality", return_value=mock_report),
            patch("src.search.service.SearchService.enqueue_and_poll", new_callable=AsyncMock,
                  return_value={"items": [], "latency_ms": 100}),
        ):
            response = client.post(
                "/search",
                data={"category": "ring"},
                files={"image": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
            )
        assert response.status_code == 200


class TestSearchResponseSchema:
    def test_response_has_required_fields(self, client, mock_sqs_client):
        with (
            patch("src.search.service.SearchService.check_quality") as mock_quality,
            patch("src.search.service.SearchService.enqueue_and_poll", new_callable=AsyncMock) as mock_search,
        ):
            from src.quality.schemas import QualityReport
            mock_quality.return_value = QualityReport(flags=[])
            mock_search.return_value = {"items": [], "latency_ms": 250}

            response = client.post(
                "/search",
                data={"category": "ring"},
                files={"image": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
            )
        body = response.json()
        assert "quality_flags" in body
        assert "items" in body
        assert "latency_ms" in body
```

---

## 8. Mocking Strategy Reference

The most critical decision in ML testing is **what to mock and at what boundary**.

```
Layer          | Mock strategy
──────────────────────────────────────────────────────────────────────
GPU models     | Always mock. Never load Qwen3/SAM3/ALIGN in tests.
               | Use: patch("src.embedding.qwen.Qwen3VLEmbedder")
               | Return a MagicMock with .encode() returning a fake vector.
──────────────────────────────────────────────────────────────────────
YOLO           | Mock at the class level in conftest (session-scoped).
               | Unit-test segmentation logic (NMS, padding, fallback)
               | with small synthetic numpy arrays, not real model.
──────────────────────────────────────────────────────────────────────
Qdrant         | Mock get_qdrant_client() Depends. Return AsyncMock
               | with .search() returning canned qdrant_results.json.
               | Never connect to a real Qdrant in unit tests.
──────────────────────────────────────────────────────────────────────
SQS (boto3)    | Mock SQSClient.send() to return fake message_id.
               | Never hit real AWS in unit tests.
               | For integration tests use localstack or moto.
──────────────────────────────────────────────────────────────────────
OpenCV/PIL     | Do NOT mock. Use programmatically generated images.
               | make_image_bytes() in conftest creates controlled inputs.
──────────────────────────────────────────────────────────────────────
QualityChecker | Do NOT mock in quality tests — it's pure logic.
               | Mock it in router/service tests that call it.
──────────────────────────────────────────────────────────────────────
apply_search_  | Do NOT mock. It's pure Python — test it directly.
filters()      | This is the most important business logic to test.
──────────────────────────────────────────────────────────────────────
```

---

## 9. Fake Embedding Vectors

```python
# tests/helpers.py — import in any test that needs fake vectors
import numpy as np


def fake_embedding(dim: int = 2048, seed: int = 42) -> list[float]:
    """Reproducible unit-length fake embedding vector."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


def fake_qwen_embedding(seed: int = 42) -> list[float]:
    return fake_embedding(dim=2048, seed=seed)


def fake_align_embedding(seed: int = 42) -> list[float]:
    return fake_embedding(dim=640, seed=seed)
```

---

## 10. pyproject.toml — Full Tool Config

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "integration: tests that hit real AWS/Qdrant (skipped in CI)",
    "gpu: tests requiring a real GPU model (skipped in CI)",
    "slow: tests taking > 1s",
]

[tool.coverage.run]
source = ["src"]
omit = ["src/workers/*", "scripts/*"]   # workers tested separately

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
    "\\.\\.\\.",
]

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "UP", "B", "SIM"]
ignore = ["E501"]

[tool.ruff.per-file-ignores]
"tests/*" = ["S101"]   # allow assert in tests

[tool.black]
line-length = 88

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
exclude = ["tests/", "scripts/"]
```

---

## 11. Makefile — `make check`

```makefile
# Makefile
.PHONY: check fmt lint type test ci

fmt:
	black src/ tests/
	ruff check --fix src/ tests/

lint:
	ruff check src/ tests/

type:
	mypy src/

test:
	pytest tests/ -m "not integration and not gpu"

test-all:
	pytest tests/

coverage:
	pytest tests/ -m "not integration and not gpu" --cov=src --cov-report=term-missing

check: fmt lint type test   ## Run all quality gates — use this before every commit
	@echo "✅ All checks passed."

ci: lint type test          ## CI-safe check (no autofix, fails on any error)
```

---

## 12. GitHub Actions CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  quality-gate:
    name: Lint + Type + Test
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: |
          pip install -r requirements/base.txt
          pip install -r requirements/dev.txt

      - name: Lint (ruff)
        run: ruff check src/ tests/

      - name: Type check (mypy)
        run: mypy src/

      - name: Run tests
        run: pytest tests/ -m "not integration and not gpu" --tb=short

      - name: Coverage report
        run: |
          pytest tests/ -m "not integration and not gpu" \
            --cov=src --cov-report=xml --cov-fail-under=80

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml
          fail_ci_if_error: false   # non-blocking for now; make true after baseline

  docker-build:
    name: Docker build check
    runs-on: ubuntu-latest
    needs: quality-gate

    steps:
      - uses: actions/checkout@v4

      - name: Build API image (c6i node)
        run: docker build -f docker/Dockerfile.api -t ivana-api:ci .

      - name: Build worker image (g4dn node)
        run: docker build -f docker/Dockerfile.worker -t ivana-worker:ci .
```

---

## 13. Branch Protection Setup (GitHub UI)

After the CI workflow is merged to `main`, configure in GitHub → Settings → Branches:

```
Branch name pattern: main

Protection rules:
  ✅ Require status checks to pass before merging
     → Required checks: "Lint + Type + Test"
  ✅ Require branches to be up to date before merging
  ✅ Require linear history
  ✅ Do not allow bypassing the above settings (includes admins)
```

This is what makes CI feel real and not optional — nobody can merge broken code, including you.

---

## 14. Test Markers — How to Skip GPU/Integration in CI

```python
# Mark slow/heavy tests so CI skips them
import pytest

@pytest.mark.gpu
def test_qwen_real_embedding():
    """Requires g4dn.xlarge. Run locally with: pytest -m gpu"""
    ...

@pytest.mark.integration
def test_qdrant_real_search():
    """Requires running Qdrant. Run with: pytest -m integration"""
    ...

@pytest.mark.slow
def test_full_ingest_pipeline():
    """End-to-end ingest — takes ~60s."""
    ...
```

Run specific groups:
```bash
pytest -m "not integration and not gpu"    # CI default
pytest -m integration                       # integration suite
pytest -m gpu                               # GPU tests (on g4dn only)
pytest tests/quality/ -v                   # one module
```

---

## 15. Coverage Targets Per Module

| Module | Target | Why |
|---|---|---|
| `quality/checker.py` | 95% | Pure logic, all branches testable |
| `catalog/utils.py` | 100% | Two functions, 0 external deps |
| `vector_store/search.py` (filters) | 100% | Core business logic from TDD |
| `webhooks/router.py` | 90% | Security-critical — every auth path |
| `search/router.py` | 85% | Route contracts |
| `search/service.py` | 80% | Orchestration, SQS mocked |
| `embedding/` | 70% | Real models mocked; test registry logic |
| `workers/` | 60% | Integration-heavy; test message parsing |
| `sqs/client.py` | 60% | Thin boto3 wrapper |
