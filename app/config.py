"""Pi 平台 MVP 配置（实验配置）。

优先级：环境变量 > deploy/compose/.env > 默认值。
cliproxy API key 获取链：环境变量 CLIPROXY_API_KEY > ~/global-memory/cliproxy/config.yaml 的 api-keys。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEPLOY_DIR = BASE_DIR / "deploy"
COMPOSE_ENV = DEPLOY_DIR / "compose" / ".env"
CLIPROXY_CONFIG = Path.home() / "global-memory" / "cliproxy" / "config.yaml"
WORKSPACES_DIR = BASE_DIR / "workspaces"


def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def _cliproxy_api_key() -> str:
    env_key = os.environ.get("CLIPROXY_API_KEY")
    if env_key:
        return env_key
    # 从 cliproxy 配置中读取对外 api-keys（本机已有部署，避免重复提供凭据）
    try:
        import yaml

        cfg = yaml.safe_load(CLIPROXY_CONFIG.read_text(encoding="utf-8"))
        keys = (cfg or {}).get("api-keys") or []
        for k in keys:
            if isinstance(k, str) and k:
                return k
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            f"无法从 {CLIPROXY_CONFIG} 读取 api-keys（{exc}）。"
            "请设置环境变量 CLIPROXY_API_KEY。"
        ) from exc
    raise RuntimeError("cliproxy config.yaml 中未找到 api-keys。请设置环境变量 CLIPROXY_API_KEY。")


@dataclass(frozen=True)
class Settings:
    # PostgreSQL（S1 已搭建：127.0.0.1:15432, 库 pi_platform）
    pg_host: str = "127.0.0.1"
    pg_port: int = 15432
    pg_db: str = os.environ.get("PI_PG_DB", "pi_platform")
    pg_user: str = "pi_admin"
    pg_password: str = field(default_factory=lambda: _load_dotenv(COMPOSE_ENV).get("POSTGRES_PASSWORD", ""))

    # Model Gateway -> cliproxy（OpenAI 兼容）
    cliproxy_base_url: str = os.environ.get("CLIPROXY_BASE_URL", "http://127.0.0.1:8317/v1")
    cliproxy_api_key: str = field(default_factory=_cliproxy_api_key)
    cliproxy_model: str = os.environ.get("CLIPROXY_MODEL", "deepseek-v4-flash")
    # Gateway 预算（蓝图 §18.2 GW-07：超限 100% 阻断；每 attempt 一份 BudgetGrant）
    max_budget_tokens: int = int(os.environ.get("PI_MAX_BUDGET_TOKENS", "200000"))
    budget_reserve_tokens: int = int(os.environ.get("PI_BUDGET_RESERVE_TOKENS", "4096"))
    llm_attempts: int = int(os.environ.get("PI_LLM_ATTEMPTS", "3"))  # 每轮最多物理请求次数（每次独立预留）

    # 运行时
    workspaces_dir: Path = WORKSPACES_DIR
    cas_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "cas")  # G3：内容寻址 Blob 目录（gitignore 已排除 data/）
    max_turns: int = int(os.environ.get("PI_MAX_TURNS", "40"))
    max_tool_output_chars: int = int(os.environ.get("PI_MAX_TOOL_OUTPUT", "6000"))
    command_timeout_seconds: int = int(os.environ.get("PI_CMD_TIMEOUT", "60"))
    worker_threads: int = int(os.environ.get("PI_WORKER_THREADS", "2"))
    # G3：产物快照上限（防超大工作区拖垮收存）
    max_artifact_files: int = int(os.environ.get("PI_MAX_ARTIFACT_FILES", "200"))
    max_artifact_bytes: int = int(os.environ.get("PI_MAX_ARTIFACT_BYTES", str(10 * 1024 * 1024)))
    # G4：Skill 供应链源码目录（包以子目录形式存放，entrypoint=SKILL.md）
    skills_dir: Path = field(default_factory=lambda: BASE_DIR / "skills")

    @property
    def db_dsn(self) -> str:
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )


settings = Settings()