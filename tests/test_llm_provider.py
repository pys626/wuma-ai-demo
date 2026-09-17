import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import llm_provider as provider


class FakeCompletions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content='{"status":"ok"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, calls):
        self.chat = SimpleNamespace(completions=FakeCompletions(calls))


class ProviderTests(unittest.TestCase):
    def test_hunyuan_is_the_default_provider(self):
        with patch.dict(os.environ, {}, clear=True):
            config = provider.load_config()

        self.assertEqual(config.provider, "hunyuan")
        self.assertEqual(config.provider_name, "腾讯混元")
        self.assertEqual(
            config.base_url,
            "https://api.hunyuan.cloud.tencent.com/v1",
        )
        self.assertEqual(config.model, "hunyuan-turbos-latest")
        self.assertFalse(config.json_mode)
        self.assertTrue(provider.is_tencent_provider())

    def test_hunyuan_compatibility_environment_is_supported(self):
        values = {
            "LLM_PROVIDER": "hunyuan",
            "HUNYUAN_API_KEY": "hunyuan-key",
            "HUNYUAN_BASE_URL": "https://hunyuan.example.com/v1",
            "HUNYUAN_MODEL": "hunyuan-test",
        }
        with patch.dict(os.environ, values, clear=True):
            config = provider.load_config()

        self.assertEqual(config.api_key, "hunyuan-key")
        self.assertEqual(config.base_url, "https://hunyuan.example.com/v1")
        self.assertEqual(config.model, "hunyuan-test")

    def test_legacy_deepseek_environment_is_supported(self):
        values = {
            "DEEPSEEK_API_KEY": "legacy-key",
            "DEEPSEEK_BASE_URL": "https://legacy.example.com",
            "DEEPSEEK_MODEL": "legacy-model",
        }
        with patch.dict(os.environ, values, clear=True):
            config = provider.load_config()

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.api_key, "legacy-key")
        self.assertEqual(config.base_url, "https://legacy.example.com")
        self.assertEqual(config.model, "legacy-model")

    def test_generic_environment_takes_priority(self):
        values = {
            "LLM_PROVIDER": "official",
            "LLM_PROVIDER_NAME": "官方模型",
            "LLM_API_KEY": "new-key",
            "LLM_BASE_URL": "https://official.example.com",
            "LLM_MODEL": "official-model",
            "DEEPSEEK_API_KEY": "legacy-key",
            "DEEPSEEK_MODEL": "legacy-model",
        }
        with patch.dict(os.environ, values, clear=True):
            config = provider.load_config()

        self.assertEqual(config.provider_name, "官方模型")
        self.assertEqual(config.api_key, "new-key")
        self.assertEqual(config.model, "official-model")

    def test_other_provider_does_not_reuse_legacy_deepseek_target(self):
        values = {
            "LLM_PROVIDER": "official",
            "DEEPSEEK_API_KEY": "legacy-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "legacy-model",
        }
        with patch.dict(os.environ, values, clear=True):
            config = provider.load_config()

        self.assertEqual(config.api_key, "")
        self.assertEqual(config.base_url, "")
        self.assertEqual(config.model, "")

    def test_deepseek_only_option_is_not_sent_to_other_provider(self):
        calls = []
        values = {
            "LLM_PROVIDER": "official",
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://official.example.com",
            "LLM_MODEL": "official-model",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch.object(provider, "OpenAI", return_value=FakeClient(calls)):
                content = provider.chat_text(
                    [{"role": "user", "content": "测试"}],
                    max_tokens=50,
                )

        self.assertEqual(content, '{"status":"ok"}')
        self.assertNotIn("extra_body", calls[0])
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})

    def test_hunyuan_request_uses_compatible_payload_without_vendor_options(self):
        calls = []
        values = {
            "LLM_PROVIDER": "hunyuan",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "hunyuan-test",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch.object(provider, "OpenAI", return_value=FakeClient(calls)):
                provider.chat_text(
                    [{"role": "user", "content": "测试"}],
                    max_tokens=50,
                )

        self.assertNotIn("extra_body", calls[0])
        self.assertNotIn("response_format", calls[0])

    def test_deepseek_request_keeps_thinking_disabled(self):
        calls = []
        values = {
            "LLM_PROVIDER": "deepseek",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "deepseek-test",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch.object(provider, "OpenAI", return_value=FakeClient(calls)):
                provider.chat_text(
                    [{"role": "user", "content": "测试"}],
                    max_tokens=50,
                )

        self.assertEqual(
            calls[0]["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_json_mode_can_be_disabled_by_configuration(self):
        calls = []
        values = {
            "LLM_PROVIDER": "official",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "official-model",
            "LLM_JSON_MODE": "false",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch.object(provider, "OpenAI", return_value=FakeClient(calls)):
                provider.chat_text(
                    [{"role": "user", "content": "测试"}],
                    max_tokens=50,
                )

        self.assertNotIn("response_format", calls[0])


if __name__ == "__main__":
    unittest.main()
