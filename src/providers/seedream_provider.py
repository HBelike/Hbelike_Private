from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig


class SeedreamApiError(RuntimeError):
    """Seedream 图片接口调用失败。"""


@dataclass(frozen=True)
class SeedreamImageResult:
    """Seedream 成功生成并落盘后的图片结果。"""

    output_path: Path
    source_url: str | None
    raw_response: dict[str, Any]


class SeedreamProvider:
    """封装 Seedream 文生图真实 API 调用。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_api_key(self) -> bool:
        """检查图片服务密钥是否已经配置。"""
        return bool(os.getenv(self.config.image_api_key_env, "").strip())

    def generate_image(self, prompt: str, output_path: Path) -> SeedreamImageResult:
        """调用 Seedream 生成图片并保存到本地。"""
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Seedream prompt 不能为空")

        api_key = os.getenv(self.config.image_api_key_env, "").strip()
        if not api_key:
            raise SeedreamApiError(f"{self.config.image_api_key_env} 未配置，无法调用 Seedream")

        endpoint = self._build_endpoint()
        payload = {
            "model": self.config.image_model,
            "prompt": normalized_prompt,
            "size": self.config.image_size,
            "n": self.config.image_n,
            "response_format": self.config.image_response_format,
            "watermark": self.config.image_watermark,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.image_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SeedreamApiError(f"Seedream 请求失败：{exc}") from exc

        if response.status_code >= 400:
            raise SeedreamApiError(f"Seedream 返回错误状态码 {response.status_code}：{response.text[:500]}")

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise SeedreamApiError("Seedream 返回内容不是合法 JSON") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_url = self._save_first_image(response_payload, output_path)
        return SeedreamImageResult(
            output_path=output_path,
            source_url=source_url,
            raw_response=response_payload,
        )

    def _build_endpoint(self) -> str:
        """拼接图片生成接口地址。"""
        return f"{self.config.image_base_url.rstrip('/')}/{self.config.image_generations_endpoint.lstrip('/')}"

    def _save_first_image(self, response_payload: dict[str, Any], output_path: Path) -> str | None:
        """从接口响应中取第一张图片，支持 url 和 b64_json 两种返回方式。"""
        data = response_payload.get("data")
        if not isinstance(data, list) or not data:
            raise SeedreamApiError("Seedream 响应缺少 data[0]")

        first_item = data[0]
        if not isinstance(first_item, dict):
            raise SeedreamApiError("Seedream 响应 data[0] 不是对象")

        image_url = first_item.get("url")
        if isinstance(image_url, str) and image_url.strip():
            self._download_image(image_url.strip(), output_path)
            return image_url.strip()

        b64_json = first_item.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            output_path.write_bytes(base64.b64decode(b64_json))
            return None

        raise SeedreamApiError("Seedream 响应既没有 url，也没有 b64_json")

    def _download_image(self, image_url: str, output_path: Path) -> None:
        """下载 Seedream 返回的图片 URL。"""
        try:
            response = requests.get(image_url, timeout=self.config.image_timeout_seconds)
        except requests.RequestException as exc:
            raise SeedreamApiError(f"Seedream 图片下载失败：{exc}") from exc

        if response.status_code >= 400:
            raise SeedreamApiError(f"Seedream 图片下载返回错误状态码 {response.status_code}")

        output_path.write_bytes(response.content)
