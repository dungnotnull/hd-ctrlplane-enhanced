# ctrlplane-enhanced — Test Scenarios

## Scenario 1: Low-Risk Deployment (Pass-Through)

**Trigger:** POST `/api/v1/agent/risk-score` with deployment metadata for a small, well-tested change.

**Input:**
```json
{
  "deployment_id": "dep-001",
  "deployment_metadata": {
    "service_count": 1,
    "deploys_last_7d": 12,
    "error_rate_7d": 0.001,
    "latency_p95_trend": -0.02,
    "downstream_service_count": 2,
    "test_coverage_pct": 92,
    "config_change_count": 0
  }
}
```

**Expected Output:**
- `risk_score` < 0.30
- `risk_tier` = "low"
- `block_recommendation` = false
- `explanation` = "" (no explanation for low-risk deployments)

**Pass Criteria:** Agent returns in < 500ms; no LLM call triggered.

---

## Scenario 2: High-Risk Deployment Blocked

**Trigger:** POST `/api/v1/agent/risk-score` with metadata indicating a large, high-dependency deploy shortly after a rollback.

**Input:**
```json
{
  "deployment_id": "dep-002",
  "deployment_metadata": {
    "service_count": 8,
    "deploys_last_7d": 2,
    "error_rate_7d": 0.08,
    "latency_p95_trend": 0.15,
    "downstream_service_count": 15,
    "test_coverage_pct": 48,
    "config_change_count": 25
  }
}
```

**Seed memory with rollback event 12 hours ago.**

**Expected Output:**
- `risk_score` > 0.70
- `risk_tier` = "high" or "critical"
- `block_recommendation` = true
- `explanation` contains ≥ 2 sentences describing the risk drivers
- `contributing_features` shows top feature with importance > 0.20

**Pass Criteria:** Agent returns in < 2s (including LLM call); explanation is actionable.

---

## Scenario 3: Anomaly-Triggered Auto-Rollback

**Trigger:** POST `/api/v1/agent/rollback-check` after deploying a service that causes error rate spike.

**Setup:** Pre-fit isolation forest baseline with 200 normal metric samples. Then submit anomalous metrics:
```json
{
  "deployment_id": "dep-003",
  "services": ["payment-service"],
  "prometheus_url": ""
}
```

**Mock Prometheus to return:**
- `http_requests_error_rate`: 0.35 (vs baseline 0.01)
- `http_request_duration_p95_seconds`: 4.2 (vs baseline 0.8)
- `container_cpu_usage_ratio`: 0.95 (vs baseline 0.40)

**Expected Output:**
- `anomaly_detected` = true
- `anomaly_score` < -0.30
- `rollback_triggered` = true (if `deployment_age < max_rollback_age`)
- `incident_summary` contains references to error_rate and latency

**Pass Criteria:** Rollback decision made in < 1s after metric query; incident summary is coherent 3-sentence text.

---

## Scenario 4: NL Pipeline Generation — Simple

**Trigger:** POST `/api/v1/agent/generate-pipeline`

**Input:**
```json
{
  "description": "Deploy my Node.js API to staging, then run smoke tests, then deploy to production with automatic approval"
}
```

**Expected Output:**
- `validation_passed` = true
- `pipeline_yaml` contains `stages:` with at least 3 stages
- Stage names include: staging deploy, smoke/test, production deploy
- No manual `approval: true` (user specified "automatic")
- `explanation` summarizes the stages

**Pass Criteria:** Valid YAML returned in < 10s; passes jsonschema validation; no retries needed.

---

## Scenario 5: NL Pipeline Generation — Complex Multi-Stage with Approval Gate

**Trigger:** POST `/api/v1/agent/generate-pipeline`

**Input:**
```json
{
  "description": "Build Docker image, run unit tests in parallel with security scan, require VP sign-off, deploy to staging, wait 1 hour, deploy to production if staging is healthy"
}
```

**Expected Output:**
- `validation_passed` = true
- Pipeline has ≥ 5 stages
- `approval: true` appears in YAML (VP sign-off)
- `depends_on` relationships correctly capture the parallel unit test + security scan stage
- `explanation` mentions all major pipeline phases

**Pass Criteria:** Generated YAML is logically consistent (all `depends_on` refs exist); validation passes in ≤ 2 LLM attempts.

---

## Scenario 6: Resource Scheduling Prediction

**Trigger:** POST `/api/v1/agent/schedule-resources`

**Input:**
```json
{
  "pipeline_id": "pipe-001",
  "pipeline_metadata": {
    "step_count": 8,
    "service_count": 4,
    "artifact_size_mb": 350,
    "environment": "production",
    "queue_depth": 15
  }
}
```

**Expected Output:**
- `predicted_duration_seconds` between 300 and 2400 (5–40 minutes — reasonable for 4 services)
- `predicted_cpu_cores` between 1.0 and 8.0
- `predicted_memory_gb` between 2.0 and 16.0
- `confidence_interval` = [duration * 0.8, duration * 1.2]
- `recommendation` mentions production headroom and queue depth warning

**Pass Criteria:** Prediction returned in < 200ms; all values are physically reasonable; recommendation text is non-empty.

---

## Scenario 7: Weekly Knowledge Update Cycle

**Trigger:** CLI `python -m agent.main update-knowledge`

**Expected Behavior:**
1. ArXiv API queried for cs.SE + cs.DC with CI/CD keywords
2. Semantic Scholar queried for top 5 keywords
3. GitHub Engineering Blog RSS parsed
4. CNCF Blog RSS parsed
5. Ctrlplane GitHub releases fetched
6. ≥ 5 new unique entries appended to `SECOND-KNOWLEDGE-BRAIN.md`
7. Duplicate entries (same URL) are not re-added (dedup works)
8. Each entry includes: title, URL, authors, date, abstract excerpt

**Pass Criteria:**
- Completes without errors in < 120s
- `SECOND-KNOWLEDGE-BRAIN.md` grows by ≥ 5 entries
- Running the update twice does not add duplicate entries
- All entries have non-empty title and URL
