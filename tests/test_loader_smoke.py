# -*- coding: utf-8 -*-
"""Smoke tests for QwenPaw PluginLoader integration."""

import sys
import unittest
from pathlib import Path

_plugin_dir = Path(__file__).parent.parent.resolve()
_plugins_root = _plugin_dir.parent.resolve()
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

from qwenpaw.memory import memory_registry
from qwenpaw.plugins.loader import PluginLoader


class TestPluginLoaderSmoke(unittest.IsolatedAsyncioTestCase):
    """Verify that QwenPaw native PluginLoader discovers and registers jev-memory-gate."""

    async def test_native_plugin_loader_discovery_and_registration(self):
        loader = PluginLoader(plugin_dirs=[_plugins_root])
        discovered = loader.discover_plugins()

        # 1. Verify discovery
        found = False
        target_manifest = None
        target_path = None
        for manifest, path in discovered:
            if manifest.id == "jev-memory-gate":
                found = True
                target_manifest = manifest
                target_path = path
                break

        self.assertTrue(found, "PluginLoader failed to discover jev-memory-gate")
        self.assertEqual(target_manifest.name, "Jev Memory Gate")

        # 2. Verify loading
        record = await loader.load_plugin(target_manifest, target_path)
        self.assertIsNotNone(record)
        self.assertTrue(record.enabled)

        # 3. Verify backend registration
        self.assertIn("jev-remelight", memory_registry.list_registered())
        backend_cls = memory_registry.get("jev-remelight")
        self.assertIsNotNone(backend_cls)
        self.assertEqual(backend_cls.__name__, "JevGatedReMeManager")


if __name__ == "__main__":
    unittest.main()
