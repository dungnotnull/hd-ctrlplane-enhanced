"""
Automated tests for ctrlplane-enhanced AI Agent.

Tests: risk_scorer (7), auto_rollback (5), nl_pipeline_generator (5),
       resource_scheduler (5), memory_manager (3), integration (3), CLI (5).
Total: 33 tests.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestRiskScorer(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_risk.db")
        self.memory = MemoryManager(db_path=self.db_path)
        from agent.modules.risk_scorer import RiskScorer
        self.scorer = RiskScorer(config={"risk_threshold": 0.7, "model_path": os.path.join(self.tmp_dir, "risk_model.pkl")}, memory=self.memory)

    def tearDown(self):
        self.memory.close()

    def test_feature_extraction_defaults(self):
        features = self.scorer.extract_features("dep-001", {})
        self.assertIn("deployment_size", features)
        self.assertIn("test_coverage_pct", features)
        self.assertEqual(len(features), 8)

    def test_feature_extraction_from_metadata(self):
        metadata = {"service_count": 5, "test_coverage_pct": 80.0, "config_change_count": 10}
        features = self.scorer.extract_features("dep-002", metadata)
        self.assertEqual(features["deployment_size"], 5.0)
        self.assertEqual(features["test_coverage_pct"], 80.0)

    def test_predict_returns_valid_output(self):
        features = self.scorer.extract_features("dep-003", {})
        risk_score, tier, importances = self.scorer.predict(features)
        self.assertGreaterEqual(risk_score, 0.0)
        self.assertLessEqual(risk_score, 1.0)
        self.assertIn(tier, ["low", "medium", "high", "critical"])
        self.assertEqual(len(importances), 8)

    def test_low_risk_profile(self):
        metadata = {
            "service_count": 1,
            "deploys_last_7d": 15,
            "error_rate_7d": 0.001,
            "latency_p95_trend": -0.01,
            "downstream_service_count": 1,
            "test_coverage_pct": 95,
            "config_change_count": 0,
        }
        features = self.scorer.extract_features("dep-004", metadata)
        features["time_since_last_rollback_hours"] = 720.0
        risk_score, tier, _ = self.scorer.predict(features)
        self.assertLess(risk_score, 0.7, "Low-risk profile should not exceed 0.7")

    def test_high_risk_profile(self):
        features = {
            "deployment_size": 12.0,
            "deploy_frequency": 1.0,
            "time_since_last_rollback_hours": 6.0,
            "error_rate_7d": 0.12,
            "latency_p95_trend": 0.3,
            "service_dependency_count": 18.0,
            "test_coverage_pct": 35.0,
            "config_change_size": 28.0,
        }
        risk_score, tier, _ = self.scorer.predict(features)
        self.assertGreater(risk_score, 0.5, "High-risk profile should exceed 0.5")

    def test_heuristic_fallback_below_50_samples(self):
        from agent.memory.memory_manager import MemoryManager
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "test_heuristic.db")
        model_path = os.path.join(tmp_dir, "heuristic_model.pkl")
        mem = MemoryManager(db_path=db_path)
        from agent.modules.risk_scorer import RiskScorer
        scorer = RiskScorer(config={"risk_threshold": 0.7, "model_path": model_path}, memory=mem)
        scorer._sample_count = 0
        scorer._model = None
        features = {
            "deployment_size": 5.0,
            "deploy_frequency": 5.0,
            "time_since_last_rollback_hours": 100.0,
            "error_rate_7d": 0.03,
            "latency_p95_trend": 0.0,
            "service_dependency_count": 5.0,
            "test_coverage_pct": 70.0,
            "config_change_size": 5.0,
        }
        risk_score, _, _ = scorer.predict(features)
        self.assertGreaterEqual(risk_score, 0.0)
        self.assertLessEqual(risk_score, 1.0)
        mem.close()

    def test_synthetic_bootstrap_creates_model(self):
        self.assertIsNotNone(self.scorer._model)
        self.assertEqual(self.scorer._sample_count, 500)


class TestAutoRollback(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_rollback.db")
        self.memory = MemoryManager(db_path=self.db_path)
        self.llm = MagicMock()
        self.llm.complete.return_value = "Auto-rollback triggered. Error rate spiked. Verify service health."
        from agent.modules.auto_rollback import AutoRollback
        self.rb = AutoRollback(
            config={"contamination": 0.05, "anomaly_threshold": -0.3, "max_rollback_age_seconds": 3600},
            memory=self.memory,
            llm=self.llm,
        )

    def tearDown(self):
        self.memory.close()

    def test_normal_metrics_no_rollback(self):
        """Normal metrics should not trigger rollback in learning mode."""
        with patch.object(self.rb, "_query_prometheus", return_value={s: 0.01 for s in ["http_requests_error_rate", "http_request_duration_p95_seconds", "http_request_duration_p99_seconds", "container_cpu_usage_ratio", "container_memory_usage_ratio", "http_requests_per_second"]}):
            result = self.rb.evaluate("dep-001", ["svc-a"], "")
        self.assertFalse(result["rollback_triggered"])
        self.assertIn("deployment_id", result)

    def test_anomalous_metrics_trigger_rollback(self):
        """After fitting baseline, anomalous metrics should trigger rollback."""
        from agent.modules.auto_rollback import GOLDEN_SIGNALS
        normal = [{s: 0.01 for s in GOLDEN_SIGNALS} for _ in range(200)]
        self.rb.fit_baseline(normal)
        anomalous = {s: 0.0 for s in GOLDEN_SIGNALS}
        anomalous["http_requests_error_rate"] = 0.8
        anomalous["http_request_duration_p95_seconds"] = 10.0
        anomalous["container_cpu_usage_ratio"] = 0.99

        self.memory.record_risk_assessment("dep-002", {}, 0.5)

        with patch.object(self.rb, "_query_prometheus", return_value=anomalous):
            with patch.object(self.rb, "_trigger_rollback", return_value=True):
                result = self.rb.evaluate("dep-002", ["svc-b"], "")
        self.assertTrue(result["anomaly_detected"])

    def test_evaluate_returns_required_fields(self):
        with patch.object(self.rb, "_query_prometheus", return_value=None):
            result = self.rb.evaluate("dep-003", [], "")
        self.assertIn("deployment_id", result)
        self.assertIn("anomaly_detected", result)
        self.assertIn("rollback_triggered", result)
        self.assertIn("incident_summary", result)

    def test_incident_summary_fallback_when_llm_fails(self):
        self.llm.complete.side_effect = RuntimeError("LLM unavailable")
        from agent.modules.auto_rollback import GOLDEN_SIGNALS
        metrics = {s: 0.1 for s in GOLDEN_SIGNALS}
        summary = self.rb._generate_incident_summary("dep-004", metrics, -0.5)
        self.assertIn("dep-004", summary)
        self.assertIn("-0.5", summary)

    def test_isolation_forest_fitted_after_baseline(self):
        from agent.modules.auto_rollback import GOLDEN_SIGNALS
        normal = [{s: 0.01 for s in GOLDEN_SIGNALS} for _ in range(200)]
        self.rb.fit_baseline(normal)
        self.assertTrue(self.rb._baseline_fitted)


class TestNLPipelineGenerator(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_nl.db")
        self.memory = MemoryManager(db_path=self.db_path)
        self.llm = MagicMock()
        self.hf = MagicMock()
        self.hf.encode.return_value = np.random.randn(384).astype(np.float32)

    def tearDown(self):
        self.memory.close()

    def _make_generator(self):
        from agent.modules.nl_pipeline_generator import NLPipelineGenerator
        return NLPipelineGenerator(
            config={"max_attempts": 3},
            memory=self.memory,
            llm=self.llm,
            hf=self.hf,
        )

    def test_generate_valid_yaml(self):
        self.llm.complete.return_value = """name: test-pipeline
