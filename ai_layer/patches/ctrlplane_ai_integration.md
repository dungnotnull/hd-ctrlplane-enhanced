# ctrlplane-enhanced AI Integration Notes

## Overview

This document describes how the AI sidecar integrates with the upstream Ctrlplane v0.8.0 server.
No upstream Go code is modified. All AI intelligence is implemented in Python and communicates
with Ctrlplane via REST API.

---

## Integration Points

### 1. Deployment Event Webhook

**Direction:** Ctrlplane → AI Sidecar

Configure Ctrlplane to send deployment lifecycle events to the AI sidecar:

```yaml
# In Ctrlplane config (ctrlplane.yaml):
webhooks:
  - url: http://ctrlplane-ai-agent:8766/api/v1/agent/risk-score
    events:
      - deployment.created
  - url: http://ctrlplane-ai-agent:8766/api/v1/agent/rollback-check
    events:
      - deployment.completed
```

Alternatively, poll the Ctrlplane API every 30s:
```
GET /api/v1/deployments?status=active&since={last_check_ts}
```

### 2. Risk-Gated Deployment Flow

**Direction:** AI Sidecar → Ctrlplane API

When risk_score > 0.70, the sidecar can set a deployment to "blocked" status:
```
POST /api/v1/deployments/{id}/status
{"status": "blocked", "reason": "AI risk score: 0.82 — high rollback probability"}
```

This prevents the deployment from proceeding without engineer acknowledgment.

### 3. Auto-Rollback Trigger

**Direction:** AI Sidecar → Ctrlplane API

When golden signal anomaly detected:
```
POST /api/v1/deployments/{id}/rollback
{"reason": "AI anomaly detection: error_rate=0.35 (baseline: 0.01)"}
```

Ctrlplane handles the rollback mechanics; the sidecar only triggers the decision.

### 4. Pipeline YAML Push

**Direction:** AI Sidecar → Ctrlplane API

After NL pipeline generation and validation:
```
POST /api/v1/pipelines
Content-Type: application/yaml
<validated pipeline YAML>
```

### 5. Prometheus Metrics Pull

**Direction:** AI Sidecar → Prometheus API

The sidecar queries Prometheus directly (not via Ctrlplane) for golden signals:
```
GET http://prometheus:9090/api/v1/query?query=<promql>
```

---

## Environment Variable Mapping

| Env Var | Used By | Purpose |
|---------|---------|---------|
| `CTRLPLANE_URL` | auto_rollback, orchestrator | Upstream server base URL |
| `CTRLPLANE_API_KEY` | auto_rollback, orchestrator | API authentication |
| `PROMETHEUS_URL` | auto_rollback | Golden signal metrics source |
| `ANTHROPIC_API_KEY` | llm_client | Primary LLM provider |
| `OPENAI_API_KEY` | llm_client | Fallback LLM provider |
| `OLLAMA_BASE_URL` | llm_client | Offline LLM provider |

---

## Sidecar Service Ports

| Service | Port | Protocol |
|---------|------|----------|
| ctrlplane-ai-agent | 8766 | HTTP (FastAPI) |
| Ctrlplane upstream | 3000 | HTTP |
| Prometheus | 9090 | HTTP |
| Ollama | 11434 | HTTP |

---

## Security Notes

1. The AI sidecar inherits Ctrlplane's API key for all Ctrlplane API calls — use a dedicated service account API key with minimum required permissions.
2. All LLM API calls are logged to `llm_cost_log` table; no deployment metadata is sent to LLM providers beyond what is in the prompt.
3. Auto-rollback can be disabled globally via `auto_rollback.enabled: false` in `config/agent_config.yaml` or by unsetting `CTRLPLANE_API_KEY`.
4. Golden signal metrics from Prometheus are read-only; the sidecar never modifies Prometheus configuration.

---

## Upgrade Path

When upgrading from Ctrlplane v0.8.0 to a newer version:
1. Check Ctrlplane changelog for API breaking changes
2. Run `tests/test_agent.py` — integration tests will fail if API endpoints changed
3. Update `orchestrator.py` API calls if needed
4. Update `upstream/README.md` with new pinned version
5. Update `docker-compose.yml` image tag
