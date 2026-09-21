import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_plugin_dir = Path(__file__).parent.parent.resolve()
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from qwenpaw.memory import (
    AutoMemorySearchOptions,
    MemoryBackendContext,
    memory_registry,
)
from qwenpaw.plugins.api import PluginApi

from gate import GateDecision, JevGateConfig
from plugin import JevGatedReMeManager, JevMemoryGatePlugin


def _make_dummy_context(backend_config=None) -> MemoryBackendContext:
    return MemoryBackendContext(
        agent_id="default",
        working_dir=Path("/Users/yui/.qwenpaw/workspaces/default"),
        host_working_dir=Path("/Users/yui/.qwenpaw/workspaces/default"),
        backend_config=backend_config or {},
    )


class TestJevIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration test suite for JevGatedReMeManager."""

    async def test_search_for_auto_memory_skip(self):
        """When Jev decides SKIP, _search_for_auto_memory returns None without calling super()."""
        context = _make_dummy_context({"enabled": True, "api_key": "dummy"})
        manager = JevGatedReMeManager(context=context)

        # Mock gate to return SKIP
        manager._jev_gate.evaluate = AsyncMock(
            return_value=GateDecision(
                should_retrieve=False,
                probability=0.15,
                confidence=0.9,
                latency_ms=25.0,
                fallback=False,
            )
        )

        with patch.object(
            memory_registry.get("remelight"),
            "_search_for_auto_memory",
            new_callable=AsyncMock,
        ) as mock_super_search:
            result = await manager._search_for_auto_memory(
                query="What is a Python tuple?",
                options=AutoMemorySearchOptions(max_results=2),
            )

            self.assertIsNone(result)
            mock_super_search.assert_not_called()

    async def test_search_for_auto_memory_retrieve(self):
        """When Jev decides RETRIEVE, _search_for_auto_memory delegates to native ReMe."""
        context = _make_dummy_context({"enabled": True, "api_key": "dummy"})
        manager = JevGatedReMeManager(context=context)

        # Mock gate to return RETRIEVE
        manager._jev_gate.evaluate = AsyncMock(
            return_value=GateDecision(
                should_retrieve=True,
                probability=0.88,
                confidence=0.95,
                latency_ms=30.0,
                fallback=False,
            )
        )

        dummy_tool_chunk = MagicMock(name="DummyToolChunk")

        with patch.object(
            memory_registry.get("remelight"),
            "_search_for_auto_memory",
            new_callable=AsyncMock,
        ) as mock_super_search:
            mock_super_search.return_value = dummy_tool_chunk

            result = await manager._search_for_auto_memory(
                query="Do you remember the architecture we discussed?",
                options=AutoMemorySearchOptions(max_results=2),
            )

            self.assertIs(result, dummy_tool_chunk)
            mock_super_search.assert_called_once_with(
                query="Do you remember the architecture we discussed?",
                options=AutoMemorySearchOptions(max_results=2),
            )

    async def test_search_for_auto_memory_fail_open_on_error(self):
        """When Jev fails, manager fails open and calls native ReMe retrieval."""
        context = _make_dummy_context({"enabled": True, "api_key": "dummy"})
        manager = JevGatedReMeManager(context=context)

        manager._jev_gate.evaluate = AsyncMock(
            return_value=GateDecision(
                should_retrieve=True,
                fallback=True,
                fallback_reason="timeout",
            )
        )

        dummy_tool_chunk = MagicMock(name="FallbackChunk")

        with patch.object(
            memory_registry.get("remelight"),
            "_search_for_auto_memory",
            new_callable=AsyncMock,
        ) as mock_super_search:
            mock_super_search.return_value = dummy_tool_chunk

            result = await manager._search_for_auto_memory(
                query="Any query when Jev is down",
                options=AutoMemorySearchOptions(max_results=2),
            )

            self.assertIs(result, dummy_tool_chunk)
            mock_super_search.assert_called_once()

    async def test_search_for_auto_memory_gate_disabled(self):
        """When gate is disabled in config, Jev is bypassed completely."""
        context = _make_dummy_context({"enabled": False})
        manager = JevGatedReMeManager(context=context)

        manager._jev_gate.evaluate = AsyncMock()
        dummy_tool_chunk = MagicMock()

        with patch.object(
            memory_registry.get("remelight"),
            "_search_for_auto_memory",
            new_callable=AsyncMock,
        ) as mock_super_search:
            mock_super_search.return_value = dummy_tool_chunk

            result = await manager._search_for_auto_memory(
                query="Query",
                options=AutoMemorySearchOptions(max_results=2),
            )

            self.assertIs(result, dummy_tool_chunk)
            manager._jev_gate.evaluate.assert_not_called()
            mock_super_search.assert_called_once()

    async def test_regular_memory_search_tool_unaffected(self):
        """Regular memory_search tool calls bypass Jev Gate entirely."""
        context = _make_dummy_context({"enabled": True, "api_key": "dummy"})
        manager = JevGatedReMeManager(context=context)

        manager._jev_gate.evaluate = AsyncMock()
        dummy_tool_chunk = MagicMock(name="DirectSearchChunk")

        with patch.object(
            memory_registry.get("remelight"),
            "memory_search",
            new_callable=AsyncMock,
        ) as mock_super_tool:
            mock_super_tool.return_value = dummy_tool_chunk

            # Agent explicitly calls memory_search tool
            result = await manager.memory_search(query="Explicit tool call", max_results=5)

            self.assertIs(result, dummy_tool_chunk)
            mock_super_tool.assert_called_once_with(query="Explicit tool call", max_results=5)
            manager._jev_gate.evaluate.assert_not_called()

    def test_plugin_registration(self):
        """Plugin correctly registers 'jev-remelight' backend via PluginApi."""
        api = MagicMock(spec=PluginApi)
        plugin = JevMemoryGatePlugin()
        plugin.register(api)

        api.register_memory_backend.assert_called_once()
        call_kwargs = api.register_memory_backend.call_args.kwargs
        self.assertEqual(call_kwargs["backend_id"], "jev-remelight")
        self.assertIs(call_kwargs["factory"], JevGatedReMeManager)
        self.assertIs(call_kwargs["config_schema"], JevGateConfig)
        self.assertIn("api_key", call_kwargs["metadata"]["secret_fields"])


if __name__ == "__main__":
    unittest.main()
