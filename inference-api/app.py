import time
import random
import threading
from fastapi import FastAPI, Request
from pydantic import BaseModel
from prometheus_client import Histogram, Counter, Gauge, generate_latest, REGISTRY
from starlette.responses import Response

app = FastAPI(title="AI Inference API")

LATENCY_HIST = Histogram(
    "inference_latency_seconds",
    "Inference latency in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0),
)
REQUESTS_TOTAL = Counter("inference_requests_total", "Total inference requests", ["status"])
ERRORS_TOTAL = Counter("inference_errors_total", "Total inference errors", ["error_type"])
INFLIGHT = Gauge("inference_requests_in_flight", "Requests currently being processed")

_latency_override_ms = None
_lock = threading.Lock()

class ChatRequest(BaseModel):
    prompt: str
    model: str = "llama3.1"

class LatencyConfig(BaseModel):
    latency_ms: float

class DebugSetLatency(BaseModel):
    latency_ms: float = 0

@app.on_event("startup")
async def startup():
    INFLIGHT.set(0)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "inference-api"}

@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(REGISTRY), media_type="text/plain")

@app.post("/v1/chat")
async def chat(req: ChatRequest):
    INFLIGHT.inc()
    try:
        with _lock:
            base_latency = _latency_override_ms if _latency_override_ms is not None else random.uniform(50, 300)
        actual_latency = base_latency + random.uniform(-10, 10)
        if actual_latency < 10:
            actual_latency = 10
        time.sleep(actual_latency / 1000.0)
        latency_seconds = actual_latency / 1000.0
        LATENCY_HIST.observe(latency_seconds, exemplar={"model": req.model})
        if latency_seconds > 3:
            ERRORS_TOTAL.labels(error_type="timeout").inc()
            REQUESTS_TOTAL.labels(status="error").inc()
            return {"error": "inference timeout", "latency_s": latency_seconds}
        if random.random() < 0.01:
            ERRORS_TOTAL.labels(error_type="internal").inc()
            REQUESTS_TOTAL.labels(status="error").inc()
            return {"error": "internal server error"}
        REQUESTS_TOTAL.labels(status="success").inc()
        return {
            "id": "cmpl-" + hex(random.randint(0, 2**32))[2:],
            "object": "chat.completion",
            "model": req.model,
            "choices": [{"text": "Hello! I am an AI assistant.", "index": 0}],
            "usage": {"prompt_tokens": len(req.prompt.split()), "completion_tokens": 10},
            "latency_s": latency_seconds,
        }
    finally:
        INFLIGHT.dec()

@app.post("/debug/set-latency")
async def set_latency(cfg: DebugSetLatency):
    global _latency_override_ms
    with _lock:
        _latency_override_ms = cfg.latency_ms
    return {"latency_override_ms": _latency_override_ms}

@app.post("/debug/reset-latency")
async def reset_latency():
    global _latency_override_ms
    with _lock:
        _latency_override_ms = None
    return {"latency_override_ms": None}
