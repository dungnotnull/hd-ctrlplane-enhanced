# ctrlplane-enhanced — Development Phase Tracking

## Phase 0: Research & Architecture (Week 1–2)

### Tasks
- [x] Fork Ctrlplane at v0.8.0; document all existing API endpoints
- [x] Run Ctrlplane test suite as baseline (upstream Go tests)
- [x] Identify top-3 pain points from GitHub Issues + community Slack:
  1. No deployment risk quantification before execution
  2. Manual rollback process (avg 8 min incident to rollback)
  3. Pipeline YAML requires days of learning curve for new teams
- [x] Define 3 quantified improvement targets:
  1. Reduce time-to-rollback from 8 min → < 60s (automated detection + trigger)
  2. Reduce post-deploy incidents by 30% via pre-deploy risk gating (risk_score > 0.7 → block)
  3. Reduce pipeline YAML authoring time from 1–2 days → < 5 minutes (NL generation)
- [x] Architecture decision: Python sidecar via REST API (no upstream modifications)
- [x] Select HuggingFace models (BGE-large, MiniLM, CodeT5+, BART)

### Deliverables
- [x] CLAUDE.md
- [x] PROJECT-detail.md
- [x] upstream/README.md (version pin + improvement delta)

### Success Criteria
- Architecture diagram reviewed
- All 3 improvement targets have measurable success metrics
- Upstream Ctrlplane API endpoints documented

---

## Phase 1: Core Agent Modules (Week 3–5)

### Tasks
- [x] Implement `agent/modules/risk_scorer.py`:
  - 8-feature extraction from Ctrlplane API + Prometheus API
  - XGBoost binary classifier with 500-sample synthetic bootstrap
  - Rule-based heuristic fallback for < 50 samples
  - `record_deployment_outcome()` for incremental retraining
- [x] Implement `agent/modules/auto_rollback.py`:
  - Prometheus API client for 6 golden signals
  - IsolationForest with 30-day rolling baseline
  - Ctrlplane rollback API trigger
  - LLM incident summary generation
- [x] Implement `agent/modules/nl_pipeline_generator.py`:
  - Claude API pipeline YAML generation with few-shot examples
  - MiniLM template similarity retrieval
  - YAML validation (syntax + jsonschema + logical)
  - 3-attempt retry loop with error feedback
- [x] Implement `agent/modules/resource_scheduler.py`:
  - 9-feature extraction from pipeline metadata
  - XGBoost regression for duration/CPU/memory
  - 95% confidence interval calculation

### Deliverables
- [x] All 4 modules with unit tests
- [x] Synthetic training data generator for risk_scorer bootstrap

### Success Criteria
- Risk scorer: Brier score ≤ 0.20 on held-out synthetic set
- Anomaly detector: < 5% false positive rate on synthetic normal data
- Pipeline generator: ≥ 90% first-attempt YAML validation pass rate
- Resource predictor: RMSE ≤ 120s on held-out build duration data

---

## Phase 2: Orchestrator + Quality Gates (Week 6–8)

### Tasks
- [x] Implement `agent/orchestrator.py`:
  - `CtrlplaneOrchestrator` class with lazy module initialization
  - `score_deployment()` → risk scoring flow
  - `monitor_deployment()` → async anomaly monitoring loop
  - `generate_pipeline()` → NL pipeline generation flow
  - `schedule_resources()` → resource prediction flow
- [x] Implement `agent/memory/memory_manager.py`:
  - SQLite tables: risk_assessments, deployments, rollbacks, pipeline_templates, build_outcomes, llm_cost_log, knowledge_hashes
  - `find_similar_pipelines(embedding, top_k)` with cosine similarity
  - `record_deployment_outcome()` for model retraining trigger
- [x] Implement FastAPI server in `agent/main.py`:
  - POST /api/v1/agent/risk-score
  - POST /api/v1/agent/rollback-check
  - POST /api/v1/agent/generate-pipeline
  - POST /api/v1/agent/schedule-resources
  - GET /api/v1/agent/health
- [x] Quality gate enforcement in orchestrator

### Deliverables
- [x] agent/main.py (CLI + FastAPI server)
- [x] agent/orchestrator.py
- [x] agent/memory/memory_manager.py

### Success Criteria
- All 4 API endpoints return valid responses
- Orchestrator handles concurrent webhook events without deadlock
- All quality gates produce correct pass/fail decisions

---

## Phase 3: HuggingFace Model Integration (Week 9–10)

### Tasks
- [x] Implement `tools/hf_model_manager.py`:
  - Lazy loading for BGE-large, MiniLM, CodeT5+, BART
  - Local cache in ./models/ directory
  - ONNX export for BGE-large (2× inference speedup)
  - Idle unload after 30min inactivity
- [x] Integrate BGE-large embeddings into memory_manager pipeline template storage
- [x] Integrate MiniLM into nl_pipeline_generator template retrieval (< 20ms target)
- [x] Integrate CodeT5+ for YAML syntax verification in pipeline generator
- [x] Integrate BART for incident summary in auto_rollback

### Deliverables
- [x] tools/hf_model_manager.py
- [x] Model integration tests

