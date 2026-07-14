"""统一错误类型 — 项目中所有自定义异常的基类。"""


class StockAnalysisError(Exception):
    """股票分析错误基类。"""

    def __init__(self, message: str, code: str = "", original: Exception | None = None):
        self.code = code
        self.original = original
        super().__init__(message)


class DataProviderError(StockAnalysisError):
    """数据源错误。"""

    def __init__(
        self,
        message: str,
        provider: str = "",
        code: str = "",
        original: Exception | None = None,
    ):
        self.provider = provider
        super().__init__(message, code=code, original=original)


class DataValidationError(StockAnalysisError):
    """数据校验错误。"""

    def __init__(self, message: str, field: str = "", code: str = ""):
        self.field = field
        super().__init__(message, code=code)


class AnalysisError(StockAnalysisError):
    """分析计算错误。"""

    def __init__(self, message: str, module: str = "", code: str = ""):
        self.module = module
        super().__init__(message, code=code)


class LLMError(StockAnalysisError):
    """LLM 调用错误。"""

    def __init__(self, message: str, model: str = "", original: Exception | None = None):
        self.model = model
        super().__init__(message, original=original)


class ConfigError(StockAnalysisError):
    """配置错误。"""

    def __init__(self, message: str, key: str = ""):
        self.key = key
        super().__init__(message)