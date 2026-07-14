"""配置管理模块 — YAML 配置文件 + .env 密钥管理，通过 Pydantic 验证。"""

from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 加载 .env 文件
load_dotenv()

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="deepseek-v4-flash", alias="LLM_MODEL")
    llm_model_deep: str = Field(default="deepseek-v4-pro", alias="LLM_MODEL_DEEP")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def cache_dir(self) -> Path:
        return PROJECT_ROOT / "data" / "cache"

    @property
    def output_dir(self) -> Path:
        return PROJECT_ROOT / "output"

    @property
    def reports_dir(self) -> Path:
        return PROJECT_ROOT / self.report.output_dir

    @property
    def json_dir(self) -> Path:
        return PROJECT_ROOT / self.report.json_dir


def _load_yaml_config() -> dict:
    """从 config/settings.yaml 加载配置。"""
    yaml_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_field_mapping() -> dict:
    """从 config/field_mapping.yaml 加载字段映射。"""
    yaml_path = PROJECT_ROOT / "config" / "field_mapping.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 全局单例
_config: Optional[AppConfig] = None
_field_mapping: Optional[dict] = None


def get_config() -> AppConfig:
    """获取全局配置单例。"""
    global _config
    if _config is None:
        yaml_data = _load_yaml_config()
        _config = AppConfig(**yaml_data)
    return _config


def get_field_mapping() -> dict:
    """获取字段映射配置。"""
    global _field_mapping
    if _field_mapping is None:
        _field_mapping = _load_field_mapping()
    return _field_mapping