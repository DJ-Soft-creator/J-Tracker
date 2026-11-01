"""Lokale Anbieterwahl der Journal-Analyse (D-015-Parität)."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import journal_analysis_providers as providers  # noqa: E402


class LocalHostTests(unittest.TestCase):
    def test_loopback_and_private_ranges_are_local(self):
        for host in ("127.0.0.1", "10.1.2.3", "192.168.1.50", "172.16.0.9", "::1"):
            self.assertTrue(providers.is_local_host(host), host)

    def test_public_addresses_are_not_local(self):
        for host in ("8.8.8.8", "1.1.1.1", "9.9.9.9"):
            self.assertFalse(providers.is_local_host(host), host)

    def test_unresolvable_host_is_not_local(self):
        self.assertFalse(providers.is_local_host("kein-solcher-host.invalid"))


class EndpointTests(unittest.TestCase):
    def test_local_endpoints_are_accepted(self):
        self.assertTrue(providers.is_local_endpoint("http://127.0.0.1:1234/v1"))
        self.assertTrue(providers.is_local_endpoint("http://192.168.1.50:8080"))

    def test_public_or_placeholder_endpoints_are_rejected(self):
        self.assertFalse(providers.is_local_endpoint("http://8.8.8.8/v1"))
        self.assertFalse(providers.is_local_endpoint("http://{locale_LLM_IP}/v1"))
        self.assertFalse(providers.is_local_endpoint("ftp://127.0.0.1/x"))
        self.assertFalse(providers.is_local_endpoint(""))


class ResolveTests(unittest.TestCase):
    def _config(self, **provider):
        base = {"id": "lm_lokal", "api_url": "http://192.168.1.50:1234/v1", "model": "m"}
        base.update(provider)
        return {"ai_providers": [base]}

    def test_local_lm_provider_is_returned(self):
        provider = providers.resolve_local_provider(self._config(), "lm_lokal")
        self.assertEqual(provider["id"], "lm_lokal")

    def test_non_lm_id_is_rejected_even_when_local(self):
        self.assertIsNone(providers.resolve_local_provider(self._config(id="cloud_lokal"), "cloud_lokal"))

    def test_local_id_with_public_endpoint_is_rejected(self):
        config = self._config(api_url="http://8.8.8.8/v1")
        self.assertIsNone(providers.resolve_local_provider(config, "lm_lokal"))

    def test_unknown_provider_is_rejected(self):
        self.assertIsNone(providers.resolve_local_provider(self._config(), "gibt-es-nicht"))


class AnalysisProviderTests(unittest.TestCase):
    CONFIG = {"ai_providers": [
        {"id": "lm_local", "api_url": "http://127.0.0.1:1234/v1/chat/completions"},
        {"id": "opus", "api_url": "https://openrouter.ai/api/v1/chat/completions", "journal_analysis": True},
        {"id": "cloud_unapproved", "api_url": "https://openrouter.ai/api/v1/chat/completions"},
        {"id": "plain_http", "api_url": "http://example.org/v1/chat/completions", "journal_analysis": True},
        {"id": "lm_public", "api_url": "https://openrouter.ai/api/v1/chat/completions"},
    ]}

    def test_local_providers_stay_allowed(self):
        self.assertEqual(providers.resolve_analysis_provider(self.CONFIG, "lm_local")["id"], "lm_local")
        self.assertFalse(providers.is_cloud_provider(self.CONFIG["ai_providers"][0]))

    def test_only_explicitly_approved_https_cloud_providers_are_allowed(self):
        self.assertEqual(providers.resolve_analysis_provider(self.CONFIG, "opus")["id"], "opus")
        self.assertTrue(providers.is_cloud_provider(self.CONFIG["ai_providers"][1]))
        for provider_id in ("cloud_unapproved", "plain_http", "lm_public", "unbekannt"):
            self.assertIsNone(providers.resolve_analysis_provider(self.CONFIG, provider_id), provider_id)


class ModelTests(unittest.TestCase):
    def test_base_url_strips_the_chat_completions_suffix(self):
        self.assertEqual(providers.provider_base_url("http://h:1234/v1/chat/completions"),
                         "http://h:1234/v1")
        self.assertEqual(providers.provider_base_url("http://h:1234/v1"), "http://h:1234/v1")

    def test_configured_model_wins(self):
        model = providers.resolve_effective_model({"model": "fest"}, discover=lambda p: ["live"])
        self.assertEqual(model, "fest")

    def test_missing_model_is_read_live_like_the_chat(self):
        seen = []
        model = providers.resolve_effective_model(
            {"model": ""}, discover=lambda provider: seen.append(provider) or ["erstes", "zweites"])
        self.assertEqual(model, "erstes")
        self.assertEqual(len(seen), 1)

    def test_no_discovered_model_yields_empty(self):
        self.assertEqual(
            providers.resolve_effective_model({"model": ""}, discover=lambda p: []), "")


if __name__ == "__main__":
    unittest.main()