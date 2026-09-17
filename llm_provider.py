"""大模型连接适配层。

本文件只负责读取模型配置、发送请求和统一网络错误。
题目生成、回答评价与报告规则继续放在llm_service.py中。
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class LLMProviderError(Exception):
    """转换为可由上层直接展示的模型连接错误。"""


@dataclass(frozen=True)
class LLMConfig:
    """保存一次模型调用所需的配置，不保存或输出密钥内容。"""

    provider: str
    provider_name: str
    api_style: str
    api_key: str
    base_url: str
    model: str
    json_mode: bool


def _first_env(*names, default=""):
    """依次读取环境变量，返回第一个非空值。"""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _env_bool(name, default=True):
    """把.env中的true/false转换为Python布尔值。"""
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise LLMProviderError(f"{name}只能填写true或false。")


def load_config():
    """默认使用腾讯混元，并保留旧版DeepSeek环境变量兼容。"""
    explicit_provider = _first_env("LLM_PROVIDER").lower()
    if explicit_provider:
        provider = explicit_provider
    elif _first_env("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
        # 仅对旧.env进行隐式识别，新安装默认进入腾讯混元。
        provider = "deepseek"
    else:
        provider = "hunyuan"

    if provider in {"hunyuan", "tencent-hunyuan"}:
        default_name = "腾讯混元"
        default_base_url = "https://api.hunyuan.cloud.tencent.com/v1"
        default_model = "hunyuan-turbos-latest"
    elif provider == "deepseek":
        default_name = "DeepSeek"
        default_base_url = "https://api.deepseek.com"
        default_model = "deepseek-v4-flash"
    else:
        default_name = provider
        default_base_url = ""
        default_model = ""

    if provider in {"hunyuan", "tencent-hunyuan"}:
        api_key = _first_env("LLM_API_KEY", "HUNYUAN_API_KEY")
        base_url = _first_env(
            "LLM_BASE_URL",
            "HUNYUAN_BASE_URL",
            default=default_base_url,
        )
        model = _first_env(
            "LLM_MODEL",
            "HUNYUAN_MODEL",
            default=default_model,
        )
    elif provider == "deepseek":
        api_key = _first_env("LLM_API_KEY", "DEEPSEEK_API_KEY")
        base_url = _first_env(
            "LLM_BASE_URL",
            "DEEPSEEK_BASE_URL",
            default=default_base_url,
        )
        model = _first_env(
            "LLM_MODEL",
            "DEEPSEEK_MODEL",
            default=default_model,
        )
    else:
        # 切换服务后不复用旧DeepSeek地址和模型，避免密钥发往错误接口。
        api_key = _first_env("LLM_API_KEY")
        base_url = _first_env("LLM_BASE_URL")
        model = _first_env("LLM_MODEL")

    return LLMConfig(
        provider=provider,
        provider_name=_first_env("LLM_PROVIDER_NAME", default=default_name),
        api_style=_first_env(
            "LLM_API_STYLE",
            default="openai-compatible",
        ).lower().replace("_", "-"),
        api_key=api_key,
        base_url=base_url,
        model=model,
        json_mode=_env_bool(
            "LLM_JSON_MODE",
            default=provider not in {"hunyuan", "tencent-hunyuan"},
        ),
    )


def is_configured():
    """API密钥和模型名都存在时，才视为已经配置。"""
    config = load_config()
    return bool(config.api_key and config.model)


def provider_name():
    return load_config().provider_name


def model_name():
    return load_config().model


def is_tencent_provider():
    """赛事界面只把真实的腾讯混元配置标记为腾讯服务。"""
    return load_config().provider in {"hunyuan", "tencent-hunyuan"}


def _create_client(config):
    if not config.api_key:
        message = "尚未配置LLM_API_KEY，请检查项目根目录下的.env文件。"
        if config.provider in {"hunyuan", "tencent-hunyuan"}:
            message = "尚未配置腾讯混元密钥，请在.env中填写LLM_API_KEY。"
        elif config.provider == "deepseek":
            message = "尚未配置LLM_API_KEY；旧版DEEPSEEK_API_KEY也继续兼容。"
        raise LLMProviderError(message)
    if not config.model:
        raise LLMProviderError("尚未配置LLM_MODEL，请检查项目根目录下的.env文件。")
    if config.api_style != "openai-compatible":
        raise LLMProviderError(
            "当前版本只实现了openai-compatible接口；"
            "拿到官方SDK文档后，需要在llm_provider.py中增加对应适配器。"
        )

    options = {
        "api_key": config.api_key,
        "timeout": 75.0,
        "max_retries": 1,
    }
    if config.base_url:
        options["base_url"] = config.base_url
    return OpenAI(**options)


def chat_text(messages, max_tokens=1800):
    """通过OpenAI兼容接口发送消息，并返回模型的文本内容。"""
    config = load_config()
    client = _create_client(config)
    request = {
        "model": config.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    if config.json_mode:
        request["response_format"] = {"type": "json_object"}
    if config.provider == "deepseek":
        request["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        response = client.chat.completions.create(**request)
    except AuthenticationError as error:
        raise LLMProviderError("API密钥无效，请检查.env中的模型密钥配置。") from error
    except RateLimitError as error:
        raise LLMProviderError("API请求过于频繁，请稍等片刻后重试。") from error
    except APITimeoutError as error:
        raise LLMProviderError("大模型响应超时，请检查网络后重试。") from error
    except APIConnectionError as error:
        raise LLMProviderError("无法连接当前智能服务，请检查网络和接口地址。") from error
    except APIStatusError as error:
        if error.status_code == 402:
            message = "API账户余额不足，请充值后重试。"
        elif error.status_code == 400:
            message = "API请求格式错误，请检查模型名称和接口配置。"
        else:
            message = f"当前智能服务暂时不可用，状态码：{error.status_code}。"
        raise LLMProviderError(message) from error
    except Exception as error:
        raise LLMProviderError("调用大模型时出现未知错误，请稍后重试。") from error

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise LLMProviderError("大模型返回了空内容，请重新尝试。")
    return content
