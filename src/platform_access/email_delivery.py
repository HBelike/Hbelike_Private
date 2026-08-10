"""平台账号验证码的邮件投递适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

import requests


class EmailDeliveryError(RuntimeError):
    """邮件服务不可用时给 Web 层的可读错误。"""


@dataclass(frozen=True)
class ResendEmailSettings:
    """Resend 所需的服务端配置，不向浏览器暴露 API Key。"""

    api_key: str
    from_address: str
    timeout_seconds: int = 12


class ResendEmailDelivery:
    """通过 Resend REST API 投递事务邮件。"""

    endpoint = "https://api.resend.com/emails"

    def __init__(self, settings: ResendEmailSettings) -> None:
        if not settings.api_key.strip():
            raise ValueError("尚未配置 RESEND_API_KEY")
        if not settings.from_address.strip():
            raise ValueError("尚未配置 RESEND_FROM_ADDRESS")
        self._settings = settings

    def send_verification_code(self, *, recipient: str, code: str, purpose: str) -> None:
        """发送十分钟有效的一次性验证码，失败时不隐藏服务端根因。"""

        subject = "职业智能工作台验证码"
        purpose_label = {
            "register": "完成注册",
            "bootstrap": "初始化管理员账号",
            "bind_email": "绑定登录邮箱",
            "reset_password": "重置密码",
        }.get(purpose, "完成身份验证")
        safe_code = escape(code)
        payload = {
            "from": self._settings.from_address,
            "to": [recipient],
            "subject": subject,
            "text": f"你的职业智能工作台验证码是 {code}，用于{purpose_label}，10 分钟内有效。若非本人操作，请忽略此邮件。",
            "html": (
                "<div style=\"font-family:Arial,'Microsoft YaHei',sans-serif;color:#20301d\">"
                "<h2>职业智能工作台</h2>"
                f"<p>请使用以下验证码{escape(purpose_label)}：</p>"
                f"<p style=\"font-size:28px;font-weight:700;letter-spacing:6px\">{safe_code}</p>"
                "<p>验证码 10 分钟内有效。若非本人操作，请忽略此邮件。</p>"
                "</div>"
            ),
        }
        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "career-orbit-platform/1.0",
                },
                json=payload,
                timeout=self._settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise EmailDeliveryError("验证码邮件发送失败：无法连接 Resend，请检查网络后重试") from exc

        if response.ok:
            return

        try:
            detail = response.json().get("message") or response.json().get("name")
        except ValueError:
            detail = response.text.strip()
        if response.status_code in {401, 403}:
            raise EmailDeliveryError("验证码邮件发送失败：Resend API Key 无效，或发件域名尚未验证")
        if response.status_code == 429:
            raise EmailDeliveryError("验证码邮件发送过于频繁，请稍后再试")
        raise EmailDeliveryError(f"验证码邮件发送失败：Resend 返回 {response.status_code}{f'（{detail}）' if detail else ''}")
