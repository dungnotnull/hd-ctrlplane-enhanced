"""
HFModelManager — Lazy-loading HuggingFace model registry for ctrlplane-enhanced.

Models are downloaded on first use and cached in ./models/.
Idle models are unloaded after 30 minutes to conserve memory.
"""

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_REGISTRY = {
    "text_embedding": {
        "model_id": "BAAI/bge-large-en-v1.5",
        "task": "feature-extraction",
        "use_case": "Pipeline config template similarity search (top MTEB benchmark)",
    },
    "sentence_similarity": {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "task": "feature-extraction",
        "use_case": "Real-time NL pipeline template matching (< 20ms, 5x faster than bge-large)",
    },
    "code_analysis": {
        "model_id": "Salesforce/codet5p-770m",
        "task": "text2text-generation",
        "use_case": "YAML/pipeline config syntax analysis and completion",
    },
    "summarization": {
        "model_id": "facebook/bart-large-cnn",
        "task": "summarization",
        "use_case": "Incident log summarization for runbook entries (ROUGE-L 44.16)",
    },
}

IDLE_UNLOAD_SECONDS = 1800  # 30 minutes


class HFModelManager:
    def __init__(self, cache_dir: str = "./models") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._models: dict[str, Any] = {}
        self._tokenizers: dict[str, Any] = {}
        self._last_used: dict[str, float] = {}

    # ── Public methods ───────────────────────────────────────────────────────

    def encode(self, text: str, model_key: str = "sentence_similarity") -> np.ndarray:
        """Encode text to embedding vector using the specified model."""
        model, tokenizer = self._load_encoder(model_key)
        import torch
        with torch.no_grad():
            inputs = tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512, padding=True
            )
            outputs = model(**inputs)
            embedding = outputs.last_hidden_state[:, 0, :].squeeze().numpy()
        self._last_used[model_key] = time.time()
        return embedding.astype(np.float32)

    def encode_batch(self, texts: list[str], model_key: str = "sentence_similarity") -> np.ndarray:
        """Encode a batch of texts to embedding matrix."""
        model, tokenizer = self._load_encoder(model_key)
        import torch
        with torch.no_grad():
            inputs = tokenizer(
                texts, return_tensors="pt", truncation=True, max_length=512, padding=True
            )
            outputs = model(**inputs)
            embeddings = outputs.last_hidden_state[:, 0, :].numpy()
        self._last_used[model_key] = time.time()
        return embeddings.astype(np.float32)

    def summarize(self, text: str, max_length: int = 150, min_length: int = 30) -> str:
        """Summarize text using BART-large-cnn."""
        model_key = "summarization"
        pipe = self._load_pipeline(model_key)
        self._last_used[model_key] = time.time()
        result = pipe(
            text[:1024],  # BART max input
            max_length=max_length,
            min_length=min_length,
            do_sample=False,
        )
        return result[0]["summary_text"]

    def analyze_code(self, code: str, instruction: str = "Review this YAML for syntax issues:") -> str:
        """Use CodeT5+ to analyze/complete YAML config."""
        model_key = "code_analysis"
        pipe = self._load_pipeline(model_key)
        self._last_used[model_key] = time.time()
        prompt = f"{instruction}\n{code[:512]}"
        result = pipe(prompt, max_new_tokens=256)
        return result[0]["generated_text"]

    def cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Compute cosine similarity between two embedding vectors."""
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))

    def unload_idle(self) -> list[str]:
        """Unload models that haven't been used in IDLE_UNLOAD_SECONDS."""
        unloaded = []
        now = time.time()
        for key in list(self._models.keys()):
            if now - self._last_used.get(key, 0) > IDLE_UNLOAD_SECONDS:
                del self._models[key]
                self._tokenizers.pop(key, None)
                unloaded.append(key)
                logger.info("Unloaded idle model: %s", key)
        return unloaded

    def list_loaded(self) -> list[str]:
        return list(self._models.keys())

    # ── Private methods ──────────────────────────────────────────────────────

    def _load_encoder(self, model_key: str):
        if model_key not in self._models:
            info = MODEL_REGISTRY.get(model_key)
            if info is None:
                raise ValueError(f"Unknown model key: {model_key}")
            logger.info("Loading HuggingFace model: %s", info["model_id"])
            try:
                from transformers import AutoModel, AutoTokenizer
                tokenizer = AutoTokenizer.from_pretrained(
                    info["model_id"], cache_dir=str(self.cache_dir)
                )
                model = AutoModel.from_pretrained(
                    info["model_id"], cache_dir=str(self.cache_dir)
                )
                model.eval()
                self._models[model_key] = model
                self._tokenizers[model_key] = tokenizer
                self._last_used[model_key] = time.time()
                logger.info("Loaded %s successfully", info["model_id"])
            except Exception as e:
                logger.warning("Failed to load %s: %s — using random embedding fallback", info["model_id"], e)
                self._models[model_key] = None
                self._tokenizers[model_key] = None

        if self._models[model_key] is None:
            raise RuntimeError(f"Model {model_key} unavailable (failed to load)")
        return self._models[model_key], self._tokenizers[model_key]

    def _load_pipeline(self, model_key: str):
        if model_key not in self._models:
            info = MODEL_REGISTRY.get(model_key)
            if info is None:
                raise ValueError(f"Unknown model key: {model_key}")
            logger.info("Loading HuggingFace pipeline: %s", info["model_id"])
            try:
                from transformers import pipeline
                pipe = pipeline(
                    info["task"],
                    model=info["model_id"],
                    cache_dir=str(self.cache_dir),
                )
                self._models[model_key] = pipe
                self._last_used[model_key] = time.time()
                logger.info("Loaded pipeline %s successfully", info["model_id"])
            except Exception as e:
                logger.warning("Failed to load pipeline %s: %s", info["model_id"], e)
                self._models[model_key] = None

        if self._models[model_key] is None:
            raise RuntimeError(f"Pipeline {model_key} unavailable")
        return self._models[model_key]
