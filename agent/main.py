"""ctrlplane-enhanced � CLI and FastAPI server entry point."""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.orchestrator import CtrlplaneOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ctrlplane-enhanced AI Agent",
    version="1.0.0",
    description="ML-powered CI/CD Intelligence Agent � risk scoring, auto-rollback, NL pipeline generation, resource scheduling",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ?? API Key authentication middleware ??????????????????????????????????????

API_KEY = os.getenv("CTRLPLANE_AGENT_API_KEY", "")

if not API_KEY:
    logger.warning(
        "CTRLPLANE_AGENT_API_KEY not set � API endpoints are open. "
        "Set this environment variable in production."
    )


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    # Health endpoint is always accessible
    if request.url.path == "/api/v1/agent/health":
        return await call_next(request)

    # If no API key configured, allow all (dev mode)
    if not API_KEY:
        return await call_next(request)

    # Check X-API-Key header
    provided_key = request.headers.get("X-API-Key", "")
    if provided_key != API_KEY:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key. Provide X-API-Key header."},
        )

    return await call_next(request)


# ?? Rate limiting ??????????????????????????????????????????????????????????

RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

_request_counts: dict[str, list[float]] = {}


def _check_rate_limit(client_id: str) -> bool:
    """Simple in-memory rate limiter. Returns True if request is allowed."""
    if not RATE_LIMIT_ENABLED:
        return True
    import time
    now = time.time()
    window = 60.0  # 1 minute
    if client_id not in _request_counts:
        _request_counts[client_id] = []
    # Remove timestamps older than window
    _request_counts[client_id] = [t for t in _request_counts[client_id] if now - t < window]
    if len(_request_counts[client_id]) >= RATE_LIMIT_PER_MINUTE:
        return False
    _request_counts[client_id].append(now)
    return True


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/api/v1/agent/health":
        return await call_next(request)

    client_id = request.headers.get("X-API-Key", request.client.host if request.client else "unknown")
    if not _check_rate_limit(client_id):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {RATE_LIMIT_PER_MINUTE} requests per minute."},
        )
    return await call_next(request)


# ?? Graceful shutdown ??????????????????????????????????????????????????????

_shutdown_event = asyncio.Event()


@app.on_event("startup")
async def startup_event():
    logger.info("ctrlplane-enhanced AI Agent starting up")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("ctrlplane-enhanced AI Agent shutting down gracefully...")
    orchestrator = _get_orchestrator_raw()
    if orchestrator is not None:
        memory = orchestrator._get_memory_raw()
        if memory is not None:
            try:
                memory.close()
                logger.info("Memory manager closed successfully")
            except Exception as e:
                logger.warning("Error closing memory manager: %s", e)
    _shutdown_event.set()


def _get_orchestrator_raw():
    global _orchestrator
    return _orchestrator


# ?? Orchestrator singleton ??????????????????????????????????????????????????

_orchestrator: CtrlplaneOrchestrator | None = None


def get_orchestrator() -> CtrlplaneOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CtrlplaneOrchestrator()
    return _orchestrator


# ?? Pydantic request/response models ???????????????????????????????????????

class RiskScoreRequest(BaseModel):
    deployment_id: str
    deployment_metadata: dict = {}


class RiskScoreResponse(BaseModel):
    deployment_id: str
    risk_score: float
    risk_tier: str
    explanation: str
    block_recommendation: bool
    contributing_features: dict


class RollbackCheckRequest(BaseModel):
    deployment_id: str
    services: list[str]
    prometheus_url: str = ""


class RollbackCheckResponse(BaseModel):
    deployment_id: str
    anomaly_detected: bool
    anomaly_score: float
    rollback_triggered: bool
    incident_summary: str


class PipelineGenerateRequest(BaseModel):
    description: str
    context: dict = {}


class PipelineGenerateResponse(BaseModel):
    pipeline_yaml: str
    explanation: str
    validation_passed: bool
    pipeline_id: str = ""


class ResourceScheduleRequest(BaseModel):
    pipeline_id: str
    pipeline_metadata: dict = {}


class ResourceScheduleResponse(BaseModel):
    predicted_duration_seconds: int
    predicted_cpu_cores: float
    predicted_memory_gb: float
    confidence_interval: list[float]
    recommendation: str


# ?? FastAPI endpoints ???????????????????????????????????????????????????????

