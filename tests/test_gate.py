import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_plugin_dir = Path(__file__).parent.parent.resolve()
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))

import httpx
from gate import GateDecision, JevGate, JevGateConfig


class TestJevGate(unittest.IsolatedAsyncioTestCase):
    """Test suite for JevGate decision evaluator."""

    async def test_gate_disabled_returns_true(self):
        config = JevGateConfig(enabled=False)
        gate = JevGate(config)
        decision = await gate.evaluate("Python tuple vs list")
        self.assertTrue(decision.should_retrieve)
        self.assertFalse(decision.fallback)
        self.assertEqual(decision.fallback_reason, "gate_disabled")

    async def test_missing_api_key_fails_open(self):
        with patch.dict(os.environ, {}, clear=True):
            config = JevGateConfig(enabled=True, api_key="")
            gate = JevGate(config)
            decision = await gate.evaluate("Some query")
            self.assertTrue(decision.should_retrieve)
            self.assertTrue(decision.fallback)
            self.assertEqual(decision.fallback_reason, "api_key_missing")

    async def test_official_jev_answers_noul_format(self):
        """Verify the exact response format returned by live TypeSafe AI Jev API."""
        config = JevGateConfig(
            enabled=True,
            api_key="test-key",
            threshold=0.50,
        )
        gate = JevGate(config)

        # 1. Chit-chat: "夕颜早～" -> noul: 0.09 (SKIP)
        chitchat_resp = {
            "answers": {
                "need_long_term_memory": {
                    "type": "noul",
                    "noul": 0.09,
                }
            }
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = httpx.Response(
                status_code=200,
                json=chitchat_resp,
                request=httpx.Request("POST", config.endpoint),
            )
            decision = await gate.evaluate("夕颜早～")
            self.assertFalse(decision.should_retrieve)
            self.assertAlmostEqual(decision.probability, 0.09, places=4)
            self.assertFalse(decision.fallback)

        # 2. Recall: "你还记得我之前说的那个项目吗？" -> noul: 0.94 (RETRIEVE)
        recall_resp = {
            "answers": {
                "need_long_term_memory": {
                    "type": "noul",
                    "noul": 0.94,
                }
            }
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = httpx.Response(
                status_code=200,
                json=recall_resp,
                request=httpx.Request("POST", config.endpoint),
            )
            decision = await gate.evaluate("你还记得我之前说的那个项目吗？")
            self.assertTrue(decision.should_retrieve)
            self.assertAlmostEqual(decision.probability, 0.94, places=4)
            self.assertFalse(decision.fallback)

    async def test_threshold_evaluation_skip_and_retrieve(self):
        config = JevGateConfig(
            enabled=True,
            api_key="test-key",
            threshold=0.50,
        )
        gate = JevGate(config)

        test_cases = [
            (0.10, False),  # P=0.10 -> SKIP
            (0.49, False),  # P=0.49 -> SKIP
            (0.50, True),   # P=0.50 -> RETRIEVE (inclusive)
            (0.90, True),   # P=0.90 -> RETRIEVE
        ]

        for probability, expected_decision in test_cases:
            mock_response = {
                "decisions": {
                    "need_long_term_memory": {
                        "probability": probability,
                        "confidence": 0.95,
                    }
                }
            }
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = httpx.Response(
                    status_code=200,
                    json=mock_response,
                    request=httpx.Request("POST", config.endpoint),
                )
                decision = await gate.evaluate("Sample query")
                self.assertEqual(
                    decision.should_retrieve,
                    expected_decision,
                    f"Failed for probability={probability}",
                )
                self.assertAlmostEqual(decision.probability, probability, places=4)
                self.assertFalse(decision.fallback)

    async def test_custom_threshold(self):
        config = JevGateConfig(
            enabled=True,
            api_key="test-key",
            threshold=0.70,
        )
        gate = JevGate(config)

        # 0.60 < 0.70 -> SKIP
        mock_resp_60 = {
            "decisions": {
                "need_long_term_memory": {"probability": 0.60, "confidence": 0.9}
            }
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = httpx.Response(
                status_code=200,
                json=mock_resp_60,
                request=httpx.Request("POST", config.endpoint),
            )
            decision = await gate.evaluate("Query")
            self.assertFalse(decision.should_retrieve)

        # 0.75 >= 0.70 -> RETRIEVE
        mock_resp_75 = {
            "decisions": {
                "need_long_term_memory": {"probability": 0.75, "confidence": 0.9}
            }
        }
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = httpx.Response(
                status_code=200,
                json=mock_resp_75,
                request=httpx.Request("POST", config.endpoint),
            )
            decision = await gate.evaluate("Query")
            self.assertTrue(decision.should_retrieve)

    async def test_fail_open_on_timeout(self):
        config = JevGateConfig(enabled=True, api_key="test-key", timeout_ms=300)
        gate = JevGate(config)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Read timed out")
            decision = await gate.evaluate("Query")
            self.assertTrue(decision.should_retrieve)
            self.assertTrue(decision.fallback)
            self.assertEqual(decision.fallback_reason, "timeout")

    async def test_fail_open_on_http_errors(self):
        config = JevGateConfig(enabled=True, api_key="test-key")
        gate = JevGate(config)

        for status_code in (401, 429, 500, 503):
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = httpx.Response(
                    status_code=status_code,
                    request=httpx.Request("POST", config.endpoint),
                )
                decision = await gate.evaluate("Query")
                self.assertTrue(decision.should_retrieve)
                self.assertTrue(decision.fallback)
                self.assertEqual(decision.fallback_reason, f"http_{status_code}")

    async def test_fail_open_on_invalid_schema(self):
        config = JevGateConfig(enabled=True, api_key="test-key")
        gate = JevGate(config)

        invalid_payloads = [
            {},
            {"error": "unexpected"},
            {"decisions": {"other_key": {"probability": 0.9}}},
            {"decisions": {"need_long_term_memory": "invalid_value"}},
        ]

        for bad_json in invalid_payloads:
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = httpx.Response(
                    status_code=200,
                    json=bad_json,
                    request=httpx.Request("POST", config.endpoint),
                )
                decision = await gate.evaluate("Query")
                self.assertTrue(decision.should_retrieve)
                self.assertTrue(decision.fallback)
                self.assertEqual(decision.fallback_reason, "invalid_response_schema")

    def test_env_var_api_key_resolution(self):
        with patch.dict(os.environ, {"JEV_API_KEY": "env-secret-123"}):
            config = JevGateConfig(enabled=True, api_key="")
            gate = JevGate(config)
            self.assertEqual(gate._resolve_api_key(), "env-secret-123")

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "typesafe-key-456"}):
            config = JevGateConfig(enabled=True, api_key="")
            gate = JevGate(config)
            self.assertEqual(gate._resolve_api_key(), "typesafe-key-456")


if __name__ == "__main__":
    unittest.main()
