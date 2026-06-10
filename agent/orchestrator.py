"""CtrlplaneOrchestrator — core agent decision loop for ctrlplane-enhanced."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


class CtrlplaneOrchestrator:
    """
    Central coordinator that routes requests to specialized modules.
    Modules are lazily initialized on first use to keep startup fast.
    """

    def __init__(self, config_path: str = "") -> None:
        self._config = self._load_config(config_path)
        self._risk_scorer = None
        self._auto_rollback = None
        self._nl_generator = None
        self._resource_scheduler = None
        self._memory = None
        self._llm = None
        self._hf = None

    # ── Lazy module accessors ────────────────────────────────────────────────

    def _get_memory(self):
        if self._memory is None:
            from agent.memory.memory_manager import MemoryManager
            self._memory = MemoryManager(
                db_path=self._config.get("memory", {}).get("db_path", "./ctrlplane_memory.db")
            )
        return self._memory

    def _get_llm(self):
        if self._llm is None:
            from tools.llm_client import LLMClient
            self._llm = LLMClient(
                primary=self._config.get("llm", {}).get("primary", "claude"),
                fallback=self._config.get("llm", {}).get("fallback", "openai"),
                offline=self._config.get("llm", {}).get("offline", "ollama"),
            )
        return self._llm

    def _get_hf(self):
        if self._hf is None:
            from tools.hf_model_manager import HFModelManager
            self._hf = HFModelManager(
                cache_dir=self._config.get("hf", {}).get("cache_dir", "./models")
            )
        return self._hf

    def _get_risk_scorer(self):
        if self._risk_scorer is None:
            from agent.modules.risk_scorer import RiskScorer
            self._risk_scorer = RiskScorer(
                config=self._config.get("risk_scorer", {}),
                memory=self._get_memory(),
            )
        return self._risk_scorer

    def _get_auto_rollback(self):
        if self._auto_rollback is None:
            from agent.modules.auto_rollback import AutoRollback
            self._auto_rollback = AutoRollback(
                config=self._config.get("auto_rollback", {}),
                memory=self._get_memory(),
                llm=self._get_llm(),
            )
        return self._auto_rollback

    def _get_nl_generator(self):
        if self._nl_generator is None:
            from agent.modules.nl_pipeline_generator import NLPipelineGenerator
            self._nl_generator = NLPipelineGenerator(
                config=self._config.get("nl_pipeline", {}),
                memory=self._get_memory(),
                llm=self._get_llm(),
                hf=self._get_hf(),
            )
        return self._nl_generator

    def _get_resource_scheduler(self):
        if self._resource_scheduler is None:
            from agent.modules.resource_scheduler import ResourceScheduler
            self._resource_scheduler = ResourceScheduler(
                config=self._config.get("resource_scheduler", {}),
                memory=self._get_memory(),
            )
        return self._resource_scheduler

    # ── Public API ───────────────────────────────────────────────────────────

    async def score_deployment(
        self, deployment_id: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract features → XGBoost risk score → LLM explanation if high risk."""
        logger.info("Scoring deployment %s", deployment_id)
        scorer = self._get_risk_scorer()

        features = await asyncio.get_event_loop().run_in_executor(
            None, scorer.extract_features, deployment_id, metadata
        )
        risk_score, tier, importances = await asyncio.get_event_loop().run_in_executor(
            None, scorer.predict, features
        )

        explanation = ""
        if risk_score >= self._config.get("risk_scorer", {}).get("risk_threshold", 0.7):
            explanation = await self._explain_risk(risk_score, importances)

        result = {
            "deployment_id": deployment_id,
            "risk_score": round(risk_score, 4),
            "risk_tier": tier,
            "explanation": explanation,
            "block_recommendation": risk_score >= 0.7,
            "contributing_features": importances,
        }

        self._get_memory().record_risk_assessment(deployment_id, features, risk_score)
        return result

    async def check_rollback(
        self, deployment_id: str, services: list[str], prometheus_url: str
    ) -> dict[str, Any]:
        """Query Prometheus golden signals → anomaly detection → rollback if triggered."""
        logger.info("Checking rollback eligibility for deployment %s", deployment_id)
        rb = self._get_auto_rollback()

        result = await asyncio.get_event_loop().run_in_executor(
            None, rb.evaluate, deployment_id, services, prometheus_url
        )
        return result

    async def generate_pipeline(
        self, description: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """NL description → template retrieval → LLM YAML generation → validation."""
        logger.info("Generating pipeline for: %s", description[:80])
        generator = self._get_nl_generator()

        result = await asyncio.get_event_loop().run_in_executor(
            None, generator.generate, description, context
        )
        return result

    async def schedule_resources(
        self, pipeline_id: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract pipeline features → XGBoost predictions for duration/CPU/memory."""
        logger.info("Predicting resources for pipeline %s", pipeline_id)
        scheduler = self._get_resource_scheduler()

        result = await asyncio.get_event_loop().run_in_executor(
            None, scheduler.predict, pipeline_id, metadata
        )
        return result

    async def monitor_deployment(
        self,
        deployment_id: str,
        services: list[str],
        prometheus_url: str,
        interval_seconds: int = 30,
        max_duration_seconds: int = 3600,
    ) -> None:
        """Async monitoring loop: check anomalies every interval_seconds until max_duration."""
        logger.info("Starting monitoring loop for deployment %s", deployment_id)
        elapsed = 0
        while elapsed < max_duration_seconds:
            result = await self.check_rollback(deployment_id, services, prometheus_url)
            if result.get("rollback_triggered"):
                logger.warning("Rollback triggered for deployment %s", deployment_id)
                break
            await asyncio.sleep(interval_seconds)
            elapsed += interval_seconds
        logger.info("Monitoring complete for deployment %s", deployment_id)

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _explain_risk(self, risk_score: float, importances: dict) -> str:
        feature_list = "\n".join(
            f"  - {k}: importance={v:.3f}" for k, v in sorted(importances.items(), key=lambda x: -x[1])[:5]
        )
        prompt = (
            f"A deployment was scored {risk_score:.0%} probability of rollback.\n"
            f"Contributing factors (by importance):\n{feature_list}\n\n"
            "Write 3 sentences:\n"
            "1. What is the main risk driver?\n"
            "2. Why does this factor increase rollback probability?\n"
            "3. What should the team do before deploying?"
        )
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._get_llm().complete, prompt, 300
            )
        except Exception as e:
            logger.warning("LLM explanation failed: %s", e)
            top_feature = max(importances, key=importances.get) if importances else "unknown"
            return f"High risk ({risk_score:.0%}) driven primarily by {top_feature}. Review carefully before deploying."

    def _get_memory_raw(self):
        """Return memory instance or None - used by shutdown handler."""
        return self._memory

    @staticmethod
    def _load_config(config_path: str) -> dict:
        if not config_path:
            config_path = str(Path(__file__).parent.parent / "config" / "agent_config.yaml")
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}
