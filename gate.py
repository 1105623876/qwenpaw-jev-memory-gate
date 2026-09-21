# -*- coding: utf-8 -*-
"""TypeSafe AI Jev memory gate client and evaluator."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("qwenpaw.plugins.jev_memory_gate")

QUESTION_KEY = "need_long_term_memory"
QUESTION_INSTRUCTIONS = (
    "Determine whether answering this user query requires recalling personal preferences, "
    "past user facts, project history, or prior decisions stored in long-term semantic memory. "
    "Return false for general knowledge questions, chit-chat, self-contained logic, "
    "or requests that do not require past personal context."
)


class JevGateConfig(BaseModel):
    """Configuration for TypeSafe AI Jev memory gate."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(
        default=True,
        description="Whether to enable Jev decision gate for automatic memory retrieval",
    )
    api_key: str = Field(
        default="",
        description="TypeSafe AI API key (defaults to JEV_API_KEY environment variable)",
    )
    endpoint: str = Field(
        default="https://api.typesafe.ai/v1/systemone",
        description="TypeSafe AI Jev API endpoint",
    )
    model: str = Field(
        default="jev-latest",
        description="Jev model identifier",
    )
    threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Probability threshold (P >= threshold triggers retrieval)",
    )
    timeout_ms: int = Field(
        default=2000,
        ge=50,
        le=10000,
        description="Timeout in milliseconds for Jev API calls",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="HTTP/HTTPS/SOCKS5 proxy URL (e.g. http://127.0.0.1:7897)",
    )
    debug_log_query: bool = Field(
        default=False,
        description="Whether to include truncated query in debug logs (for troubleshooting)",
    )


@dataclass(frozen=True)
class GateDecision:
    """Decision produced by Jev memory gate."""

    should_retrieve: bool
    probability: Optional[float] = None
    confidence: Optional[float] = None
    latency_ms: float = 0.0
    fallback: bool = False
    fallback_reason: Optional[str] = None


