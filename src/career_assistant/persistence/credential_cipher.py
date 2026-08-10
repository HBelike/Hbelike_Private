"""求职助手模型连接凭据的应用层加密边界。

本模块只处理 API Key 在进入 PostgreSQL 前的加密和读取时的解密：

* 新写入统一使用 ``cryptography.fernet.Fernet``；
* 主密钥只从服务端环境变量 ``CAREER_CREDENTIAL_MASTER_KEY`` 读取；
* 旧版 ``plaintext_api_key`` 仅能在显式开启兼容开关时读取；
* 模块绝不记录、序列化或返回任何凭据原文。

Fernet 已同时提供保密性和完整性校验。它是一个经过长期维护的 pyca/cryptography
高层接口，避免业务仓储自行组合 AES、随机数和 MAC 而引入实现风险。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken


MASTER_KEY_ENV_NAME = "CAREER_CREDENTIAL_MASTER_KEY"
LEGACY_PLAINTEXT_FLAG_ENV_NAME = "CAREER_ALLOW_LEGACY_PLAINTEXT_CREDENTIALS"
FERNET_V1_SCHEME = "fernet_v1"
LEGACY_PLAINTEXT_SCHEME = "legacy_plaintext"
LEGACY_UNKNOWN_SCHEME = "legacy_unknown"


class CredentialCipherError(ValueError):
    """凭据无法安全加密或解密时抛出的可展示错误。

    该异常不包含 Key、密文或主密钥，Web API 可以安全地转换为用户可理解的 422
    响应；调用方不应将原始异常内容记入日志。
    """


@dataclass(frozen=True)
class CredentialCipher:
    """封装 Fernet 主密钥与旧明文兼容策略。

    ``_fernet`` 只保留在进程内存中，数据表仅存 Fernet token 的字节形式。该对象
    无可变共享状态，因此可被 FastAPI 多请求线程安全复用；每一次数据库写入仍由
    仓储自身事务负责。
    """

    _fernet: Fernet | None = field(repr=False)
    _allow_legacy_plaintext: bool = False
    _configuration_error: CredentialCipherError | None = field(default=None, repr=False)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "CredentialCipher":
        """从服务端环境构造加密器，不读取前端提交内容。

        未配置主密钥时允许应用继续读取环境变量 Provider Key，但拒绝把新 API Key
        写入数据库。旧明文的读取必须额外显式开启兼容开关，避免生产环境无意间继续
        依赖明文数据。
        """

        source = os.environ if environment is None else environment
        master_key = str(source.get(MASTER_KEY_ENV_NAME, "")).strip()
        allow_legacy_plaintext = _parse_bool(
            source.get(LEGACY_PLAINTEXT_FLAG_ENV_NAME, "false"),
        )
        if not master_key:
            return cls(None, allow_legacy_plaintext)
        try:
            return cls.from_master_key(
                master_key,
                allow_legacy_plaintext=allow_legacy_plaintext,
            )
        except CredentialCipherError as exc:
            # 不能因错误的运行时 Secret 让整个 API 在启动阶段崩溃；保存或读取该
            # 凭据时会返回同一条不含敏感信息的可操作提示。
            return cls(None, allow_legacy_plaintext, exc)

    @classmethod
    def from_master_key(
        cls,
        master_key: str | bytes,
        *,
        allow_legacy_plaintext: bool = False,
    ) -> "CredentialCipher":
        """用 Fernet 格式主密钥创建加密器，供应用和验证脚本显式注入。"""

        try:
            normalized_key = (
                master_key.strip().encode("ascii")
                if isinstance(master_key, str)
                else bytes(master_key).strip()
            )
            return cls(Fernet(normalized_key), allow_legacy_plaintext)
        except (TypeError, ValueError) as exc:
            raise CredentialCipherError(
                f"{MASTER_KEY_ENV_NAME} 格式无效；请使用 Fernet.generate_key() 生成的 URL-safe Base64 密钥",
            ) from exc

    @property
    def can_encrypt(self) -> bool:
        """标识当前进程是否具备安全保存新 API Key 的条件。"""

        return self._fernet is not None

    def require_encryption_ready(self) -> None:
        """在批量迁移等无明文输入的操作前校验主密钥是否可用。"""

        if self._fernet is not None:
            return
        if self._configuration_error is not None:
            raise self._configuration_error
        raise CredentialCipherError(
            f"无法安全保存模型连接：请在服务端配置 {MASTER_KEY_ENV_NAME}",
        )

    def encrypt(self, plaintext: str) -> bytes:
        """将非空 API Key 转为 Fernet token，不允许降级为明文。"""

        normalized_plaintext = plaintext.strip()
        if not normalized_plaintext:
            raise CredentialCipherError("API Key 不能为空")
        self.require_encryption_ready()
        assert self._fernet is not None
        return self._fernet.encrypt(normalized_plaintext.encode("utf-8"))

    def decrypt(
        self,
        *,
        encryption_scheme: str | None,
        encrypted_api_key: bytes | bytearray | memoryview | None,
        plaintext_api_key: str | None,
    ) -> str | None:
        """按持久化格式读取凭据，永远不会把不可识别密文当作 API Key 使用。"""

        scheme = (encryption_scheme or LEGACY_PLAINTEXT_SCHEME).strip().lower()
        if scheme == FERNET_V1_SCHEME:
            if self._fernet is None:
                if self._configuration_error is not None:
                    raise self._configuration_error
                raise CredentialCipherError(
                    f"无法读取已加密模型连接：请在服务端配置原始 {MASTER_KEY_ENV_NAME}",
                )
            if encrypted_api_key is None:
                raise CredentialCipherError("已加密模型连接缺少凭据数据，请重新填写并保存 API Key")
            try:
                return self._fernet.decrypt(bytes(encrypted_api_key)).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError) as exc:
                raise CredentialCipherError(
                    "已加密模型连接无法解密；请确认主密钥未更换，或重新填写并保存 API Key",
                ) from exc

        if scheme == LEGACY_PLAINTEXT_SCHEME:
            if not self._allow_legacy_plaintext:
                raise CredentialCipherError(
                    "检测到旧版明文 API Key；请执行凭据迁移后重新启动，或仅在本地临时开启旧明文兼容开关",
                )
            value = (plaintext_api_key or "").strip()
            return value or None

        if scheme == LEGACY_UNKNOWN_SCHEME:
            raise CredentialCipherError(
                "检测到无法识别的旧版加密凭据；为避免误用，请重新填写并保存 API Key",
            )

        raise CredentialCipherError("模型连接凭据格式不受支持，请重新填写并保存 API Key")


def _parse_bool(value: object) -> bool:
    """解析环境中的布尔开关，非法值按关闭处理以保持默认安全。"""

    return str(value).strip().lower() in {"1", "true", "yes", "on"}
