"""
AutoRollback — Isolation forest anomaly detection on Prometheus golden signals.

Monitors 6 golden signals post-deployment. Triggers Ctrlplane rollback API
when anomaly score < threshold. Generates LLM incident summary.
"""

import logging
import os
import time
from typing import Any

import numpy as np
import requests

logger = logging.getLogger(__name__)

GOLDEN_SIGNALS = [
    "http_requests_error_rate",
    "http_request_duration_p95_seconds",
    "http_request_duration_p99_seconds",
    "container_cpu_usage_ratio",
    "container_memory_usage_ratio",
    "http_requests_per_second",
]

PROMETHEUS_QUERIES = {
    "http_requests_error_rate": 'sum(rate(http_requests_total{{status=~"5..",job=~"{service}"}}[5m])) / sum(rate(http_requests_total{{job=~"{service}"}}[5m]))',
    "http_request_duration_p95_seconds": 'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{job=~"{service}"}}[5m])) by (le))',
    "http_request_duration_p99_seconds": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{job=~"{service}"}}[5m])) by (le))',
    "container_cpu_usage_ratio": 'sum(rate(container_cpu_usage_seconds_total{{container=~"{service}"}}[5m])) / sum(kube_pod_container_resource_limits{{resource="cpu",container=~"{service}"}})',
    "container_memory_usage_ratio": 'sum(container_memory_working_set_bytes{{container=~"{service}"}}) / sum(kube_pod_container_resource_limits{{resource="memory",container=~"{service}"}})',
    "http_requests_per_second": 'sum(rate(http_requests_total{{job=~"{service}"}}[5m]))',
}


