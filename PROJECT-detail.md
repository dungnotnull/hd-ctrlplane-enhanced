# ctrlplane-enhanced — Full Technical Specification

## Executive Summary

`ctrlplane-enhanced` forks Ctrlplane (open-source CI/CD orchestration platform, v0.8.0) and adds a Python sidecar AI layer that provides four intelligence capabilities:

1. **Deployment Risk Scoring** — XGBoost model predicts rollback probability before deployment executes, enabling engineers to gate high-risk changes.
2. **Anomaly-Triggered Auto-Rollback** — Isolation forest monitors Prometheus golden signals post-deploy and triggers Ctrlplane rollback API when anomalies are detected.
3. **NL Pipeline YAML Generation** — Claude API converts a natural-language pipeline description into a validated Ctrlplane pipeline YAML in seconds.
4. **Predictive Resource Scheduling** — XGBoost regression forecasts build duration and compute requirements from pipeline metadata to pre-allocate queue resources.

The AI layer runs as a separate Python service alongside the upstream Ctrlplane Go server. It connects via Ctrlplane's REST API and Prometheus API — zero upstream code modifications required.

---

## Problem Statement

CI/CD deployments at scale suffer from:
- **Silent failures** — rollbacks discovered only after user-impacting incidents
- **Inconsistent risk assessment** — senior engineers use gut feel, not data
- **Pipeline YAML complexity** — new teams spend days writing correct Ctrlplane YAML
- **Resource contention** — build queues stall because compute isn't pre-allocated

This agent addresses all four with ML/LLM automation that improves over time via continuous research ingestion.

---

## Target Users & Use Cases

| User | Trigger | Agent Action |
|------|---------|-------------|
| DevOps Engineer | Pushes new deployment candidate | Agent scores risk → if > 70% → blocks deploy + posts LLM explanation to Slack/webhook |
| SRE on-call | Post-deploy production alert | Agent detects anomaly in error rate → auto-rollbacks in < 60s → posts incident summary |
| Platform Engineer | Describes pipeline in plain English | Agent generates validated Ctrlplane YAML → previews diff → posts to Ctrlplane API |
| Build Team Lead | Plans sprint with 50 pipelines queued | Agent predicts total compute needed → recommends pre-scaling |

---

## Agent Architecture

```
                    ┌──────────────────────────────────┐
                    │  Ctrlplane REST API (upstream Go) │
                    │  POST /api/v1/deployments         │
                    │  POST /api/v1/pipelines           │
                    │  POST /api/v1/deployments/{id}/rollback │
                    └──────────────┬───────────────────┘
                                   │ webhook / poll
                    ┌──────────────▼───────────────────┐
                    │  ctrlplane-enhanced AI Sidecar    │
                    │  (Python, FastAPI / CLI)          │
                    │                                   │
                    │  ┌─────────────────────────────┐ │
                    │  │  Orchestrator               │ │
                    │  │  (orchestrator.py)          │ │
                    │  └──────────┬──────────────────┘ │
                    │             │                     │
                    │  ┌──────────▼──────────────────┐ │
                    │  │  Module Router              │ │
                    │  └──┬──────┬────────┬──────────┘ │
                    │     │      │        │             │
                    │  ┌──▼──┐ ┌─▼──┐ ┌──▼──┐ ┌──▼──┐ │
                    │  │Risk │ │Roll│ │NL   │ │Sched│ │
                    │  │Scor │ │back│ │Gen  │ │uler │ │
                    │  └──┬──┘ └─┬──┘ └──┬──┘ └──┬──┘ │
                    └─────┼──────┼───────┼───────┼────┘
                          │      │       │       │
              ┌───────────▼┐  ┌──▼──┐  ┌▼────┐  └──────────┐
              │ XGBoost    │  │Isofo│  │Claude│          ┌──▼──┐
              │ Risk Model │  │rest │  │ API  │          │XGB  │
              └────────────┘  └─────┘  └──────┘          │Sched│
                                                         └─────┘
                    │               │               │
              ┌─────▼───────┐  ┌────▼────┐  ┌──────▼───────┐
              │ Prometheus  │  │ HF Model│  │  SQLite DB   │
              │ Metrics API │  │ Manager │  │  (memory)    │
              └─────────────┘  └─────────┘  └──────────────┘
```