@app.get("/api/v1/agent/health")
async def health():
    return {"status": "healthy", "version": "1.0.0", "upstream": "ctrlplane-v0.8.0"}


@app.post("/api/v1/agent/risk-score", response_model=RiskScoreResponse)
async def risk_score(request: RiskScoreRequest):
    try:
        result = await get_orchestrator().score_deployment(
            request.deployment_id, request.deployment_metadata
        )
        return result
    except Exception as e:
        logger.exception("Risk scoring failed for %s", request.deployment_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/agent/rollback-check", response_model=RollbackCheckResponse)
async def rollback_check(request: RollbackCheckRequest):
    try:
        result = await get_orchestrator().check_rollback(
            request.deployment_id, request.services, request.prometheus_url
        )
        return result
    except Exception as e:
        logger.exception("Rollback check failed for %s", request.deployment_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/agent/generate-pipeline", response_model=PipelineGenerateResponse)
async def generate_pipeline(request: PipelineGenerateRequest):
    try:
        result = await get_orchestrator().generate_pipeline(
            request.description, request.context
        )
        return result
    except Exception as e:
        logger.exception("Pipeline generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/agent/schedule-resources", response_model=ResourceScheduleResponse)
async def schedule_resources(request: ResourceScheduleRequest):
    try:
        result = await get_orchestrator().schedule_resources(
            request.pipeline_id, request.pipeline_metadata
        )
        return result
    except Exception as e:
        logger.exception("Resource scheduling failed for %s", request.pipeline_id)
        raise HTTPException(status_code=500, detail=str(e))


# ?? CLI ?????????????????????????????????????????????????????????????????????

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctrlplane-agent",
        description="ctrlplane-enhanced CI/CD Intelligence Agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # score
    p_score = sub.add_parser("score", help="Score deployment risk")
    p_score.add_argument("deployment_id", help="Ctrlplane deployment ID")
    p_score.add_argument("--metadata", default="{}", help="JSON deployment metadata")

    # rollback
    p_rb = sub.add_parser("rollback", help="Check golden signals and auto-rollback if anomaly")
    p_rb.add_argument("deployment_id", help="Ctrlplane deployment ID")
    p_rb.add_argument("--services", nargs="+", default=[], help="Affected service names")
    p_rb.add_argument("--prometheus-url", default="", help="Prometheus API base URL")

    # generate
    p_gen = sub.add_parser("generate", help="Generate pipeline YAML from description")
    p_gen.add_argument("description", help="Natural language pipeline description")
    p_gen.add_argument("--output", default="-", help="Output file path (- for stdout)")

    # schedule
    p_sched = sub.add_parser("schedule", help="Predict resource requirements for pipeline")
    p_sched.add_argument("pipeline_id", help="Ctrlplane pipeline ID")
    p_sched.add_argument("--metadata", default="{}", help="JSON pipeline metadata")

    # serve
    p_serve = sub.add_parser("serve", help="Start FastAPI server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.add_argument("--reload", action="store_true")

    # update-knowledge
    sub.add_parser("update-knowledge", help="Run research paper crawl immediately")

    return parser


async def run_cli(args: argparse.Namespace) -> None:
    orch = CtrlplaneOrchestrator()

    if args.command == "score":
        metadata = json.loads(args.metadata)
        result = await orch.score_deployment(args.deployment_id, metadata)
        print(json.dumps(result, indent=2))

    elif args.command == "rollback":
        result = await orch.check_rollback(
            args.deployment_id, args.services, args.prometheus_url
        )
        print(json.dumps(result, indent=2))

    elif args.command == "generate":
        result = await orch.generate_pipeline(args.description, {})
        yaml_output = result["pipeline_yaml"]
        if args.output == "-":
            print(yaml_output)
        else:
            Path(args.output).write_text(yaml_output)
            print(f"Pipeline YAML written to {args.output}")

    elif args.command == "schedule":
        metadata = json.loads(args.metadata)
        result = await orch.schedule_resources(args.pipeline_id, metadata)
        print(json.dumps(result, indent=2))

    elif args.command == "update-knowledge":
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        from knowledge_updater import KnowledgeUpdater
        updater = KnowledgeUpdater()
        added = await updater.run_update()
        print(f"Knowledge update complete: {added} new entries added.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        uvicorn.run(
            "agent.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    else:
        asyncio.run(run_cli(args))


if __name__ == "__main__":
    main()
