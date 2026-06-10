<div align="center">

# 🧠 ctrlplane-enhanced

**ML-Powered CI/CD Intelligence Agent**

*Risk Scoring · Auto-Rollback · NL Pipeline Generation · Resource Scheduling*

[![CI](https://img.shields.io/badge/CI-passing-brightgreen?style=flat-square)](./.github/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue?style=flat-square)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](./pyproject.toml)
[![Tests](https://img.shields.io/badge/Tests-33%20passing-2EA44F?style=flat-square)](./tests/test_agent.py)

---

</div>

## 🎯 What Is This?

**ctrlplane-enhanced** is a Python sidecar AI layer that adds four intelligence capabilities on top of [Ctrlplane](https://github.com/ctrlplanedev/ctrlplane) (open-source CI/CD orchestration platform, v0.8.0). It connects via REST API — **zero upstream code modifications required**.

| Capability | What It Does | Tech Stack |
|---|---|---|
| 🔴 **Deployment Risk Scoring** | Predicts rollback probability (0-1) before deployment | XGBoost binary classifier, 8 features |
| 🟠 **Anomaly-Triggered Auto-Rollback** | Monitors 6 Prometheus golden signals post-deploy; triggers rollback in < 60s | Isolation Forest, LLM incident summary |
| 🟢 **NL Pipeline Generation** | Converts plain-English descriptions into validated Ctrlplane pipeline YAML | Claude/GPT-4o/Ollama, MiniLM retrieval |
| 🔵 **Predictive Resource Scheduling** | Forecasts build duration, CPU, and memory from pipeline metadata | XGBoost regression, 9 features |

---

## 🏗️ Architecture

```
                      ┌──────────────────────────────────────────┐
                      │       Ctrlplane REST API (Go server)      │
                      │   POST /api/v1/deployments                │
                      │   POST /api/v1/pipelines                  │
                      │   POST /api/v1/deployments/{id}/rollback  │
                      └─────────────┬────────────────────────────┘
                                    │  webhook / poll
                      ┌─────────────▼────────────────────────────┐
                      │   🧠 ctrlplane-enhanced AI Sidecar        │
                      │   (Python, FastAPI on :8766)              │
                      │                                           │
                      │   ┌──────────────┐  ┌──────────────────┐ │
                      │   │  Orchestrator │  │  Module Router    │ │
                      │   └──────┬───────┘  └──┬───┬───┬───────┘ │
                      │          │             │   │   │           │
                      │   ┌──────▼──┐ ┌────▼─┐ ┌▼──┐ ┌▼──────┐  │
                      │   │ Risk    │ │Roll  │ │NL │ │ Sched  │  │
                      │   │ Scorer  │ │back  │ │Gen│ │ uler   │  │
                      │   └────┬────┘ └──┬───┘ └┬──┘ └──┬─────┘  │
                      └──────┼────────┼──────┼───────┼─────────┘
                             │        │      │       │
                  ┌──────────▼──┐ ┌──▼───┐ ┌▼────┐ ┌▼──────────┐
                  │ XGBoost     │ │IsoFo │ │Claude│ │XGBoost    │
                  │ Risk Model  │ │rest  │ │ API  │ │Scheduler  │
                  └─────────────┘ └──────┘ └──────┘ └────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for full-stack deployment)
- API keys: [Anthropic](https://console.anthropic.com/) (primary), [OpenAI](https://platform.openai.com/) (fallback), or [Ollama](https://ollama.ai/) (offline)

### Option A: pip install

```bash
# Clone the repository
git clone https://github.com/dungnotnull/hd-ctrlplane-enhanced.git
cd hd-ctrlplane-enhanced

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp config/.env.example .env
# Edit .env with your API keys

# Run CLI commands
python -m agent.main score dep-001 --metadata '{"service_count": 5}'
python -m agent.main generate "Deploy to staging then production"
python -m agent.main serve --host 0.0.0.0 --port 8766
```

### Option B: Docker Compose

```bash
cd docker
docker-compose up -d

# Verify all services are healthy
curl http://localhost:8766/api/v1/agent/health
# {"status":"healthy","version":"1.0.0","upstream":"ctrlplane-v0.8.0"}
```

### Option C: pip install (editable)

```bash
pip install -e ".[dev]"
ctrlplane-agent serve --port 8766
```

---

## 📡 API Reference

All endpoints served on the AI sidecar (default port `:8766`). Authentication via `X-API-Key` header (configure with `CTRLPLANE_AGENT_API_KEY` env var).

### Health Check

```
GET /api/v1/agent/health
```

```json
{"status": "healthy", "version": "1.0.0", "upstream": "ctrlplane-v0.8.0"}
```

### 🔴 Risk Score

```
POST /api/v1/agent/risk-score
```

```bash
curl -X POST http://localhost:8766/api/v1/agent/risk-score \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "deployment_id": "dep-001",
    "deployment_metadata": {
      "service_count": 5,
      "test_coverage_pct": 80
    }
  }'
```

**Response:**

```json
{
  "deployment_id": "dep-001",
  "risk_score": 0.72,
  "risk_tier": "high",
  "explanation": "Deployment risk is primarily driven by low test coverage...",
  "block_recommendation": true,
  "contributing_features": {"test_coverage_pct": 0.25, "deployment_size": 0.18}
}
```

### 🟠 Rollback Check

```
POST /api/v1/agent/rollback-check
```

```json
{
  "deployment_id": "dep-001",
  "services": ["payment-service", "api-gateway"],
  "prometheus_url": "http://prometheus:9090"
}
```

### 🟢 Generate Pipeline

```
POST /api/v1/agent/generate-pipeline
```

```json
{
  "description": "Deploy my Node.js API to staging, run smoke tests, then deploy to production"
}
```

### 🔵 Schedule Resources

```
POST /api/v1/agent/schedule-resources
```

```json
{
  "pipeline_id": "pipe-001",
  "pipeline_metadata": {
    "step_count": 8,
    "service_count": 4,
    "environment": "production"
  }
}
```

---

## 💻 CLI Commands

```bash
# Score deployment risk
python -m agent.main score <deployment_id> [--metadata JSON]

# Check rollback eligibility
python -m agent.main rollback <deployment_id> [--services svc1 svc2] [--prometheus-url URL]

# Generate pipeline YAML from natural language
python -m agent.main generate "<description>" [--output FILE]

# Predict resource requirements
python -m agent.main schedule <pipeline_id> [--metadata JSON]

# Start FastAPI server
python -m agent.main serve [--host HOST] [--port PORT] [--reload]

# Run knowledge crawl immediately
python -m agent.main update-knowledge
```

---

## 📁 Project Structure

```
ctrlplane-enhanced/
├── 🧠 agent/
│   ├── main.py                     # CLI + FastAPI server (5 endpoints)
│   ├── orchestrator.py              # Central coordinator with lazy module init
│   ├── memory/
│   │   └── memory_manager.py        # SQLite persistent storage (8 tables)
│   └── modules/
│       ├── risk_scorer.py           # XGBoost deployment risk scoring (8 features)
│       ├── auto_rollback.py         # Isolation Forest anomaly detection (6 signals)
│       ├── nl_pipeline_generator.py # Claude/GPT NL-to-YAML (3-attempt retry)
│       └── resource_scheduler.py    # XGBoost regression (duration/CPU/memory)
├── 🔧 tools/
│   ├── llm_client.py               # Claude/OpenAI/Ollama with cost tracking
│   ├── hf_model_manager.py          # Lazy-loading HuggingFace model registry
│   └── knowledge_updater.py         # ArXiv + GitHub + RSS research crawl
├── ⚙️ config/
│   ├── agent_config.yaml            # Runtime configuration
│   └── .env.example                 # Environment variable template
├── 🐳 docker/
│   ├── Dockerfile                   # Multi-stage Python build
│   ├── docker-compose.yml           # Full-stack: agent + ctrlplane + postgres + prometheus
│   └── prometheus.yml               # Golden signal scrape config
├── 🧪 tests/
│   ├── test_agent.py                # 33 automated tests
│   └── test-scenarios.md            # 7 end-to-end test scenarios
├── 📚 docs/
│   ├── PROJECT-detail.md            # Full technical specification
│   ├── PROJECT-DEVELOPMENT-PHASE-TRACKING.md  # Phase roadmap
│   ├── SECOND-KNOWLEDGE-BRAIN.md    # Research knowledge base (18 papers + 8 models)
│   └── ai_layer/patches/ctrlplane_ai_integration.md  # Integration guide
├── pyproject.toml                   # Package metadata and build config
├── requirements.txt                 # Python dependencies
├── LICENSE                          # Apache-2.0
├── CONTRIBUTING.md                  # Developer contribution guide
├── SECURITY.md                      # Vulnerability disclosure policy
├── CODE_OF_CONDUCT.md               # Contributor Covenant
├── CHANGELOG.md                     # Release history
└── README.md                        # This file
```

---

## 🤖 HuggingFace Models

Models are lazily downloaded on first use and cached in `./models/`. For air-gapped deployments, pre-download with:

```bash
python -c "from tools.hf_model_manager import HFModelManager; HFModelManager().encode('test', 'sentence_similarity')"
```

| Model | Task | Size | Why |
|-------|-----|------|-----|
| `BAAI/bge-large-en-v1.5` | Pipeline template similarity search | ~1.3 GB | #1 on MTEB retrieval benchmark |
| `sentence-transformers/all-MiniLM-L6-v2` | Real-time NL matching (< 20ms) | ~90 MB | 5x faster than bge-large |
| `Salesforce/codet5p-770m` | YAML syntax analysis | ~1.5 GB | Code-aware, handles structured config |
| `facebook/bart-large-cnn` | Incident log summarization | ~1.6 GB | ROUGE-L 44.16 on CNN/DM |

---

## ⚙️ Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CTRLPLANE_URL` | Yes | `http://localhost:3000` | Ctrlplane upstream server URL |
| `CTRLPLANE_API_KEY` | Yes | — | Ctrlplane API key |
| `CTRLPLANE_AGENT_API_KEY` | No | *(empty = open)* | Sidecar API key (set in production!) |
| `PROMETHEUS_URL` | Yes | `http://localhost:9090` | Prometheus metrics URL |
| `ANTHROPIC_API_KEY` | Yes* | — | Primary LLM provider |
| `OPENAI_API_KEY` | No | — | Fallback LLM provider |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Offline LLM provider |
| `RATE_LIMIT_ENABLED` | No | `true` | Enable rate limiting |
| `RATE_LIMIT_PER_MINUTE` | No | `60` | Max requests per minute per client |

*At least one LLM provider is required for NL pipeline generation and risk explanations.

### Runtime Configuration

All settings live in `config/agent_config.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `risk_scorer.risk_threshold` | `0.70` | Score above which deployment is blocked |
| `auto_rollback.anomaly_threshold` | `-0.30` | Isolation Forest score below which anomaly triggers |
| `auto_rollback.max_rollback_age_seconds` | `3600` | Max deployment age for auto-rollback |
| `nl_pipeline.max_attempts` | `3` | Max LLM retry attempts |
| `llm.primary` | `claude` | Primary LLM provider |
| `llm.fallback` | `openai` | Fallback LLM provider |
| `llm.offline` | `ollama` | Offline LLM provider |

---

## 🧪 Testing

```bash
# Run all 33 tests
pytest tests/test_agent.py -v

# Run with coverage
pytest tests/test_agent.py -v --cov=agent --cov=tools --cov-report=term-missing

# Lint
ruff check . --ignore E501

# Type check
mypy agent/ tools/ --ignore-missing-imports
```

### Test Coverage

| Module | Tests | Coverage |
|--------|-------|----------|
| Risk Scorer | 7 | Feature extraction, XGBoost predict, heuristic fallback, high/low profiles |
| Auto-Rollback | 5 | Anomaly detection, rollback trigger, incident summary, baseline fitting |
| NL Pipeline | 5 | Valid YAML, retry on invalid, fallback, orphan detection, memory seeding |
| Resource Scheduler | 5 | All fields, positive duration, CI bounds, production headroom, queue warning |
| Memory Manager | 3 | CRUD, similarity search, dedup |
| Integration | 3 | Risk-to-memory, pipeline-to-memory, scheduler outcome |
| CLI Smoke | 5 | All subcommands exist and parse |

---

## 🔒 Security

- **API Authentication**: All endpoints (except health) require `X-API-Key` header when `CTRLPLANE_AGENT_API_KEY` is set
- **Rate Limiting**: Configurable per-client rate limiting (default: 60 requests/minute)
- **No hardcoded secrets**: All credentials via environment variables
- **Graceful shutdown**: SQLite connections properly closed on SIGTERM
- **Cost tracking**: All LLM API calls logged with token counts and estimated cost

See [SECURITY.md](./SECURITY.md) for vulnerability disclosure policy.

---

## 🐳 Docker Deployment

```bash
cd docker
docker-compose up -d
```

| Service | Port | Description |
|---------|------|-------------|
| ctrlplane-ai-agent | 8766 | Python AI sidecar (this project) |
| ctrlplane | 3000 | Upstream Ctrlplane Go server |
| postgres | 5432 | Ctrlplane database |
| prometheus | 9090 | Golden signal metrics |
| ollama | 11434 | Local LLM (optional, use `--profile offline`) |

---

## 🔄 LLM Provider Cascade

```
Request ──→ Claude (primary) ──→ OpenAI (fallback) ──→ Ollama (offline)
              │ retry 3x            │ retry 3x             │ retry 3x
              └── cost tracked ─────┴── cost tracked ───────┴── cost tracked
```

- 3-attempt retry with exponential backoff (1s, 2s, 4s)
- Automatic provider cascade on failure
- Token cost estimation logged to `llm_cost_log` table

---

## 📊 How It Works

### Risk Scoring Flow

```
Ctrlplane webhook ──→ orchestrator.score_deployment()
                         ├── risk_scorer.extract_features() (8 features from API + memory)
                         ├── risk_scorer.predict() (XGBoost or heuristic fallback)
                         ├── IF risk_score >= 0.70 ──→ LLM generates plain-English explanation
                         └── memory.record_risk_assessment()
```

### Auto-Rollback Flow

```
Deployment completes ──→ orchestrator.monitor_deployment() (every 30s)
                          ├── auto_rollback.evaluate()
                          │     ├── Query Prometheus for 6 golden signals
                          │     ├── IsolationForest.predict() ──→ anomaly_score
                          │     ├── IF anomaly_score < -0.30 AND age < 3600s
                          │     │     ├── POST /api/v1/deployments/{id}/rollback
                          │     │     └── LLM generates incident summary
                          │     └── ELSE: continue monitoring
                          └── memory.record_metric_sample()
```

### NL Pipeline Generation Flow

```
User: "Deploy to staging, test, then production"
  ├── MiniLM encode ──→ find_similar_pipelines(top_k=2)
  ├── Build Claude prompt with few-shot examples
  ├── Claude generates YAML ──→ validate (syntax + schema + logic)
  ├── IF invalid ──→ retry with error feedback (max 3)
  └── Return validated YAML + explanation
```

---

## 📈 Quality Gates

| Metric | Target | Implementation |
|--------|--------|----------------|
| Risk score calibration | Brier score <= 0.15 | XGBoost with 500-sample synthetic bootstrap |
| Auto-rollback precision | False positive rate <= 5% | Isolation Forest contamination=0.05, 200+ baseline samples |
| Pipeline YAML validity | 100% schema validation | jsonschema + logical consistency check |
| LLM retry success | >= 90% within 3 attempts | 3-attempt retry with error feedback |
| Risk scoring latency | <= 500ms P95 | Heuristic fallback for < 50 samples |

---

## 🗺️ Roadmap

- [x] Phase 0: Research & Architecture
- [x] Phase 1: Core Agent Modules (risk scorer, auto-rollback, NL generator, scheduler)
- [x] Phase 2: Orchestrator + Quality Gates
- [x] Phase 3: HuggingFace Model Integration
- [x] Phase 4: LLM API Integration (Claude/OpenAI/Ollama cascade)
- [x] Phase 5: SECOND-KNOWLEDGE-BRAIN Pipeline
- [x] Phase 6: Docker + Testing (33 tests)
- [x] Phase 7: Cross-Agent Wiring & Deployment

---

## 🤝 Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup, coding standards, and PR process.

## 📄 License

[Apache-2.0](./LICENSE) — same as upstream [Ctrlplane](https://github.com/ctrlplanedev/ctrlplane).

## 🙏 Acknowledgments

- [Ctrlplane](https://github.com/ctrlplanedev/ctrlplane) — upstream CI/CD orchestration platform
- [XGBoost](https://xgboost.ai/) — gradient-boosted decision trees
- [scikit-learn](https://scikit-learn.org/) — Isolation Forest implementation
- [Anthropic](https://www.anthropic.com/) — Claude LLM API
- [HuggingFace](https://huggingface.co/) — sentence-transformers, BGE, CodeT5+, BART

<div align="center">

**Built with ❤️ for the DevOps community**

</div>