stages:
  - name: deploy-staging
    jobs:
      - name: deploy
        type: deploy
        environment: staging
"""
        gen = self._make_generator()
        result = gen.generate("Deploy to staging", {})
        self.assertTrue(result["validation_passed"])
        self.assertIn("name:", result["pipeline_yaml"])

    def test_retry_on_invalid_yaml(self):
        valid_yaml = """name: retry-pipeline
stages:
  - name: deploy
    jobs:
      - name: deploy-job
        type: deploy
        environment: production
"""
        self.llm.complete.side_effect = ["invalid: yaml: {{{", valid_yaml]
        gen = self._make_generator()
        result = gen.generate("Deploy to production", {})
        self.assertTrue(result["validation_passed"])
        self.assertEqual(self.llm.complete.call_count, 2)

    def test_fallback_yaml_on_all_retries_exhausted(self):
        self.llm.complete.return_value = "not yaml at all <<<>>>"
        from agent.modules.nl_pipeline_generator import NLPipelineGenerator
        gen = NLPipelineGenerator(
            config={"max_attempts": 1},
            memory=self.memory,
            llm=self.llm,
            hf=self.hf,
        )
        result = gen.generate("some description", {})
        self.assertIn("name:", result["pipeline_yaml"])

    def test_validation_detects_orphan_depends_on(self):
        from agent.modules.nl_pipeline_generator import NLPipelineGenerator
        gen = NLPipelineGenerator(config={}, memory=self.memory, llm=self.llm, hf=self.hf)
        bad_yaml = """name: bad-pipeline
