"""LLM 客户端 — OpenAI 兼容接口封装。

支持阿里云百炼 DashScope、DeepSeek、OpenAI 等任意 OpenAI 兼容供应商。
通过 .env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 切换。
"""

from openai import OpenAI

from src.config import get_config


class LLMClient:
    """LLM 客户端，封装 OpenAI 兼容 API 调用。"""

    def __init__(self):
        config = get_config()
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self._model = config.llm_model
        self._model_deep = config.llm_model_deep
        self._max_tokens = config.llm.max_tokens
        self._temperature = config.llm.temperature
        self._last_usage: dict | None = None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        deep: bool = False,
    ) -> str:
        """调用 LLM 生成文本。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词（含分析数据）
            deep: 是否使用深度模型

        Returns:
            LLM 生成的文本。
        """
        model = self._model_deep if deep else self._model

        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        self._last_usage = {
            "model": model,
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

        return response.choices[0].message.content or ""

    @property
    def last_usage(self) -> dict | None:
        """最近一次调用的 Token 用量。"""
        return self._last_usage