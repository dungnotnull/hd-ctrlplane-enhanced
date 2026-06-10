# Changelog

All notable changes to ctrlplane-enhanced will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-10

### Added
- Deployment Risk Scoring: XGBoost binary classifier predicting rollback probability from 8 deployment features
- Anomaly-Triggered Auto-Rollback: Isolation forest monitoring 6 Prometheus golden signals post-deploy
- NL Pipeline Generation: Claude/OpenAI/Ollama-powered natural language to validated Ctrlplane pipeline YAML
- Predictive Resource Scheduling: XGBoost regression forecasting build duration, CPU, and memory requirements
- FastAPI server with 5 endpoints (health, risk-score, rollback-check, generate-pipeline, schedule-resources)
- CLI with 5 commands (score, rollback, generate, schedule, serve, update-knowledge)
- SQLite persistent memory manager with risk assessments, deployment outcomes, pipeline templates, build outcomes
- HuggingFace model manager with lazy loading and idle unloading (BGE-large, MiniLM, CodeT5+, BART)
- LLM client with 3-provider cascade (Claude ? OpenAI ? Ollama) and cost tracking
- Knowledge updater with ArXiv, Semantic Scholar, GitHub, RSS crawl pipeline
- Docker multi-stage build and docker-compose for full-stack deployment
- API key authentication middleware and rate limiting (60 req/min default)
- Graceful shutdown with SQLite connection cleanup
- 33 automated tests covering all modules, integration, and CLI smoke tests
- Comprehensive project documentation (PROJECT-detail.md, SECOND-KNOWLEDGE-BRAIN.md, CLAUDE.md)

### Security
- API key authentication on all non-health endpoints
- Rate limiting middleware (configurable)
- Environment variable-based secrets management (.env)
- No hardcoded credentials in source code