---

## Full Module Catalog

### `agent/modules/risk_scorer.py`

**Responsibility:** XGBoost-based deployment risk scoring predicting probability of rollback.

**Inputs:**
- Deployment metadata from Ctrlplane API (service count, config diff size, deploy frequency)
- Historical deployment outcomes from memory_manager (rollback rate, error history)
- Pipeline metadata (test coverage %, environment criticality)

**Outputs:**
- `risk_score: float` (0.0–1.0, probability of rollback)
- `risk_tier: str` ("low" | "medium" | "high" | "critical")
- `contributing_features: dict` (feature importances for LLM explanation)

**Features (8):**
1. `deployment_size` — number of services changed in this deployment
2. `deploy_frequency` — deployments to this environment in last 7 days
3. `time_since_last_rollback` — hours since last rollback (lower = higher risk)
4. `error_rate_7d` — average error rate of affected services over last 7 days
5. `latency_p95_trend` — slope of P95 latency over last 7 days (positive = degrading)
6. `service_dependency_count` — number of downstream services depending on changed services
7. `test_coverage_pct` — CI test coverage percentage for this build
8. `config_change_size` — number of environment variables / config keys modified

**Model:** XGBoost binary classifier (rollback: yes/no) trained on synthetic + historical data. 500-sample synthetic bootstrap on first run; retrains incrementally from actual outcomes via `record_deployment_outcome()`.

**Quality gate:** Minimum 50 historical samples before serving live predictions; falls back to rule-based heuristics below that threshold.

---

### `agent/modules/auto_rollback.py`

**Responsibility:** Monitor post-deployment golden signals via Prometheus API; trigger Ctrlplane rollback when isolation forest detects anomaly.

**Inputs:**
- Prometheus API endpoint (configurable base URL)
- Active deployment ID + affected service labels
- `evaluation_window_seconds` (default: 300)

**Outputs:**
- Anomaly decision (`NORMAL` | `ANOMALY`)
- Anomaly score (< 0 = more anomalous)
- Rollback action result from Ctrlplane API
- LLM-generated incident summary

**Golden signals monitored (6):**
1. `http_requests_error_rate` — ratio of 5xx responses
2. `http_request_duration_p95_seconds` — 95th percentile request latency
3. `http_request_duration_p99_seconds` — 99th percentile request latency
4. `container_cpu_usage_ratio` — CPU utilization vs limit
5. `container_memory_usage_ratio` — memory utilization vs limit
6. `http_requests_per_second` — throughput (drop = traffic shedding)

**Model:** `sklearn.ensemble.IsolationForest` with `contamination=0.05`. Fitted on 30-day rolling baseline of pre-deploy metrics. Retrained weekly.

**Auto-rollback logic:**
```
if anomaly_score < threshold AND deployment_age < max_rollback_age:
    POST /api/v1/deployments/{id}/rollback
    generate_incident_summary(anomaly_signals, llm_client)
    notify_webhook(summary)
```

**Quality gate:** Requires 200+ baseline samples before enabling automatic rollback. In learning mode, only alerts without acting.

---

### `agent/modules/nl_pipeline_generator.py`

**Responsibility:** Convert natural-language pipeline descriptions to validated Ctrlplane pipeline YAML via Claude API.

**Inputs:**
- `description: str` — plain English pipeline description (e.g., "Deploy my Node.js app to staging, run smoke tests, then deploy to production with manual gate")
- `context: dict` — optional: existing services, environments, resource constraints

**Outputs:**
- `pipeline_yaml: str` — valid Ctrlplane pipeline YAML
- `validation_result: dict` — schema check + logical consistency check
- `explanation: str` — LLM explanation of generated pipeline steps

**Pipeline generation flow:**
1. Encode description via `all-MiniLM-L6-v2` → find similar pipeline templates in memory
2. Build Claude prompt with top-3 similar templates as few-shot examples
3. Claude generates pipeline YAML with chain-of-thought
4. Validate: YAML syntax → Ctrlplane schema (jsonschema) → logical consistency (no orphan stages)
5. If validation fails: retry with error feedback (max 3 attempts)
6. Return validated YAML + explanation

