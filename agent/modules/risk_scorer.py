"""
RiskScorer â€” XGBoost-based deployment risk scoring.

Predicts rollback probability (0â€“1) from 8 deployment features.
Bootstrap-trains on 500 synthetic samples; retrains incrementally from real outcomes.
Falls back to rule-based heuristics when < 50 real samples available.
"""

import logging
import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "deployment_size",
    "deploy_frequency",
    "time_since_last_rollback_hours",
    "error_rate_7d",
    "latency_p95_trend",
    "service_dependency_count",
    "test_coverage_pct",
    "config_change_size",
]

RISK_TIERS = {
    (0.0, 0.3): "low",
    (0.3, 0.6): "medium",
    (0.6, 0.8): "high",
    (0.8, 1.01): "critical",
}


class RiskScorer:
    def __init__(self, config: dict, memory) -> None:
        self.config = config
        self.memory = memory
        self.model_path = Path(config.get("model_path", "./models/risk_scorer.pkl"))
        self.risk_threshold = config.get("risk_threshold", 0.7)
        self._model = None
        self._sample_count = 0
        self._load_or_bootstrap()

    # â”€â”€ Public methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def extract_features(self, deployment_id: str, metadata: dict) -> dict[str, float]:
        """Extract 8 features from Ctrlplane metadata + memory history."""
        history = self.memory.get_deployment_history(deployment_id)

        rollback_history = history.get("rollbacks", [])
        if rollback_history:
            last_rollback_ts = rollback_history[-1].get("timestamp", "")
            try:
                last_dt = datetime.fromisoformat(last_rollback_ts)
                time_since_rollback = (datetime.utcnow() - last_dt).total_seconds() / 3600
            except ValueError:
                time_since_rollback = 168.0  # default 1 week
        else:
            time_since_rollback = 720.0  # no rollback history â†’ 30 days

        return {
            "deployment_size": float(metadata.get("service_count", 1)),
            "deploy_frequency": float(metadata.get("deploys_last_7d", 5)),
            "time_since_last_rollback_hours": time_since_rollback,
            "error_rate_7d": float(metadata.get("error_rate_7d", 0.01)),
            "latency_p95_trend": float(metadata.get("latency_p95_trend", 0.0)),
            "service_dependency_count": float(metadata.get("downstream_service_count", 2)),
            "test_coverage_pct": float(metadata.get("test_coverage_pct", 75.0)),
            "config_change_size": float(metadata.get("config_change_count", 0)),
        }

    def predict(self, features: dict[str, float]) -> tuple[float, str, dict]:
        """Return (risk_score, tier, feature_importances)."""
        x = np.array([[features[f] for f in FEATURE_NAMES]], dtype=np.float32)

        if self._sample_count < 50 or self._model is None:
            risk_score = self._heuristic_score(features)
            importances = {f: 1.0 / len(FEATURE_NAMES) for f in FEATURE_NAMES}
        else:
            prob = self._model.predict_proba(x)[0]
            risk_score = float(prob[1]) if len(prob) > 1 else float(prob[0])
            importances = dict(zip(FEATURE_NAMES, self._model.feature_importances_.tolist()))

        tier = next(
            label for (lo, hi), label in RISK_TIERS.items() if lo <= risk_score < hi
        )
        return risk_score, tier, importances

    def record_outcome(self, deployment_id: str, features: dict, rolled_back: bool) -> None:
        """Record actual outcome and retrain if enough new data accumulated."""
        self.memory.record_deployment_outcome(deployment_id, features, int(rolled_back))
        self._sample_count += 1
        if self._sample_count % 50 == 0:
            self._retrain()

    # â”€â”€ Private methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _load_or_bootstrap(self) -> None:
        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    saved = pickle.load(f)
                self._model = saved["model"]
                self._sample_count = saved["sample_count"]
                logger.info("Loaded risk scorer model (%d samples)", self._sample_count)
                return
            except Exception as e:
                logger.warning("Failed to load risk scorer model: %s", e)

        logger.info("Bootstrapping risk scorer with synthetic data...")
        X, y = self._generate_synthetic_data(500)
        self._train(X, y)
        self._sample_count = 500
        self._save_model()

    def _heuristic_score(self, features: dict) -> float:
        """Rule-based fallback when insufficient training data."""
        score = 0.0
        if features["deployment_size"] > 5:
            score += 0.15
        if features["deploy_frequency"] < 2:
            score += 0.10
        if features["time_since_last_rollback_hours"] < 24:
            score += 0.25
        if features["error_rate_7d"] > 0.05:
            score += 0.20
        if features["latency_p95_trend"] > 0.1:
            score += 0.15
        if features["service_dependency_count"] > 10:
            score += 0.10
        if features["test_coverage_pct"] < 60:
            score += 0.10
        if features["config_change_size"] > 20:
            score += 0.10
        return min(score, 1.0)

    def _train(self, X: np.ndarray, y: np.ndarray) -> None:
        try:
            from xgboost import XGBClassifier
            self._model = XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            )
            self._model.fit(X, y)
        except ImportError:
            logger.warning("xgboost not available; using sklearn GradientBoosting fallback")
            from sklearn.ensemble import GradientBoostingClassifier
            self._model = GradientBoostingClassifier(n_estimators=100, random_state=42)
            self._model.fit(X, y)

    def _retrain(self) -> None:
        rows = self.memory.get_all_deployment_outcomes()
        if len(rows) < 50:
            return
        X = np.array([[r[f] for f in FEATURE_NAMES] for r in rows], dtype=np.float32)
        y = np.array([r["rolled_back"] for r in rows])
        self._train(X, y)
        self._save_model()
        logger.info("Risk scorer retrained on %d samples", len(rows))

    def _save_model(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({"model": self._model, "sample_count": self._sample_count}, f)

    @staticmethod
    def _generate_synthetic_data(n: int) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.RandomState(42)
        X = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float32)
        X[:, 0] = rng.randint(1, 15, n).astype(float)           # deployment_size
        X[:, 1] = rng.poisson(7, n).astype(float)               # deploy_frequency
        X[:, 2] = rng.exponential(200, n)                       # time_since_last_rollback
        X[:, 3] = rng.beta(2, 50, n)                            # error_rate_7d
        X[:, 4] = rng.normal(0, 0.05, n)                        # latency_p95_trend
        X[:, 5] = rng.randint(1, 20, n).astype(float)           # service_dependency_count
        X[:, 6] = rng.uniform(40, 100, n)                       # test_coverage_pct
        X[:, 7] = rng.randint(0, 30, n).astype(float)           # config_change_size

        # Synthetic labels: high deployment_size + low coverage + recent rollback â†’ more likely
        logit = (
            0.3 * (X[:, 0] / 15)
            + 0.25 * (X[:, 2] < 24).astype(float)
            + 0.2 * (X[:, 3] / 0.1)
            - 0.15 * (X[:, 6] / 100)
            + 0.1 * (X[:, 5] / 20)
            - 0.5
        )
        prob = 1 / (1 + np.exp(-logit))
        y = (rng.uniform(0, 1, n) < prob).astype(int)
        return X, y