### Success Criteria
- MiniLM embedding latency < 20ms (batch of 10)
- BGE-large embedding quality: top-1 accuracy > 85% on pipeline template retrieval
- BART incident summary: coherent 3-sentence summary from 500-token log input

---

## Phase 4: LLM API Integration (Week 11–12)

### Tasks
- [x] Implement `tools/llm_client.py`:
  - Unified interface: Claude / OpenAI / Ollama
  - Streaming support for long pipeline YAML generation
  - Retry with exponential backoff (1s, 2s, 4s)
  - Cost tracking to SQLite `llm_cost_log`
  - Provider cascade: Claude → OpenAI → Ollama
- [x] Prompt engineering for 4 use cases:
  - Risk explanation (given features + score → plain-English reason)
  - Incident summary (given anomaly signals → runbook entry)
  - Pipeline YAML generation (given description + examples → YAML)
  - Pipeline fix (given YAML + validation error → corrected YAML)
- [x] Test all 4 prompts with Claude, OpenAI, and Ollama
- [x] Validate token budget: risk explanation < 512 output; YAML generation < 4096 output

### Deliverables
- [x] tools/llm_client.py
- [x] Prompt templates in config/agent_config.yaml

### Success Criteria
- Claude: all 4 prompt types succeed in < 10s (non-streaming)
- Cascade: OpenAI fallback triggers correctly when Claude unavailable
- Cost: risk explanation < $0.002 per call (Claude claude-opus-4-8 pricing)

---

## Phase 5: SECOND-KNOWLEDGE-BRAIN Pipeline (Week 13–14)

### Tasks
- [x] Implement `tools/knowledge_updater.py`:
  - ArXiv API: cs.SE, cs.DC categories; keyword filter: CI/CD, deployment risk, pipeline, anomaly detection
  - GitHub API: Ctrlplane releases + issues (label: enhancement)
  - GitHub Engineering Blog: RSS feed parser
  - CNCF Blog: RSS feed parser
  - ICSE/MSR: Semantic Scholar API for conference paper search
  - Scoring: recency (last 90 days) × relevance (keyword match) → top-20 per run
  - Dedup: URL/DOI SHA256 hash stored in memory_manager.knowledge_hashes table
- [x] Run first knowledge crawl → populate SECOND-KNOWLEDGE-BRAIN.md
- [x] APScheduler: weekly Sunday 02:00 automatic update
- [x] Verify dedup logic prevents duplicate entries

### Deliverables
- [x] tools/knowledge_updater.py
- [x] SECOND-KNOWLEDGE-BRAIN.md with first crawl results

### Success Criteria
- First crawl: ≥ 15 unique papers/resources added
- Dedup: zero duplicate entries after 3 consecutive runs
- Weekly schedule: fires correctly at configured time

---

## Phase 6: Docker + Testing (Week 15–16)

### Tasks
- [x] Implement `docker/docker-compose.yml`:
  - Service 1: `ctrlplane-ai-agent` (Python sidecar, this project)
  - Service 2: `ctrlplane` (upstream server, pinned v0.8.0 image)
  - Service 3: `postgres` (Ctrlplane database)
  - Service 4: `prometheus` (metrics scraping)
  - Service 5: `ollama` (local LLM, optional)
- [x] Write `tests/test_agent.py`:
  - 7 risk scorer unit tests (feature extraction, XGBoost predict, heuristic fallback)
  - 5 auto-rollback tests (anomaly detection, rollback trigger, monitoring loop)
  - 5 NL pipeline generator tests (YAML generation, validation, retry logic)
  - 5 resource scheduler tests (prediction accuracy, confidence interval)
  - 3 memory manager tests (CRUD, similar pipeline retrieval, outcome recording)
  - 3 integration tests (end-to-end risk flow, pipeline flow, rollback flow)
  - 5 CLI/API smoke tests
- [x] Write `tests/test-scenarios.md` (7 scenarios)
- [x] Run all tests → fix failures
- [x] docker-compose up → verify all services healthy

### Deliverables
- [x] docker/docker-compose.yml + docker/Dockerfile
- [x] tests/test_agent.py (33 tests)
- [x] tests/test-scenarios.md

### Success Criteria
- All 33 automated tests pass
- docker-compose up completes in < 3 minutes
- All 7 test scenarios produce expected outputs

---

## Phase 7: Cross-Agent Wiring & Deployment (Week 17–18)

### Tasks
- [x] Verify compatibility with coroot-enhanced (folder 11) Prometheus metrics format
- [x] Webhook integration: ctrlplane-enhanced → coroot-enhanced for incident correlation
- [x] Verify Ctrlplane API compatibility with latest upstream release
- [x] Performance test: 100 concurrent risk scoring requests → P95 < 500ms
- [x] Load test NL pipeline generator: 10 concurrent requests → P95 < 15s
- [x] Write deployment runbook in upstream/README.md

### Deliverables
- [x] Cross-agent webhook integration tested
- [x] Performance benchmark report
- [x] Deployment runbook

### Success Criteria
- P95 risk scoring latency < 500ms under 100 concurrent requests
- Zero memory leaks in 24h stress test
- Cross-agent webhook delivers incident data in < 1s
