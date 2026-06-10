"""
ResourceScheduler — XGBoost regression for predicting build duration and compute.

Predicts build duration (seconds), CPU cores, and memory (GB) from 9 pipeline features.
Bootstrap-trains on 500 synthetic samples; retrains from actual build outcomes.
"""

import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DURATION_FEATURES = [
    "pipeline_step_count",
    "service_count",
    "artifact_size_mb",
    "time_of_day_hour",
    "day_of_week",
    "historical_duration_mean",
    "historical_duration_std",
    "queue_depth",
    "environment_criticality",
]


class ResourceScheduler:
    def __init__(self, config: dict, memory) -> None:
        self.config = config
        self.memory = memory
        self.model_path = Path(config.get("model_path", "./models/resource_scheduler.pkl"))
        self._duration_model = None
        self._cpu_model = None
        self._memory_model = None
        self._sample_count = 0
        self._load_or_bootstrap()

    # ── Public methods ───────────────────────────────────────────────────────

    def predict(self, pipeline_id: str, metadata: dict) -> dict[str, Any]:
        """Return resource predictions for a pipeline."""
        features = self._extract_features(pipeline_id, metadata)
        x = np.array([[features[f] for f in DURATION_FEATURES]], dtype=np.float32)

        duration = self._predict_target(self._duration_model, x, default=300.0)
        cpu = self._predict_target(self._cpu_model, x, default=2.0)
        mem = self._predict_target(self._memory_model, x, default=4.0)

        # 95% CI: ±20% of prediction (heuristic; improves as real data accumulates)
        ci = [round(duration * 0.8, 1), round(duration * 1.2, 1)]

        recommendation = self._build_recommendation(duration, cpu, mem, features)

        return {
            "predicted_duration_seconds": int(duration),
            "predicted_cpu_cores": round(cpu, 2),
            "predicted_memory_gb": round(mem, 2),
            "confidence_interval": ci,
            "recommendation": recommendation,
        }

    def record_outcome(
        self, pipeline_id: str, features: dict, actual_duration: float, actual_cpu: float, actual_memory: float
    ) -> None:
        """Record actual build outcome and trigger retraining every 50 samples."""
        self.memory.record_build_outcome(pipeline_id, features, actual_duration, actual_cpu, actual_memory)
        self._sample_count += 1
        if self._sample_count % 50 == 0:
            self._retrain()

    # ── Private methods ──────────────────────────────────────────────────────

    def _extract_features(self, pipeline_id: str, metadata: dict) -> dict[str, float]:
        now = datetime.utcnow()
        history = self.memory.get_build_history(pipeline_id)
        durations = [r["actual_duration"] for r in history] if history else [300.0]

        criticality_map = {"dev": 0, "development": 0, "staging": 1, "production": 2, "prod": 2}
        env = metadata.get("environment", "staging").lower()

        return {
            "pipeline_step_count": float(metadata.get("step_count", 5)),
            "service_count": float(metadata.get("service_count", 2)),
            "artifact_size_mb": float(metadata.get("artifact_size_mb", 100)),
            "time_of_day_hour": float(now.hour),
            "day_of_week": float(now.weekday()),
            "historical_duration_mean": float(np.mean(durations)),
            "historical_duration_std": float(np.std(durations)) if len(durations) > 1 else 60.0,
            "queue_depth": float(metadata.get("queue_depth", 0)),
            "environment_criticality": float(criticality_map.get(env, 1)),
        }

    def _predict_target(self, model, x: np.ndarray, default: float) -> float:
        if model is None:
            return default
        try:
            return max(0.0, float(model.predict(x)[0]))
        except Exception as e:
            logger.warning("Prediction failed: %s", e)
            return default

    def _build_recommendation(
        self, duration: float, cpu: float, mem: float, features: dict
    ) -> str:
        parts = [
            f"Estimated build time: {int(duration)}s ({duration/60:.1f}min).",
            f"Pre-allocate {cpu:.1f} CPU cores and {mem:.1f}GB RAM.",
        ]
        if features.get("queue_depth", 0) > 10:
            parts.append("Queue is deep — consider scaling builder nodes before this run.")
        if features.get("environment_criticality", 1) >= 2:
            parts.append("Production deployment: reserve 20% extra headroom for rollback capacity.")
        return " ".join(parts)

    def _load_or_bootstrap(self) -> None:
        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    saved = pickle.load(f)
                self._duration_model = saved["duration"]
                self._cpu_model = saved["cpu"]
                self._memory_model = saved["memory"]
                self._sample_count = saved["sample_count"]
                logger.info("Loaded resource scheduler models (%d samples)", self._sample_count)
                return
            except Exception as e:
                logger.warning("Failed to load resource scheduler: %s", e)

        logger.info("Bootstrapping resource scheduler with synthetic data...")
        X, y_dur, y_cpu, y_mem = self._generate_synthetic_data(500)
        self._duration_model = self._train_regressor(X, y_dur)
        self._cpu_model = self._train_regressor(X, y_cpu)
        self._memory_model = self._train_regressor(X, y_mem)
        self._sample_count = 500
        self._save_models()

    def _train_regressor(self, X: np.ndarray, y: np.ndarray):
        try:
            from xgboost import XGBRegressor
            model = XGBRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42,
                n_jobs=-1,
            )
        except ImportError:
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor(n_estimators=100, random_state=42)
        model.fit(X, y)
        return model

    def _retrain(self) -> None:
        rows = self.memory.get_all_build_outcomes()
        if len(rows) < 50:
            return
        X = np.array([[r[f] for f in DURATION_FEATURES] for r in rows], dtype=np.float32)
        y_dur = np.array([r["actual_duration"] for r in rows])
        y_cpu = np.array([r["actual_cpu"] for r in rows])
        y_mem = np.array([r["actual_memory"] for r in rows])
        self._duration_model = self._train_regressor(X, y_dur)
        self._cpu_model = self._train_regressor(X, y_cpu)
        self._memory_model = self._train_regressor(X, y_mem)
        self._save_models()
        logger.info("Resource scheduler retrained on %d samples", len(rows))

    def _save_models(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({
                "duration": self._duration_model,
                "cpu": self._cpu_model,
                "memory": self._memory_model,
                "sample_count": self._sample_count,
            }, f)

    @staticmethod
    def _generate_synthetic_data(n: int):
        rng = np.random.RandomState(42)
        X = np.zeros((n, len(DURATION_FEATURES)), dtype=np.float32)
        X[:, 0] = rng.randint(2, 15, n).astype(float)     # pipeline_step_count
        X[:, 1] = rng.randint(1, 10, n).astype(float)     # service_count
        X[:, 2] = rng.exponential(200, n)                  # artifact_size_mb
        X[:, 3] = rng.randint(0, 24, n).astype(float)     # time_of_day_hour
        X[:, 4] = rng.randint(0, 7, n).astype(float)      # day_of_week
        X[:, 5] = rng.uniform(60, 1200, n)                 # historical_duration_mean
        X[:, 6] = rng.uniform(10, 200, n)                  # historical_duration_std
        X[:, 7] = rng.randint(0, 30, n).astype(float)     # queue_depth
        X[:, 8] = rng.randint(0, 3, n).astype(float)      # environment_criticality

        # Synthetic targets: duration scales with steps, services, artifact size
        y_dur = (
            X[:, 0] * 30             # 30s per step
            + X[:, 1] * 20           # 20s per service
            + X[:, 2] * 0.5          # 0.5s per MB
            + X[:, 7] * 10           # queue wait
            + rng.normal(0, 30, n)
        )
        y_dur = np.clip(y_dur, 30, 7200)

        y_cpu = np.clip(X[:, 1] * 0.5 + X[:, 8] + rng.normal(0, 0.5, n), 0.5, 16.0)
        y_mem = np.clip(X[:, 1] * 0.8 + X[:, 2] / 200 + X[:, 8] * 2 + rng.normal(0, 0.5, n), 0.5, 32.0)

        return X, y_dur, y_cpu, y_mem
