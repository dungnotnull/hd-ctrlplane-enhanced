"""MemoryManager — SQLite-based persistent memory for ctrlplane-enhanced."""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL,
    features_json TEXT NOT NULL,
    risk_score REAL NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS deployment_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL,
    features_json TEXT NOT NULL,
    rolled_back INTEGER NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS rollback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    anomaly_score REAL NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS metric_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metrics_json TEXT NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS pipeline_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nl_description TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    pipeline_yaml TEXT NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS build_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id TEXT NOT NULL,
    features_json TEXT NOT NULL,
    actual_duration REAL NOT NULL,
    actual_cpu REAL NOT NULL,
    actual_memory REAL NOT NULL,
    created_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS deployment_starts (
    deployment_id TEXT PRIMARY KEY,
    start_time REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_hashes (
    hash TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    added_at REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS llm_cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    use_case TEXT DEFAULT '',
    created_at REAL DEFAULT (unixepoch())
);
"""


class MemoryManager:
    def __init__(self, db_path: str = "./ctrlplane_memory.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── Risk assessment ──────────────────────────────────────────────────────

    def record_risk_assessment(
        self, deployment_id: str, features: dict, risk_score: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO risk_assessments (deployment_id, features_json, risk_score) VALUES (?,?,?)",
            (deployment_id, json.dumps(features), risk_score),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO deployment_starts (deployment_id, start_time) VALUES (?,?)",
            (deployment_id, time.time()),
        )
        self._conn.commit()

    def get_deployment_history(self, deployment_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT * FROM rollback_events WHERE deployment_id=? ORDER BY created_at DESC LIMIT 10",
            (deployment_id,),
        ).fetchall()
        return {"rollbacks": [dict(r) for r in rows]}

    def get_deployment_start_time(self, deployment_id: str) -> float | None:
        row = self._conn.execute(
            "SELECT start_time FROM deployment_starts WHERE deployment_id=?",
            (deployment_id,),
        ).fetchone()
        return float(row["start_time"]) if row else None

    # ── Deployment outcomes (for model retraining) ───────────────────────────

    def record_deployment_outcome(
        self, deployment_id: str, features: dict, rolled_back: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO deployment_outcomes (deployment_id, features_json, rolled_back) VALUES (?,?,?)",
            (deployment_id, json.dumps(features), rolled_back),
        )
        self._conn.commit()

    def get_all_deployment_outcomes(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT features_json, rolled_back FROM deployment_outcomes"
        ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["features_json"])
            d["rolled_back"] = r["rolled_back"]
            result.append(d)
        return result

    # ── Rollback events ──────────────────────────────────────────────────────

    def record_rollback(
        self, deployment_id: str, metrics: dict, anomaly_score: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO rollback_events (deployment_id, metrics_json, anomaly_score) VALUES (?,?,?)",
            (deployment_id, json.dumps(metrics), anomaly_score),
        )
        self._conn.commit()

    # ── Prometheus metric baseline ───────────────────────────────────────────

    def record_metric_sample(self, metrics: dict) -> None:
        self._conn.execute(
            "INSERT INTO metric_samples (metrics_json) VALUES (?)",
            (json.dumps(metrics),),
        )
        # Keep last 10000 samples (rolling window)
        self._conn.execute(
            "DELETE FROM metric_samples WHERE id NOT IN "
            "(SELECT id FROM metric_samples ORDER BY id DESC LIMIT 10000)"
        )
        self._conn.commit()

    def get_metric_baseline(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT metrics_json FROM metric_samples ORDER BY id DESC LIMIT 5000"
        ).fetchall()
        return [json.loads(r["metrics_json"]) for r in rows]

    # ── Pipeline templates ───────────────────────────────────────────────────

    def save_pipeline_template(
        self, description: str, embedding: list[float], pipeline_yaml: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO pipeline_templates (nl_description, embedding_json, pipeline_yaml) VALUES (?,?,?)",
            (description, json.dumps(embedding), pipeline_yaml),
        )
        self._conn.commit()

    def pipeline_template_exists(self, description: str) -> bool:
        row = self._conn.execute(
            "SELECT id FROM pipeline_templates WHERE nl_description=?", (description,)
        ).fetchone()
        return row is not None

    def find_similar_pipelines(self, query_embedding, top_k: int = 3) -> list[dict]:
        rows = self._conn.execute(
            "SELECT nl_description, embedding_json, pipeline_yaml FROM pipeline_templates"
        ).fetchall()
        if not rows:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        if query_vec.ndim > 1:
            query_vec = query_vec.flatten()

        scored = []
        for r in rows:
            emb = np.array(json.loads(r["embedding_json"]), dtype=np.float32)
            if emb.shape != query_vec.shape:
                continue
            norm_q = np.linalg.norm(query_vec)
            norm_e = np.linalg.norm(emb)
            if norm_q > 0 and norm_e > 0:
                sim = float(np.dot(query_vec, emb) / (norm_q * norm_e))
            else:
                sim = 0.0
            scored.append((sim, dict(r)))

        scored.sort(key=lambda x: -x[0])
        return [x[1] for x in scored[:top_k]]

    # ── Build outcomes ───────────────────────────────────────────────────────

    def record_build_outcome(
        self,
        pipeline_id: str,
        features: dict,
        actual_duration: float,
        actual_cpu: float,
        actual_memory: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO build_outcomes (pipeline_id, features_json, actual_duration, actual_cpu, actual_memory) "
            "VALUES (?,?,?,?,?)",
            (pipeline_id, json.dumps(features), actual_duration, actual_cpu, actual_memory),
        )
        self._conn.commit()

    def get_build_history(self, pipeline_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT features_json, actual_duration FROM build_outcomes WHERE pipeline_id=? "
            "ORDER BY id DESC LIMIT 30",
            (pipeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_build_outcomes(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT features_json, actual_duration, actual_cpu, actual_memory FROM build_outcomes"
        ).fetchall()
        result = []
        for r in rows:
            d = json.loads(r["features_json"])
            d["actual_duration"] = r["actual_duration"]
            d["actual_cpu"] = r["actual_cpu"]
            d["actual_memory"] = r["actual_memory"]
            result.append(d)
        return result

    # ── Knowledge dedup ──────────────────────────────────────────────────────

    def has_knowledge_hash(self, hash_str: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM knowledge_hashes WHERE hash=?", (hash_str,)
        ).fetchone() is not None

    def add_knowledge_hash(self, hash_str: str, source: str, title: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO knowledge_hashes (hash, source, title) VALUES (?,?,?)",
            (hash_str, source, title),
        )
        self._conn.commit()

    # ── LLM cost tracking ────────────────────────────────────────────────────

    def record_llm_cost(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        use_case: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO llm_cost_log (provider, model, tokens_in, tokens_out, cost_usd, use_case) "
            "VALUES (?,?,?,?,?,?)",
            (provider, model, tokens_in, tokens_out, cost_usd, use_case),
        )
        self._conn.commit()

    def get_total_cost(self) -> float:
        row = self._conn.execute("SELECT SUM(cost_usd) FROM llm_cost_log").fetchone()
        return float(row[0] or 0.0)

    def close(self) -> None:
        self._conn.close()