**LLM prompt pattern:**
```
System: You are a Ctrlplane CI/CD expert. Generate valid pipeline YAML for Ctrlplane v0.8.
User: <description>
      Similar pipelines:
      <template_1>
      <template_2>
      Generate a complete, valid pipeline YAML. Think step by step.
```

---

### `agent/modules/resource_scheduler.py`

**Responsibility:** Predict build duration and compute requirements for upcoming pipelines to enable proactive resource pre-allocation.

**Inputs:**
- Pipeline metadata from Ctrlplane API (step count, service count, artifact sizes)
- Historical build records from memory_manager (duration, CPU/memory usage)
- Time context (time of day, day of week, queue depth)

**Outputs:**
- `predicted_duration_seconds: int`
- `predicted_cpu_cores: float`
- `predicted_memory_gb: float`
- `confidence_interval: tuple[float, float]` — 95% CI for duration
- `recommendation: str` — human-readable pre-allocation advice

**Features (9):**
1. `pipeline_step_count` — number of pipeline stages
2. `service_count` — services being deployed
3. `artifact_size_mb` — total artifact size
4. `time_of_day_hour` — 0–23 (captures peak vs off-peak)
5. `day_of_week` — 0–6 (Monday peak pattern)
6. `historical_duration_mean` — mean duration of this pipeline over last 30 runs
7. `historical_duration_std` — standard deviation (captures variability)
8. `queue_depth` — current pending jobs in queue
9. `environment_criticality` — 0=dev, 1=staging, 2=prod (prod runs get more resources)

**Model:** XGBoost regression (target: `build_duration_seconds`). Separate models for CPU and memory predictions. 500-sample synthetic bootstrap; updates incrementally from actual build outcomes.

---

## HuggingFace Model Selection

| Model | Task | Benchmark | Reason vs Alternatives |
|-------|------|-----------|------------------------|
| `BAAI/bge-large-en-v1.5` | Pipeline template embeddings | MTEB #1 open-source (Aug 2024) | Outperforms OpenAI text-embedding-3-small at 1/10 cost |
| `sentence-transformers/all-MiniLM-L6-v2` | Real-time NL pipeline matching | 80% of bge-large quality at 5× speed | Necessary for < 100ms latency in NL generator retry loop |
| `Salesforce/codet5p-770m` | YAML/pipeline config analysis | HumanEval 46.8% (code-aware) | Understands structured YAML better than text-only models |
| `facebook/bart-large-cnn` | Incident log summarization | ROUGE-L 44.16 on CNN/DM | Strong extractive+abstractive; handles DevOps log noise well |

---

## LLM API Integration Spec

| Provider | Model | Use Case | Token Budget |
|----------|-------|----------|-------------|
| Claude | claude-opus-4-8 | NL pipeline generation (primary), risk explanation, incident runbooks | 4,096 output tokens |
| OpenAI | gpt-4o | Fallback pipeline generation; JSON function calling for structured resource allocation | 2,048 output tokens |
| Ollama | llama3 | Air-gapped/offline deployments; local risk explanation | 2,048 output tokens |

**Retry policy:** 3 attempts with exponential backoff (1s, 2s, 4s). On provider failure: cascade to next provider.

**Cost tracking:** All API calls logged to `llm_cost_log` table in SQLite with model, tokens_in, tokens_out, cost_usd.

---

## E2E Execution Flow

### Flow 1: Pre-Deployment Risk Assessment

```
1. Ctrlplane webhook → POST /api/v1/agent/risk-score with deployment_id
2. orchestrator.score_deployment(deployment_id)
3. risk_scorer.extract_features(deployment_id) 
   → Ctrlplane API: GET /api/v1/deployments/{id}
   → Prometheus API: query error_rate, latency trend for affected services
   → memory_manager: get rollback history for this environment
4. risk_scorer.predict(features) → risk_score, tier
5. IF risk_score > 0.7:
   a. llm_client.complete(risk_explanation_prompt) → plain-language explanation
   b. Return: {risk_score, tier, explanation, block_recommendation=True}
6. ELSE: Return {risk_score, tier, block_recommendation=False}
7. memory_manager.record_risk_assessment(deployment_id, features, risk_score)
```

