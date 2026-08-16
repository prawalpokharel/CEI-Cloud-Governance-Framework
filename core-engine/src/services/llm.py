"""
LLM client.

Provider-agnostic by a thin interface, with Gemini implemented. The
abstraction costs almost nothing and buys the ability to compare providers on
the same prompts later without touching call sites -- worth having when the
output is code that gets proposed against a customer's repository.

## What the model is and is not asked to do

It is NOT asked to edit files. A dependency version bump is a mechanical
substitution: find the declaration, replace the version. Handing that to a
model introduces a failure mode that does not otherwise exist -- silently
reformatting the file, dropping a comment, "improving" an unrelated line --
in exchange for nothing, because the deterministic version is both simpler
and exact.

It IS asked for the things that need judgement and have no parser:

* which risks a specific upgrade carries
* what a reviewer should check before merging
* a readable explanation of why this change is being proposed

So the model writes prose about a diff the code already computed. If the
model is unavailable, the fix still works; the PR body is just terser.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60

# Provider-side 5xx and timeouts are transient by definition -- Gemini returns
# 503 "high demand" under load. Retrying a few times with jitter costs seconds
# and avoids degrading a pull request body over a blip. 4xx is never retried:
# the request is wrong, not unlucky.
MAX_ATTEMPTS = 3

# Models retired for new API keys. Configuring one produces a 404 that reads
# like a broken integration rather than a stale setting, so it is named.
RETIRED_GEMINI_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
}


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """
    Not configured, or the provider refused.

    Distinct from LLMError because callers treat it differently: an
    unavailable model degrades the PR body, while a genuine error should
    surface.
    """


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient:
    """
    Minimal completion client.

    Deliberately not a chat abstraction: every call here is one prompt in,
    one block of text out. A conversation interface would invite carrying
    state between fixes, and each fix should be judged on its own evidence.
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.provider = (provider or os.environ.get("LLM_PROVIDER", "")).lower().strip()
        self.model = model or os.environ.get("LLM_MODEL", "").strip()
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model and self._api_key)

    def describe(self) -> dict[str, Any]:
        """Configuration state, safe to log and to return over the API."""
        return {
            "provider": self.provider or None,
            "model": self.model or None,
            "configured": self.configured,
            # Never the key, not even a prefix: a prefix is enough to confirm
            # a guess about which project a leaked key belongs to.
            "api_key_present": bool(self._api_key),
        }

    def complete(
        self,
        prompt: str,
        *,
        max_output_tokens: int = 2048,
        temperature: float = 0.2,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> LLMResponse:
        if not self.configured:
            raise LLMUnavailable(
                "LLM is not configured. Set LLM_PROVIDER, LLM_MODEL, and "
                "LLM_API_KEY."
            )
        if self.provider == "gemini":
            return self._gemini(prompt, max_output_tokens, temperature, timeout)
        raise LLMUnavailable(f"Unsupported LLM provider {self.provider!r}")

    # -- Gemini ------------------------------------------------------------

    def _gemini(
        self, prompt: str, max_output_tokens: int, temperature: float, timeout: int
    ) -> LLMResponse:
        if self.model in RETIRED_GEMINI_MODELS:
            raise LLMUnavailable(
                f"{self.model} is retired and rejects new API keys. Use "
                "gemini-3.7-flash (pinned) or gemini-flash-latest (tracks the "
                "newest flash release)."
            )

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_output_tokens,
                "temperature": temperature,
            },
        }).encode()

        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # Header rather than a query parameter: a key in a URL lands
                # in proxy logs, browser history, and error reports.
                "x-goog-api-key": self._api_key,
            },
        )

        payload = None
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as exc:
                detail = self._error_detail(exc)
                if exc.code in (401, 403):
                    raise LLMUnavailable(f"Gemini rejected the API key: {detail}")
                if exc.code == 404:
                    raise LLMUnavailable(f"Model {self.model!r} unavailable: {detail}")
                if exc.code == 429:
                    raise LLMUnavailable(f"Gemini quota exhausted: {detail}")
                if exc.code < 500:
                    raise LLMError(f"Gemini returned HTTP {exc.code}: {detail}")
                last_error = LLMError(f"Gemini returned HTTP {exc.code}: {detail}")
                log.info(
                    "Gemini attempt %d/%d failed with %d; retrying",
                    attempt, MAX_ATTEMPTS, exc.code,
                )
            except Exception as exc:
                last_error = LLMError(f"Gemini request failed: {exc}")
                log.info(
                    "Gemini attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc
                )

            if attempt < MAX_ATTEMPTS:
                time.sleep(min(8.0, 2 ** attempt) * (0.5 + random.random()))

        if payload is None:
            raise last_error or LLMError("Gemini request failed")

        candidates = payload.get("candidates") or []
        if not candidates:
            # Safety filters return no candidate at all. Treated as
            # unavailable rather than as an error: the caller should carry on
            # without prose, not fail the fix.
            feedback = payload.get("promptFeedback") or {}
            raise LLMUnavailable(f"Gemini returned no candidates ({feedback})")

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()

        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS" and not text:
            raise LLMError(
                "Gemini hit the output limit before producing any text. "
                "Raise max_output_tokens."
            )

        usage = payload.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )

    @staticmethod
    def _error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read().decode()
            return json.loads(raw)["error"]["message"][:300]
        except Exception:
            return "<no detail>"


def load_env_file(path: str = ".env") -> None:
    """
    Load a .env file into the environment without overwriting what is set.

    Existing variables win, so a real deployment's injected configuration is
    never shadowed by a file someone left in the working directory.
    """
    import pathlib

    candidate = pathlib.Path(path)
    if not candidate.exists():
        # Also look one level up: commands run from core-engine/ while the
        # file lives at the repository root.
        candidate = pathlib.Path("..") / path
        if not candidate.exists():
            return

    # Record where the file was found. Relative paths inside it (a private
    # key, a certificate) must resolve against the file's own directory, not
    # against whatever directory the process happens to be started from --
    # the CLI runs from core-engine/ while .env lives at the repo root.
    os.environ.setdefault(
        "CLOUDOPTIMIZER_ENV_DIR", str(candidate.resolve().parent)
    )

    for line in candidate.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