class AutoRollback:
    def __init__(self, config: dict, memory, llm) -> None:
        self.config = config
        self.memory = memory
        self.llm = llm
        self.contamination = config.get("contamination", 0.05)
        self.anomaly_threshold = config.get("anomaly_threshold", -0.3)
        self.max_rollback_age_seconds = config.get("max_rollback_age_seconds", 3600)
        self.ctrlplane_url = os.getenv("CTRLPLANE_URL", "http://localhost:3000")
        self.ctrlplane_api_key = os.getenv("CTRLPLANE_API_KEY", "")
        self._model = None
        self._baseline_fitted = False
        self._load_or_init_model()

    # ── Public methods ───────────────────────────────────────────────────────

    def evaluate(
        self, deployment_id: str, services: list[str], prometheus_url: str
    ) -> dict[str, Any]:
        """Query Prometheus → anomaly detection → rollback if triggered."""
        metrics = self._query_prometheus(services, prometheus_url)
        if metrics is None:
            return self._no_data_response(deployment_id)

        signal_vector = np.array([[metrics.get(s, 0.0) for s in GOLDEN_SIGNALS]])

        if not self._baseline_fitted:
            self._update_baseline(signal_vector)
            return {
                "deployment_id": deployment_id,
                "anomaly_detected": False,
                "anomaly_score": 0.0,
                "rollback_triggered": False,
                "incident_summary": "Learning mode: collecting baseline metrics.",
            }

        anomaly_score = self._model.score_samples(signal_vector)[0]
        anomaly_detected = anomaly_score < self.anomaly_threshold

        rollback_triggered = False
        incident_summary = ""

        if anomaly_detected:
            logger.warning(
                "Anomaly detected for deployment %s (score=%.3f)", deployment_id, anomaly_score
            )
            deployment_age = self._get_deployment_age(deployment_id)
            if deployment_age is not None and deployment_age < self.max_rollback_age_seconds:
                rollback_triggered = self._trigger_rollback(deployment_id)
                if rollback_triggered:
                    incident_summary = self._generate_incident_summary(
                        deployment_id, metrics, anomaly_score
                    )
                    self.memory.record_rollback(deployment_id, metrics, anomaly_score)

        self._update_baseline(signal_vector)

        return {
            "deployment_id": deployment_id,
            "anomaly_detected": anomaly_detected,
            "anomaly_score": round(float(anomaly_score), 4),
            "rollback_triggered": rollback_triggered,
            "incident_summary": incident_summary,
        }

    def fit_baseline(self, historical_metrics: list[dict]) -> None:
        """Fit isolation forest on 30-day rolling baseline metrics."""
        if len(historical_metrics) < 100:
            logger.info("Insufficient baseline samples (%d < 100)", len(historical_metrics))
            return
        X = np.array([[m.get(s, 0.0) for s in GOLDEN_SIGNALS] for m in historical_metrics])
        self._fit_model(X)
        self._baseline_fitted = True
        logger.info("Isolation forest fitted on %d baseline samples", len(X))

    # ── Private methods ──────────────────────────────────────────────────────

    def _load_or_init_model(self) -> None:
        from sklearn.ensemble import IsolationForest
        self._model = IsolationForest(
            contamination=self.contamination,
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
        )
        baseline = self.memory.get_metric_baseline()
        if len(baseline) >= 100:
            self.fit_baseline(baseline)

    def _fit_model(self, X: np.ndarray) -> None:
        self._model.fit(X)
        self._baseline_fitted = True

    def _update_baseline(self, signal_vector: np.ndarray) -> None:
        """Add new data point and refit if enough new data (every 200 points)."""
        row = {GOLDEN_SIGNALS[i]: float(signal_vector[0, i]) for i in range(len(GOLDEN_SIGNALS))}
        self.memory.record_metric_sample(row)
        baseline = self.memory.get_metric_baseline()
        if len(baseline) >= 200 and len(baseline) % 200 == 0:
            X = np.array([[m.get(s, 0.0) for s in GOLDEN_SIGNALS] for m in baseline])
            self._fit_model(X)

    def _query_prometheus(self, services: list[str], prometheus_url: str) -> dict | None:
        if not prometheus_url:
            prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        service_regex = "|".join(services) if services else ".*"
        metrics = {}
        try:
            for signal, query_template in PROMETHEUS_QUERIES.items():
                query = query_template.format(service=service_regex)
                response = requests.get(
                    f"{prometheus_url}/api/v1/query",
                    params={"query": query},
                    timeout=5,
                )
                if response.ok:
                    data = response.json().get("data", {}).get("result", [])
                    if data:
                        metrics[signal] = float(data[0]["value"][1])
                    else:
                        metrics[signal] = 0.0
                else:
                    metrics[signal] = 0.0
            return metrics
        except Exception as e:
            logger.warning("Prometheus query failed: %s", e)
            return self._mock_metrics()

    @staticmethod
    def _mock_metrics() -> dict:
        """Return zeroed metrics when Prometheus is unavailable."""
        return {s: 0.0 for s in GOLDEN_SIGNALS}

    def _trigger_rollback(self, deployment_id: str) -> bool:
        try:
            response = requests.post(
                f"{self.ctrlplane_url}/api/v1/deployments/{deployment_id}/rollback",
                headers={"X-API-Key": self.ctrlplane_api_key},
                timeout=10,
            )
            if response.ok:
                logger.info("Rollback triggered for deployment %s", deployment_id)
                return True
            logger.error("Rollback API returned %d for %s", response.status_code, deployment_id)
            return False
        except Exception as e:
            logger.error("Rollback trigger failed: %s", e)
            return False

    def _generate_incident_summary(
        self, deployment_id: str, metrics: dict, anomaly_score: float
    ) -> str:
        anomalous = [
            f"{k}: {v:.4f}"
            for k, v in sorted(metrics.items(), key=lambda x: -abs(x[1]))[:3]
        ]
        prompt = (
            f"A deployment {deployment_id} was auto-rolled-back due to golden signal anomalies.\n"
            f"Anomaly score: {anomaly_score:.3f} (threshold: {self.anomaly_threshold})\n"
            f"Top anomalous signals: {', '.join(anomalous)}\n\n"
            "Write a 3-sentence incident summary for the on-call runbook:\n"
            "1. What happened and when?\n"
            "2. Which signals triggered the rollback?\n"
            "3. What should the on-call engineer verify next?"
        )
        try:
            return self.llm.complete(prompt, max_tokens=200)
        except Exception as e:
            logger.warning("LLM incident summary failed: %s", e)
            return (
                f"Auto-rollback triggered for deployment {deployment_id}. "
                f"Anomaly score {anomaly_score:.3f}. "
                f"Top signals: {', '.join(anomalous)}. Check deployment logs and Prometheus dashboard."
            )

    def _get_deployment_age(self, deployment_id: str) -> float | None:
        info = self.memory.get_deployment_start_time(deployment_id)
        if info is None:
            return None
        return time.time() - info

    @staticmethod
    def _no_data_response(deployment_id: str) -> dict:
        return {
            "deployment_id": deployment_id,
            "anomaly_detected": False,
            "anomaly_score": 0.0,
            "rollback_triggered": False,
            "incident_summary": "No metrics data available.",
        }
