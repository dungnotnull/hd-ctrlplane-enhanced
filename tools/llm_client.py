"""
LLMClient â€” Unified client for Claude (primary), OpenAI (fallback), and Ollama (offline).

Supports streaming, retry with exponential backoff, and cost tracking.
Provider cascade: Claude â†’ OpenAI â†’ Ollama.
"""

import logging
import os
import time
from typing import Iterator

logger = logging.getLogger(__name__)

PROVIDER_MODELS = {
    "claude": os.getenv("CLAUDE_MODEL", "claude-opus-4-8"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "ollama": os.getenv("OLLAMA_MODEL", "llama3"),
}

COST_PER_1K = {
    "claude": {"in": 0.015, "out": 0.075},
    "openai": {"in": 0.005, "out": 0.015},
    "ollama": {"in": 0.0, "out": 0.0},
}


class LLMClient:
    def __init__(
        self,
        primary: str = "claude",
        fallback: str = "openai",
        offline: str = "ollama",
        memory=None,
    ) -> None:
        self.provider_order = [primary, fallback, offline]
        self.memory = memory
        self._clients: dict = {}

    # â”€â”€ Public methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1024,
        system: str = "",
        use_case: str = "",
    ) -> str:
        last_error = None
        for provider in self.provider_order:
            for attempt in range(3):
                try:
                    result = self._call(provider, prompt, max_tokens, system)
                    self._track_cost(provider, prompt, result, use_case)
                    return result
                except Exception as e:
                    last_error = e
                    wait = 2 ** attempt
                    logger.warning("%s attempt %d failed (%s); retry in %ds", provider, attempt + 1, e, wait)
                    time.sleep(wait)
            logger.error("Provider %s exhausted all retries", provider)
        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    def stream(
        self,
        prompt: str,
        max_tokens: int = 2048,
        system: str = "",
    ) -> Iterator[str]:
        for provider in self.provider_order:
            try:
                yield from self._stream(provider, prompt, max_tokens, system)
                return
            except Exception as e:
                logger.warning("Streaming failed for %s: %s", provider, e)
        raise RuntimeError("All streaming providers failed")

    # â”€â”€ Private provider implementations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _call(self, provider: str, prompt: str, max_tokens: int, system: str) -> str:
        if provider == "claude":
            return self._claude_complete(prompt, max_tokens, system)
        elif provider == "openai":
            return self._openai_complete(prompt, max_tokens, system)
        elif provider == "ollama":
            return self._ollama_complete(prompt, max_tokens, system)
        raise ValueError(f"Unknown provider: {provider}")

    def _stream(self, provider: str, prompt: str, max_tokens: int, system: str) -> Iterator[str]:
        if provider == "claude":
            yield from self._claude_stream(prompt, max_tokens, system)
        elif provider == "openai":
            yield from self._openai_stream(prompt, max_tokens, system)
        elif provider == "ollama":
            yield from self._ollama_stream(prompt, max_tokens, system)

    # Claude â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _claude_complete(self, prompt: str, max_tokens: int, system: str) -> str:
        import anthropic
        if "claude" not in self._clients:
            self._clients["claude"] = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY", "")
            )
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": PROVIDER_MODELS["claude"], "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = self._clients["claude"].messages.create(**kwargs)
        return response.content[0].text

    def _claude_stream(self, prompt: str, max_tokens: int, system: str) -> Iterator[str]:
        import anthropic
        if "claude" not in self._clients:
            self._clients["claude"] = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY", "")
            )
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": PROVIDER_MODELS["claude"], "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        with self._clients["claude"].messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    # OpenAI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _openai_complete(self, prompt: str, max_tokens: int, system: str) -> str:
        import openai
        if "openai" not in self._clients:
            self._clients["openai"] = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._clients["openai"].chat.completions.create(
            model=PROVIDER_MODELS["openai"],
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def _openai_stream(self, prompt: str, max_tokens: int, system: str) -> Iterator[str]:
        import openai
        if "openai" not in self._clients:
            self._clients["openai"] = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        stream = self._clients["openai"].chat.completions.create(
            model=PROVIDER_MODELS["openai"],
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # Ollama â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _ollama_complete(self, prompt: str, max_tokens: int, system: str) -> str:
        import requests
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        payload = {
            "model": PROVIDER_MODELS["ollama"],
            "prompt": f"{system}\n\n{prompt}" if system else prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        response = requests.post(f"{base_url}/api/generate", json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["response"]

    def _ollama_stream(self, prompt: str, max_tokens: int, system: str) -> Iterator[str]:
        import requests
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        payload = {
            "model": PROVIDER_MODELS["ollama"],
            "prompt": f"{system}\n\n{prompt}" if system else prompt,
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        with requests.post(f"{base_url}/api/generate", json=payload, stream=True, timeout=120) as r:
            r.raise_for_status()
            import json
            for line in r.iter_lines():
                if line:
                    data = json.loads(line)
                    if data.get("response"):
                        yield data["response"]

    # Cost tracking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _track_cost(self, provider: str, prompt: str, result: str, use_case: str) -> None:
        if self.memory is None:
            return
        try:
            tokens_in = len(prompt.split()) * 4 // 3  # rough estimate
            tokens_out = len(result.split()) * 4 // 3
            rates = COST_PER_1K.get(provider, {"in": 0.0, "out": 0.0})
            cost = (tokens_in * rates["in"] + tokens_out * rates["out"]) / 1000
            self.memory.record_llm_cost(
                provider=provider,
                model=PROVIDER_MODELS.get(provider, "unknown"),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                use_case=use_case,
            )
        except Exception as e:
            logger.debug("Cost tracking failed: %s", e)