### Flow 2: Post-Deployment Auto-Rollback

```
1. Deployment completes → orchestrator starts monitoring loop
2. Every 30s: auto_rollback.evaluate(deployment_id, services)
3. Query Prometheus: last 5min metrics for affected services
4. IsolationForest.predict(metrics_vector) → anomaly_score
5. IF anomaly_score < -0.3 AND deployment_age < 3600s:
   a. POST /api/v1/deployments/{id}/rollback → Ctrlplane
   b. llm_client.complete(incident_summary_prompt) → runbook entry
   c. POST webhook → notify team
   d. memory_manager.record_rollback(deployment_id, anomaly_signals)
6. monitoring_loop continues until: max_age OR deployment_superseded OR rollback_triggered
```

### Flow 3: NL Pipeline Generation

```
1. User: POST /api/v1/agent/generate-pipeline {description: "..."}
2. orchestrator.generate_pipeline(description)
3. hf_model_manager.encode(description, model="all-MiniLM-L6-v2")
4. memory_manager.find_similar_pipelines(embedding, top_k=3)
5. nl_pipeline_generator.generate(description, similar_pipelines)
6. attempt=1; max_attempts=3
7. llm_client.complete(pipeline_generation_prompt) → yaml_string
8. validate(yaml_string) → success OR error_msg
9. IF error: llm_client.complete(fix_prompt(yaml_string, error_msg)) → retry
10. IF success: POST /api/v1/pipelines → Ctrlplane
11. memory_manager.save_pipeline(description, embedding, yaml_string)
12. Return {pipeline_id, yaml, explanation}
```

---

## SECOND-KNOWLEDGE-BRAIN.md Integration

- Sources: ArXiv cs.SE, cs.DC; ICSE, MSR conference proceedings; Ctrlplane GitHub; GitHub Engineering blog; CNCF blog
- Update schedule: weekly (Sunday 02:00)
- Crawl pipeline: `tools/knowledge_updater.py` → ArXiv API + GitHub API + RSS feeds
- Dedup: URL/DOI hash check against existing entries in SECOND-KNOWLEDGE-BRAIN.md

---

## Quality Gates

1. **Risk score calibration**: Brier score ≤ 0.15 on held-out validation set before serving predictions
2. **Auto-rollback precision**: False positive rate ≤ 5% (test against synthetic normal traffic)
3. **Pipeline YAML validity**: 100% of generated YAMLs must pass jsonschema validation before delivery
4. **LLM retry success**: ≥ 90% of NL pipeline requests succeed within 3 LLM attempts
5. **Response latency**: Risk scoring ≤ 500ms P95; NL generation ≤ 10s P95

---

## Test Scenarios

See `tests/test-scenarios.md` for 7 end-to-end scenarios covering:
1. Low-risk deployment (no-op)
2. High-risk deployment blocked
3. Anomaly-triggered rollback
4. NL pipeline generation (simple)
5. NL pipeline generation (complex multi-stage)
6. Resource scheduling prediction
7. Knowledge update cycle

---

## Key Design Decisions

1. **Sidecar architecture** — No upstream Go code modifications. All AI intelligence lives in the Python sidecar, connected via Ctrlplane REST API. Enables independent upgrades of both components.
2. **XGBoost over neural nets for risk/resource** — Interpretable feature importances are critical for engineer trust. XGBoost trains on < 1000 samples with good results; neural nets need 10k+ samples.
3. **Isolation forest for anomaly detection** — No labeled anomaly data available at deployment time. Isolation forest is unsupervised and well-suited for high-dimensional golden signal vectors.
4. **Claude-first for YAML generation** — Claude's long-context window handles full pipeline YAML examples in the prompt without truncation. GPT-4o is fallback.
5. **BGE-large for template matching** — Higher quality embeddings mean the few-shot examples given to Claude are more relevant, dramatically improving generation quality.
6. **SQLite for memory** — Single-file persistence is sufficient for single-instance deployment. Trivially replaced with PostgreSQL via SQLAlchemy if multi-instance needed.
7. **Synthetic bootstrap training** — 500 synthetic deployment records generated at startup allow the risk model to serve predictions immediately; it improves as real outcomes accumulate.