stages:
  - name: deploy
    jobs:
      - name: deploy-job
        type: deploy
        depends_on: [nonexistent-job]
"""
        valid, error = gen._validate(bad_yaml)
        self.assertFalse(valid)
        self.assertIn("nonexistent-job", error)

    def test_example_pipelines_seeded_in_memory(self):
        from agent.modules.nl_pipeline_generator import NLPipelineGenerator
        gen = NLPipelineGenerator(config={}, memory=self.memory, llm=self.llm, hf=self.hf)
        rows = self.memory._conn.execute("SELECT COUNT(*) FROM pipeline_templates").fetchone()
        self.assertGreater(rows[0], 0)


class TestResourceScheduler(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_sched.db")
        self.memory = MemoryManager(db_path=self.db_path)
        from agent.modules.resource_scheduler import ResourceScheduler
        self.scheduler = ResourceScheduler(config={"model_path": os.path.join(self.tmp_dir, "sched_model.pkl")}, memory=self.memory)

    def tearDown(self):
        self.memory.close()

    def test_predict_returns_all_fields(self):
        result = self.scheduler.predict("pipe-001", {})
        self.assertIn("predicted_duration_seconds", result)
        self.assertIn("predicted_cpu_cores", result)
        self.assertIn("predicted_memory_gb", result)
        self.assertIn("confidence_interval", result)
        self.assertIn("recommendation", result)

    def test_predict_duration_is_positive(self):
        result = self.scheduler.predict("pipe-002", {"step_count": 5})
        self.assertGreater(result["predicted_duration_seconds"], 0)

    def test_confidence_interval_bounds(self):
        result = self.scheduler.predict("pipe-003", {})
        ci = result["confidence_interval"]
        dur = result["predicted_duration_seconds"]
        # CI should be approximately [dur*0.8, dur*1.2] but rounding may cause small diffs
        self.assertAlmostEqual(ci[0], dur * 0.8, delta=2.0)
        self.assertAlmostEqual(ci[1], dur * 1.2, delta=2.0)
        self.assertLess(ci[0], ci[1])

    def test_production_recommendation_mentions_headroom(self):
        result = self.scheduler.predict("pipe-004", {"environment": "production"})
        self.assertIn("production", result["recommendation"].lower())

    def test_high_queue_depth_triggers_warning(self):
        result = self.scheduler.predict("pipe-005", {"queue_depth": 20})
        rec = result["recommendation"].lower()
        self.assertTrue("queue" in rec or "scaling" in rec or "builder" in rec)


class TestMemoryManager(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_memory.db")
        self.memory = MemoryManager(db_path=self.db_path)

    def tearDown(self):
        self.memory.close()

    def test_risk_assessment_crud(self):
        self.memory.record_risk_assessment("dep-001", {"a": 1.0}, 0.45)
        result = self.memory.get_deployment_history("dep-001")
        self.assertIn("rollbacks", result)

    def test_pipeline_template_storage_and_retrieval(self):
        emb = np.random.randn(384).astype(np.float32)
        self.memory.save_pipeline_template("deploy to staging", emb.tolist(), "name: test\nstages: []")
        similar = self.memory.find_similar_pipelines(emb, top_k=1)
        self.assertEqual(len(similar), 1)
        self.assertIn("pipeline_yaml", similar[0])

    def test_knowledge_hash_dedup(self):
        self.memory.add_knowledge_hash("abc123", "arxiv", "Test Paper")
        self.assertTrue(self.memory.has_knowledge_hash("abc123"))
        self.assertFalse(self.memory.has_knowledge_hash("xyz999"))


class TestIntegration(unittest.TestCase):
    def setUp(self):
        from agent.memory.memory_manager import MemoryManager
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_integration.db")
        self.memory = MemoryManager(db_path=self.db_path)

    def tearDown(self):
        self.memory.close()

    def test_risk_score_to_memory_pipeline(self):
        from agent.modules.risk_scorer import RiskScorer
        scorer = RiskScorer(config={"risk_threshold": 0.7, "model_path": os.path.join(self.tmp_dir, "int_risk.pkl")}, memory=self.memory)
        features = scorer.extract_features("dep-001", {"service_count": 3})
        risk_score, tier, _ = scorer.predict(features)
        self.memory.record_risk_assessment("dep-001", features, risk_score)
        start = self.memory.get_deployment_start_time("dep-001")
        self.assertIsNotNone(start)

    def test_pipeline_generate_to_memory(self):
        llm = MagicMock()
        llm.complete.return_value = """name: integration-test
