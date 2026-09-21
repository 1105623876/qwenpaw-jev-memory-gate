# -*- coding: utf-8 -*-
"""QwenPaw Jev Memory Gate Plugin entry point and backend definition."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from qwenpaw.memory import (
    AutoMemorySearchOptions,
    BaseMemoryManager,
    MemoryBackendContext,
    memory_registry,
)
from qwenpaw.plugins.api import PluginApi

try:
    from .gate import JevGate, JevGateConfig
except (ImportError, ValueError):
    from gate import JevGate, JevGateConfig

logger = logging.getLogger("qwenpaw.plugins.jev_memory_gate")

# Verify core compatibility via public contract
_ReMeBase = memory_registry.get("remelight")
if _ReMeBase is None:
    raise RuntimeError(
        "Jev Memory Gate is incompatible with this QwenPaw installation: "
        "the 'remelight' memory backend was not found in memory_registry."
    )

if not hasattr(_ReMeBase, "_search_for_auto_memory") or not asyncio.iscoroutinefunction(
    getattr(_ReMeBase, "_search_for_auto_memory")
):
    raise RuntimeError(
        "Jev Memory Gate is incompatible with this QwenPaw version: "
        "required ReMe '_search_for_auto_memory' hook is missing or not an async coroutine."
    )


class JevGatedReMeManager(_ReMeBase):
    """ReMe Light memory backend gated by TypeSafe AI Jev decision model."""

    def __init__(
        self,
        working_dir: str | None = None,
        agent_id: str | None = None,
        *,
        context: MemoryBackendContext | None = None,
    ) -> None:
        super().__init__(
            working_dir=working_dir,
            agent_id=agent_id,
            context=context,
        )
        raw_config = context.backend_config if context else {}
        self._gate_config = JevGateConfig.model_validate(raw_config or {})
        self._jev_gate = JevGate(self._gate_config)
        logger.info(
            "JevGatedReMeManager initialized: agent_id=%s, gate_enabled=%s, threshold=%.2f",
            self.agent_id,
            self._gate_config.enabled,
            self._gate_config.threshold,
        )

    @property
    def jev_gate(self) -> JevGate:
        """Expose JevGate instance for testing and inspection."""
        return self._jev_gate

    async def close(self) -> bool:
        """Close gate HTTP client and underlying ReMe instance."""
        await self._jev_gate.close()
        if hasattr(super(), "close"):
            return await super().close()
        return True

    async def _search_for_auto_memory(
        self,
        *,
        query: str,
        options: AutoMemorySearchOptions,
    ) -> Any:
        """Run Jev evaluation before executing automatic memory recall.

        Contract:
        - If Jev decides SKIP (P < threshold), returns None.
          BaseMemoryManager.auto_memory_search then returns None,
          and MemoryMiddleware injects 0 synthetic messages.
        - If Jev decides RETRIEVE (P >= threshold, or fail-open),
          delegates directly to native ReMe search.
        - Regular Agent tool calls to memory_search() do not pass
          through this hook and remain 100% unaffected.
        """
        if not self._gate_config.enabled:
            return await super()._search_for_auto_memory(query=query, options=options)

        decision = await self._jev_gate.evaluate(query)
        if not decision.should_retrieve:
            logger.info(
                "JevGate decided SKIP for auto-memory recall: score=%s query_length=%d",
                f"{decision.probability:.4f}" if decision.probability is not None else "none",
                len(query) if query else 0,
            )
            return None

        logger.info(
            "JevGate decided RETRIEVE for auto-memory recall: score=%s query_length=%d fallback=%s reason=%s",
            f"{decision.probability:.4f}" if decision.probability is not None else "none",
            len(query) if query else 0,
            decision.fallback,
            decision.fallback_reason or "none",
        )
        return await super()._search_for_auto_memory(query=query, options=options)


class JevMemoryGatePlugin:
    """Plugin lifecycle manager for Jev Memory Gate."""

    def register(self, api: PluginApi) -> None:
        """Register the jev-remelight backend with QwenPaw."""
        api.register_memory_backend(
            backend_id="jev-remelight",
            factory=JevGatedReMeManager,
            label="ReMe Light (Jev Gated)",
            config_schema=JevGateConfig,
            metadata={
                "description": "ReMe Light semantic memory with TypeSafe AI Jev automatic recall gate.",
                "secret_fields": ["api_key"],
                "network_access": True,
            },
        )
        logger.info("Jev Memory Gate plugin registered backend: 'jev-remelight'")


plugin = JevMemoryGatePlugin()
