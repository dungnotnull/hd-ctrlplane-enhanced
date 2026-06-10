# ctrlplane-enhanced — CI/CD Intelligence Agent (Ctrlplane Fork)

**Tagline:** ML-powered deployment risk scoring, anomaly-triggered auto-rollback, and NL pipeline generation on top of Ctrlplane.

**Current Build Phase:** Phase 1 — Core Agent Modules

**Upstream:** Ctrlplane v0.8.0 (https://github.com/ctrlplanedev/ctrlplane)

---

## Problem Statement

Modern CI/CD pipelines deploy frequently but lack proactive intelligence: engineers discover failures only after production impact, risk assessment is manual and inconsistent, and writing pipeline YAML requires deep platform expertise. This agent adds a sidecar AI layer on top of Ctrlplane that quantifies deployment risk before execution, detects anomalies in golden signals and triggers rollback automatically, generates pipeline YAML from natural language descriptions, and predicts resource requirements for upcoming builds — all while continuously learning from the latest CI/CD and MLOps research.

---

## Agent Architecture

```
User / Ctrlplane Events
        ↓
┌─────────────────────────────────────────────────────┐
│  Orchestrator (agent/orchestrator.py)               │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │  Planner     │→ │  Executor    │                 │
│  └──────────────┘  └──────────────┘                 │
│         ↕                  ↕                        │
│  ┌────────────────────────────────────────────────┐  │
│  │ Modules                                        │  │
│  │  risk_scorer.py        auto_rollback.py        │  │
│  │  nl_pipeline_generator.py  resource_scheduler  │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         ↓               ↓               ↓
    LLM API (Claude)  HuggingFace   Ctrlplane REST API
    (llm_client)      (hf_model_mgr) + Prometheus
         ↓
  Risk Report / Pipeline YAML / Rollback Action
```

**Step-by-step execution:**
1. Ctrlplane webhook triggers → orchestrator receives deployment event
2. `risk_scorer.py` extracts 8 features → XGBoost predicts rollback probability
3. If risk > threshold → LLM generates plain-language risk explanation + mitigation
4. Post-deploy: `auto_rollback.py` monitors golden signals via Prometheus API → isolation forest detects anomaly → rollback via Ctrlplane API if triggered
5. On NL pipeline request: `nl_pipeline_generator.py` → Claude API → validated YAML → POST to Ctrlplane
6. `resource_scheduler.py` pre-allocates compute based on XGBoost predictions for queue optimization

---

## Module List (`agent/modules/`)

| File | Responsibility |
|------|---------------|
| `risk_scorer.py` | XGBoost risk scoring: extract 8 deployment features → predict rollback probability (0–1) |
| `auto_rollback.py` | Isolation forest on Prometheus golden signals (error rate, latency P95, CPU, memory) → auto-rollback via Ctrlplane API |
| `nl_pipeline_generator.py` | Claude API: parse NL pipeline description → generate validated Ctrlplane pipeline YAML |
| `resource_scheduler.py` | XGBoost regression: predict build duration + CPU/memory requirements from pipeline metadata |

---

## Tools (`agent/tools/` — inside agent/ for domain tools)

No additional agent-level tools beyond the universal three.

---

## HuggingFace Models

| Model ID | Task | Reason Chosen |
|----------|------|---------------|
| `BAAI/bge-large-en-v1.5` | Pipeline config text embeddings for template similarity search | Top-ranked on MTEB; outperforms OpenAI ada-002 on retrieval |
| `sentence-transformers/all-MiniLM-L6-v2` | Fast pipeline template matching (< 20ms) | 5× faster than bge-large; sufficient for real-time NL matching |
| `Salesforce/codet5p-770m` | YAML/config syntax analysis and completion | Code-aware; handles structured config formats better than general LLMs |
| `facebook/bart-large-cnn` | Deployment incident summaries from log chunks | Strong extractive+abstractive summarization; outperforms T5 on DevOps text |

---

## LLM API Integration

| Provider | Use Case |
|----------|----------|
| Claude (claude-opus-4-8) | NL pipeline YAML generation, risk explanation, incident runbook drafting |
| OpenAI (gpt-4o) | Fallback for pipeline generation; structured JSON function calling for resource allocation |
| Ollama (llama3) | Offline mode for air-gapped deployments; local risk explanation generation |

---

## Knowledge Crawl Sources

| Source | ArXiv / URL | Update Frequency |
|--------|-------------|-----------------|
| ArXiv cs.SE | Deployment risk, CI/CD intelligence, pipeline optimization | Weekly |
| ArXiv cs.DC | Distributed systems reliability, SRE, anomaly detection | Weekly |
| ICSE / MSR | Mining Software Repositories, deployment failure patterns | Monthly (conference proceedings) |
| Ctrlplane GitHub | Releases, changelogs, community issues | Weekly |
| GitHub Engineering Blog | CI/CD best practices, deployment reliability at scale | Weekly |
| CNCF Blog | Cloud-native deployment patterns, GitOps | Weekly |

---

## Supporting Tools (`tools/`)

| File | Responsibility |
|------|---------------|
| `knowledge_updater.py` | ArXiv cs.SE+cs.DC crawl + GitHub Engineering blog + Ctrlplane releases → SECOND-KNOWLEDGE-BRAIN.md weekly |
| `llm_client.py` | Unified Claude/OpenAI/Ollama client with streaming, retry, and cost tracking |
| `hf_model_manager.py` | Lazy-loading HuggingFace models (BGE-large, MiniLM, CodeT5+, BART) with ./models/ cache |

---

## Active Development Tasks

- [x] CLAUDE.md — agent identity and architecture
- [x] PROJECT-detail.md — full technical spec
- [x] PROJECT-DEVELOPMENT-PHASE-TRACKING.md — phase roadmap
- [x] SECOND-KNOWLEDGE-BRAIN.md — domain knowledge base
- [x] agent/main.py — CLI entry point (score/rollback/generate/schedule/serve/update-knowledge)
- [x] agent/orchestrator.py — CtrlplaneOrchestrator with lazy module init
- [x] agent/modules/risk_scorer.py — XGBoost deployment risk scoring
- [x] agent/modules/auto_rollback.py — isolation forest auto-rollback
- [x] agent/modules/nl_pipeline_generator.py — LLM NL-to-YAML pipeline generator
- [x] agent/modules/resource_scheduler.py — XGBoost resource prediction
- [x] agent/memory/memory_manager.py — SQLite persistent memory
- [x] tools/knowledge_updater.py — research paper crawl pipeline
- [x] tools/llm_client.py — unified LLM client
- [x] tools/hf_model_manager.py — HuggingFace model manager
- [x] config/agent_config.yaml — runtime configuration
- [x] config/.env.example — environment variable template
- [x] docker/docker-compose.yml — containerized deployment
- [x] tests/test-scenarios.md — 7 end-to-end test scenarios
- [x] tests/test_agent.py — automated tests
- [x] requirements.txt — Python dependencies
- [x] upstream/README.md — Ctrlplane v0.8.0 pin + improvement delta
- [x] ai_layer/patches/ctrlplane_ai_integration.md — integration notes