class JevGate:
    """Evaluates whether an incoming query warrants long-term memory retrieval."""

    def __init__(self, config: Optional[JevGateConfig] = None) -> None:
        self.config = config or JevGateConfig()
        self._client: Optional[httpx.AsyncClient] = None

    def _resolve_api_key(self) -> str:
        """Resolve API key from config or environment variables."""
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        env_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        return (env_key or "").strip()

    def _resolve_proxy(self) -> Optional[str]:
        """Resolve proxy URL from config, environment, or default local proxy."""
        if self.config.proxy and self.config.proxy.strip():
            return self.config.proxy.strip()
        return (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or "http://127.0.0.1:7897"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize a reusable persistent HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            proxy = self._resolve_proxy()
            timeout_sec = max(0.1, self.config.timeout_ms / 1000.0)
            self._client = httpx.AsyncClient(
                proxy=proxy,
                timeout=timeout_sec,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    async def aclose(self) -> None:
        """Close the persistent HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    close = aclose

    async def evaluate(self, query: str) -> GateDecision:
        """Evaluate a query with fail-open semantics."""
        start_time = time.perf_counter()
        query_len = len(query) if query else 0

        if not self.config.enabled:
            return GateDecision(
                should_retrieve=True,
                latency_ms=0.0,
                fallback=False,
                fallback_reason="gate_disabled",
            )

        api_key = self._resolve_api_key()
        if not api_key:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            self._log_decision(
                decision=True,
                prob=None,
                latency_ms=latency_ms,
                query_len=query_len,
                fallback=True,
                reason="api_key_missing",
            )
            return GateDecision(
                should_retrieve=True,
                latency_ms=latency_ms,
                fallback=True,
                fallback_reason="api_key_missing",
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "QwenPaw-JevMemoryGate/0.1.0",
        }
        payload = {
            "model": self.config.model,
            "state": query,
            "questions": {
                QUESTION_KEY: {
                    "type": "noul",
                    "instructions": QUESTION_INSTRUCTIONS,
                }
            },
        }

        timeout_sec = max(0.05, self.config.timeout_ms / 1000.0)

        try:
            client = await self._get_client()
            response = await client.post(
                self.config.endpoint,
                json=payload,
                headers=headers,
                timeout=timeout_sec,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000.0

            if response.status_code != 200:
                self._log_decision(
                    decision=True,
                    prob=None,
                    latency_ms=latency_ms,
                    query_len=query_len,
                    fallback=True,
                    reason=f"http_{response.status_code}",
                )
                return GateDecision(
                    should_retrieve=True,
                    latency_ms=latency_ms,
                    fallback=True,
                    fallback_reason=f"http_{response.status_code}",
                )

            data = response.json()
            prob, conf = self._extract_noul_result(data, QUESTION_KEY)

            if prob is None:
                self._log_decision(
                    decision=True,
                    prob=None,
                    latency_ms=latency_ms,
                    query_len=query_len,
                    fallback=True,
                    reason="invalid_response_schema",
                )
                return GateDecision(
                    should_retrieve=True,
                    latency_ms=latency_ms,
                    fallback=True,
                    fallback_reason="invalid_response_schema",
                )

            should_retrieve = prob >= self.config.threshold
            self._log_decision(
                decision=should_retrieve,
                prob=prob,
                latency_ms=latency_ms,
                query_len=query_len,
                fallback=False,
                reason=None,
            )
            return GateDecision(
                should_retrieve=should_retrieve,
                probability=prob,
                confidence=conf,
                latency_ms=latency_ms,
                fallback=False,
            )

        except httpx.TimeoutException:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            self._log_decision(
                decision=True,
                prob=None,
                latency_ms=latency_ms,
                query_len=query_len,
                fallback=True,
                reason="timeout",
            )
            return GateDecision(
                should_retrieve=True,
                latency_ms=latency_ms,
                fallback=True,
                fallback_reason="timeout",
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            reason = f"exception_{type(exc).__name__}"
            self._log_decision(
                decision=True,
                prob=None,
                latency_ms=latency_ms,
                query_len=query_len,
                fallback=True,
                reason=reason,
            )
            return GateDecision(
                should_retrieve=True,
                latency_ms=latency_ms,
                fallback=True,
                fallback_reason=reason,
            )

    @staticmethod
    def _extract_noul_result(
        data: Any,
        question_key: str,
    ) -> tuple[Optional[float], Optional[float]]:
        """Safely extract probability and confidence from Jev SystemOne response.

        Official Jev response shape:
            {"answers": {"<key>": {"type": "noul", "noul": 0.09}}}
        Also supports legacy/alternative shapes:
            {"decisions": {"<key>": {"probability": 0.8, "confidence": 0.9}}}
        """
        if not isinstance(data, dict):
            return None, None

        # 1. Locate question result container
        target = None
        for container_key in ("answers", "decisions", "results"):
            container = data.get(container_key)
            if isinstance(container, dict) and question_key in container:
                target = container[question_key]
                break

        if target is None and question_key in data:
            target = data[question_key]

        if not isinstance(target, dict):
            return None, None

        # 2. Extract probability value:
        # Official Jev noul field is "noul", e.g. {"type": "noul", "noul": 0.09}
        prob_val = None
        for field in ("noul", "probability", "prob", "score", "value"):
            if field in target and target[field] is not None:
                prob_val = target[field]
                break

        # Extract optional confidence
        conf_val = target.get("confidence")

        # Convert to float
        prob: Optional[float] = None
        if isinstance(prob_val, (int, float)):
            prob = float(prob_val)
        elif isinstance(prob_val, bool):
            prob = 1.0 if prob_val else 0.0

        conf: Optional[float] = (
            float(conf_val) if isinstance(conf_val, (int, float)) else None
        )
        return prob, conf

    def _log_decision(
        self,
        *,
        decision: bool,
        prob: Optional[float],
        latency_ms: float,
        query_len: int,
        fallback: bool,
        reason: Optional[str],
    ) -> None:
        """Emit privacy-safe structured log entry."""
        prob_str = f"{prob:.4f}" if prob is not None else "none"
        dec_str = "RETRIEVE" if decision else "SKIP"
        logger.info(
            "event=jev_memory_gate gate_enabled=%s decision=%s probability=%s "
            "threshold=%.2f jev_latency_ms=%.1f query_length=%d fallback_used=%s fallback_reason=%s",
            self.config.enabled,
            dec_str,
            prob_str,
            self.config.threshold,
            latency_ms,
            query_len,
            fallback,
            reason or "none",
        )
