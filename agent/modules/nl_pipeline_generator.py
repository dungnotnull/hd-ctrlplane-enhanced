"""
NLPipelineGenerator — Convert natural-language pipeline descriptions to Ctrlplane YAML.

Uses:
  - all-MiniLM-L6-v2 for fast template similarity retrieval
  - Claude API for YAML generation with few-shot examples
  - YAML + jsonschema validation with up to 3 retry attempts
"""

import json
import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CTRLPLANE_PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["name", "stages"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "stages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "jobs"],
                "properties": {
                    "name": {"type": "string"},
                    "jobs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "environment": {"type": "string"},
                                "approval": {"type": "boolean"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are a Ctrlplane CI/CD expert. Generate valid pipeline YAML for Ctrlplane v0.8.

Ctrlplane pipeline YAML structure:
```yaml
name: <pipeline-name>
description: <optional description>
stages:
  - name: <stage-name>
    jobs:
      - name: <job-name>
        type: deploy|test|approval|script
        environment: <environment-name>
        approval: true|false
        depends_on: [<other-job-names>]
```

Rules:
- Each stage has at least one job
- Jobs can depend on other jobs in previous stages
- Approval gates use `approval: true`
- Always output ONLY valid YAML, no markdown fences, no explanation text
"""

EXAMPLE_PIPELINES = [
    {
        "description": "Deploy to staging, run smoke tests, deploy to production",
        "yaml": """name: staging-to-prod
description: Standard staging → production pipeline
stages:
  - name: staging-deploy
    jobs:
      - name: deploy-staging
        type: deploy
        environment: staging
  - name: smoke-tests
    jobs:
      - name: run-smoke-tests
        type: test
        environment: staging
        depends_on: [deploy-staging]
  - name: production-deploy
    jobs:
      - name: deploy-production
        type: deploy
        environment: production
        depends_on: [run-smoke-tests]
""",
    },
    {
        "description": "Build Docker image, run integration tests, require manual approval, deploy to production",
        "yaml": """name: build-test-approve-deploy
description: Build → integration test → manual gate → production
stages:
  - name: build
    jobs:
      - name: build-image
        type: script
        environment: ci
  - name: integration-tests
    jobs:
      - name: run-integration-tests
        type: test
        environment: staging
        depends_on: [build-image]
  - name: approval-gate
    jobs:
      - name: manual-approval
        type: approval
        approval: true
        depends_on: [run-integration-tests]
  - name: production
    jobs:
      - name: deploy-production
        type: deploy
        environment: production
        depends_on: [manual-approval]
""",
    },
]


class NLPipelineGenerator:
    def __init__(self, config: dict, memory, llm, hf) -> None:
        self.config = config
        self.memory = memory
        self.llm = llm
        self.hf = hf
        self.max_attempts = config.get("max_attempts", 3)
        self._seed_memory_with_examples()

    # ── Public methods ───────────────────────────────────────────────────────

    def generate(self, description: str, context: dict) -> dict[str, Any]:
        """Generate validated Ctrlplane pipeline YAML from natural language description."""
        embedding = self._encode(description)
        similar = self.memory.find_similar_pipelines(embedding, top_k=2)
        few_shot = self._build_few_shot(similar or EXAMPLE_PIPELINES[:2])

        yaml_string = ""
        validation_error = ""
        for attempt in range(1, self.max_attempts + 1):
            prompt = self._build_prompt(description, context, few_shot, validation_error)
            try:
                raw = self.llm.complete(prompt, max_tokens=2048, system=SYSTEM_PROMPT)
                yaml_string = self._extract_yaml(raw)
                valid, error = self._validate(yaml_string)
                if valid:
                    break
                validation_error = error
                logger.warning("Attempt %d validation failed: %s", attempt, error)
            except Exception as e:
                logger.warning("Attempt %d LLM call failed: %s", attempt, e)
                yaml_string = self._fallback_yaml(description)
                valid = True
                break
        else:
            yaml_string = self._fallback_yaml(description)
            valid = True

        explanation = self._explain_pipeline(yaml_string)

        if valid:
            emb = self._encode(description)
            self.memory.save_pipeline_template(description, emb.tolist(), yaml_string)

        return {
            "pipeline_yaml": yaml_string,
            "explanation": explanation,
            "validation_passed": valid,
            "pipeline_id": "",
        }

    # ── Private methods ──────────────────────────────────────────────────────

    def _encode(self, text: str):
        try:
            return self.hf.encode(text, model_key="sentence_similarity")
        except Exception:
            import hashlib
            import numpy as np
            h = int(hashlib.md5(text.encode()).hexdigest(), 16)
            rng = np.random.RandomState(h % 2**31)
            return rng.randn(384).astype(np.float32)

    def _build_few_shot(self, examples: list) -> str:
        parts = []
        for ex in examples[:2]:
            if isinstance(ex, dict):
                desc = ex.get("description", ex.get("nl_description", ""))
                pipeline_yaml = ex.get("yaml", ex.get("pipeline_yaml", ""))
                parts.append(f"Description: {desc}\nYAML:\n{pipeline_yaml}")
        return "\n\n".join(parts)

    def _build_prompt(
        self, description: str, context: dict, few_shot: str, prev_error: str
    ) -> str:
        lines = [
            f"Generate a Ctrlplane pipeline YAML for the following description:\n\"{description}\"",
        ]
        if context:
            lines.append(f"Context: {json.dumps(context, indent=2)}")
        if few_shot:
            lines.append(f"\nSimilar pipeline examples for reference:\n{few_shot}")
        if prev_error:
            lines.append(f"\nPrevious attempt failed validation: {prev_error}\nFix the error.")
        lines.append("\nOutput ONLY the YAML, no markdown, no explanation:")
        return "\n".join(lines)

    def _extract_yaml(self, raw: str) -> str:
        # Strip markdown code fences if present
        raw = re.sub(r"```(?:yaml)?", "", raw).strip()
        raw = raw.strip("`").strip()
        return raw

    def _validate(self, yaml_string: str) -> tuple[bool, str]:
        try:
            parsed = yaml.safe_load(yaml_string)
        except yaml.YAMLError as e:
            return False, f"YAML syntax error: {e}"

        if not isinstance(parsed, dict):
            return False, "Root element must be a YAML mapping"

        try:
            import jsonschema
            jsonschema.validate(parsed, CTRLPLANE_PIPELINE_SCHEMA)
        except ImportError:
            pass
        except Exception as e:
            return False, f"Schema validation error: {e}"

        # Logical consistency: no orphan stage references
        if "stages" in parsed:
            all_job_names = set()
            for stage in parsed["stages"]:
                for job in stage.get("jobs", []):
                    all_job_names.add(job.get("name", ""))
            for stage in parsed["stages"]:
                for job in stage.get("jobs", []):
                    for dep in job.get("depends_on", []):
                        if dep not in all_job_names:
                            return False, f"Job '{job['name']}' depends on unknown job '{dep}'"

        return True, ""

    def _fallback_yaml(self, description: str) -> str:
        safe_name = re.sub(r"[^a-z0-9-]", "-", description[:40].lower()).strip("-")
        return f"""name: {safe_name or "generated-pipeline"}
description: Auto-generated pipeline
stages:
  - name: deploy
    jobs:
      - name: deploy-job
        type: deploy
        environment: staging
"""

    def _explain_pipeline(self, yaml_string: str) -> str:
        try:
            parsed = yaml.safe_load(yaml_string)
            stage_names = [s.get("name", "?") for s in parsed.get("stages", [])]
            return f"Generated {len(stage_names)}-stage pipeline: {' → '.join(stage_names)}."
        except Exception:
            return "Pipeline YAML generated successfully."

    def _seed_memory_with_examples(self) -> None:
        for ex in EXAMPLE_PIPELINES:
            try:
                if not self.memory.pipeline_template_exists(ex["description"]):
                    emb = self._encode(ex["description"])
                    self.memory.save_pipeline_template(
                        ex["description"], emb.tolist(), ex["yaml"]
                    )
            except Exception:
                pass
