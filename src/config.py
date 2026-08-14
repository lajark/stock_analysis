"""配置管理模块 — YAML 配置文件 + 用户级密钥配置。"""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.errors import ConfigError
from src.runtime_paths import resource_root, settings_path, user_data_root

# 兼容原有导入；打包后指向 PyInstaller 资源目录。
PROJECT_ROOT = resource_root()


class TushareConfig(BaseSettings):
    """Tushare 数据源配置。"""
    timeout: int = 30
    retry_count: int = 3
    retry_delay: float = 1.0


class AkShareConfig(BaseSettings):
    """AkShare 数据源配置。"""
    timeout: int = 30
    retry_count: int = 3


class CacheConfig(BaseSettings):
    """缓存配置。"""
    enabled: bool = True
    ttl_daily: int = 86400       # 日线缓存 TTL（秒）
    ttl_financials: int = 604800 # 财务数据缓存 TTL（秒）


class AnalysisConfig(BaseSettings):
    """分析参数配置。"""
    ma_periods: list[int] = [5, 10, 20, 60]
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    kdj_n: int = 9
    kdj_m1: int = 3
    kdj_m2: int = 3


class ValuationConfig(BaseSettings):
    """估值分析配置。"""
    percentile_windows: list[int] = [1, 3, 5]


class LLMConfig(BaseSettings):
    """LLM 配置。"""
    default_mode: str = "quick"
    max_tokens: int = 2000
    temperature: float = 0.3


class ReportConfig(BaseSettings):
    """报告配置。"""
    output_dir: str = "output/reports"
    json_dir: str = "output/json"


class AppConfig(BaseSettings):
    """全局应用配置，从 YAML 文件加载。"""
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    # 数据源
    data_provider: str = "tushare"
    tushare: TushareConfig = Field(default_factory=TushareConfig)
    akshare: AkShareConfig = Field(default_factory=AkShareConfig)

    # 缓存
    cache: CacheConfig = Field(default_factory=CacheConfig)

    # 分析
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    valuation: ValuationConfig = Field(default_factory=ValuationConfig)

    # LLM
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # 报告
    report: ReportConfig = Field(default_factory=ReportConfig)

    # 环境变量（直接从 .env 读取）
    tushare_token: str = Field(default="", alias="TUSHARE_TOKEN")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="LLM_BASE_URL",
    )
    llm_model: str = Field(default="deepseek-v4-flash", alias="LLM_MODEL")
    llm_model_deep: str = Field(default="deepseek-v4-pro", alias="LLM_MODEL_DEEP")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def cache_dir(self) -> Path:
        return user_data_root() / "data" / "cache"

    @property
    def output_dir(self) -> Path:
        return user_data_root() / "output"

    @property
    def reports_dir(self) -> Path:
        return user_data_root() / self.report.output_dir

    @property
    def json_dir(self) -> Path:
        return user_data_root() / self.report.json_dir


def _load_yaml_config() -> dict:
    """从 config/settings.yaml 加载配置。"""
    yaml_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_field_mapping() -> dict:
    """从 config/field_mapping.yaml 加载字段映射。"""
    yaml_path = PROJECT_ROOT / "config" / "field_mapping.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 全局单例
_config: AppConfig | None = None
_field_mapping: dict | None = None


def get_config() -> AppConfig:
    """获取全局配置单例。"""
    global _config
    if _config is None:
        yaml_data = _load_yaml_config()
        _config = AppConfig(_env_file=settings_path(), **yaml_data)  # type: ignore[call-arg]
    return _config


def reset_config() -> None:
    """Clear cached configuration so newly saved settings take effect."""
    global _config
    _config = None


def get_user_settings(path: Path | None = None) -> dict[str, str]:
    """Read supported user settings without exposing unrelated environment data."""
    env_path = path or settings_path()
    values = dotenv_values(env_path) if env_path.exists() else {}
    config = get_config() if path is None else None

    def current(key: str, attribute: str, default: str = "") -> str:
        value = values.get(key)
        if value is not None:
            return str(value)
        if config is not None:
            return str(getattr(config, attribute))
        return default

    return {
        "TUSHARE_TOKEN": current("TUSHARE_TOKEN", "tushare_token"),
        "LLM_API_KEY": current("LLM_API_KEY", "llm_api_key"),
        "LLM_BASE_URL": current(
            "LLM_BASE_URL",
            "llm_base_url",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        "LLM_MODEL": current("LLM_MODEL", "llm_model", "deepseek-v4-flash"),
        "LLM_MODEL_DEEP": current("LLM_MODEL_DEEP", "llm_model_deep", "deepseek-v4-pro"),
    }


def save_user_settings(
    *,
    tushare_token: str,
    llm_api_key: str,
    llm_base_url: str,
    llm_model: str,
    llm_model_deep: str,
    path: Path | None = None,
) -> Path:
    """Persist supported settings atomically and reload application configuration."""
    values = {
        "TUSHARE_TOKEN": tushare_token.strip(),
        "LLM_API_KEY": llm_api_key.strip(),
        "LLM_BASE_URL": llm_base_url.strip(),
        "LLM_MODEL": llm_model.strip(),
        "LLM_MODEL_DEEP": llm_model_deep.strip(),
    }
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ConfigError("配置值不能包含换行符", key=key)

    env_path = path or settings_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = env_path.with_suffix(env_path.suffix + ".tmp")

    def quote(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    content = "\n".join(f"{key}={quote(value)}" for key, value in values.items()) + "\n"
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(env_path)
    if path is None:
        reset_config()
    return env_path


def get_field_mapping() -> dict:
    """获取字段映射配置。"""
    global _field_mapping
    if _field_mapping is None:
        _field_mapping = _load_field_mapping()
    return _field_mapping