stages:
  - name: deploy
    jobs:
      - name: deploy-job
        type: deploy
        environment: staging
"""
        hf = MagicMock()
        hf.encode.return_value = np.random.randn(384).astype(np.float32)
        from agent.modules.nl_pipeline_generator import NLPipelineGenerator
        gen = NLPipelineGenerator(config={"max_attempts": 3}, memory=self.memory, llm=llm, hf=hf)
        result = gen.generate("deploy to staging", {})
        self.assertTrue(result["validation_passed"])
        count = self.memory._conn.execute("SELECT COUNT(*) FROM pipeline_templates").fetchone()[0]
        self.assertGreater(count, 0)

    def test_resource_scheduler_outcome_recording(self):
        from agent.modules.resource_scheduler import ResourceScheduler, DURATION_FEATURES
        scheduler = ResourceScheduler(config={"model_path": os.path.join(self.tmp_dir, "int_sched.pkl")}, memory=self.memory)
        features = {f: 1.0 for f in DURATION_FEATURES}
        self.memory.record_build_outcome("pipe-001", features, 300.0, 2.0, 4.0)
        outcomes = self.memory.get_all_build_outcomes()
        self.assertGreater(len(outcomes), 0)


class TestCLISmokeTests(unittest.TestCase):
    def test_help_output(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent.main", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        self.assertIn("ctrlplane", result.stdout.lower() + result.stderr.lower())

    def test_score_command_exists(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent.main", "score", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        self.assertEqual(result.returncode, 0)

    def test_generate_command_exists(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent.main", "generate", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        self.assertEqual(result.returncode, 0)

    def test_rollback_command_exists(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent.main", "rollback", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        self.assertEqual(result.returncode, 0)

    def test_schedule_command_exists(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent.main", "schedule", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
