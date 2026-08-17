"""LLM 客户端 — OpenAI 兼容接口封装。

支持阿里云百炼 DashScope、DeepSeek、OpenAI 等任意 OpenAI 兼容供应商。
通过 .env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 切换。
"""

import threading
from collections.abc import Iterator
from typing import Any

from loguru import logger
from openai import BadRequestError, OpenAI

from src.config import get_config

# 进程级 LLM 并发上限（默认 1 = 串行，成本控制；与数据请求并发分开限制）。
_llm_semaphore: threading.Semaphore | None = None
_llm_semaphore_lock = threading.Lock()


def _llm_gate() -> threading.Semaphore:
    """Return the process-wide LLM concurrency gate, built on first use."""
    global _llm_semaphore
    if _llm_semaphore is None:
        with _llm_semaphore_lock:
            if _llm_semaphore is None:
                _llm_semaphore = threading.Semaphore(
                    max(1, get_config().batch.llm_max_concurrent)
                )
    return _llm_semaphore


class LLMStreamCancelledError(Exception):
    """Raised when the caller aborts an in-flight streaming generation.

    Deliberately a ``reports``-layer exception (not ``app.service``'s
    ``AnalysisCancelledError``): the layer direction is app -> reports, so the
    client must not import from the app. The service layer re-raises this as
    its own cancellation error at the checkpoint boundary.
    """


class LLMClient:
    """LLM 客户端，封装 OpenAI 兼容 API 调用。"""

    def __init__(self, config: Any | None = None):
        """Create a client.

        ``config`` is a lightweight dependency-injection hook (ROADMAP L226):
        when omitted the process-global ``get_config()`` is used; a caller may
        pass an explicit config object to target a specific vendor/instance.
        The process-wide concurrency gate still reads ``get_config()`` on first
        use, since it is a global cost control, not a per-instance setting.
        """
        if config is None:
            from src.config import get_config

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

        # Serialize LLM calls across worker threads (batch concurrency is
        # separate from the data-request gate; default keeps LLM serial).
        with _llm_gate():
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

    def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        deep: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        """Stream LLM output chunk by chunk, interruptible on ``cancel_event``.

        Like :meth:`generate` but with ``stream=True``. Holds the process-wide
        LLM gate for the whole stream. Token usage is captured from the final
        chunk when the provider honours ``stream_options["include_usage"]``;
        when it does not, ``last_usage`` stays ``None`` (honest, never faked).
        Cancellation is honoured between chunks (granularity = one chunk), and
        the underlying stream is closed on both normal and cancelled paths.
        """
        model = self._model_deep if deep else self._model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        usage = None
        with _llm_gate():
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            except BadRequestError:
                # Some compatible endpoints reject stream_options entirely;
                # fall back to a plain stream rather than dropping streaming.
                logger.warning(
                    "Streaming provider rejected include_usage; retrying plain stream"
                )
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                    stream=True,
                )
            try:
                for chunk in response:
                    if cancel_event is not None and cancel_event.is_set():
                        raise LLMStreamCancelledError("LLM 流式生成已由用户取消")
                    final_usage = getattr(chunk, "usage", None)
                    if final_usage is not None:
                        usage = final_usage
                    if not chunk.choices:
                        # Final usage-only chunk (include_usage) carries no text.
                        continue
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

        if usage is not None:
            self._last_usage = {
                "model": model,
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
        else:
            self._last_usage = None

    @property
    def last_usage(self) -> dict | None:
        """最近一次调用的 Token 用量。"""
        return self._last_usage
