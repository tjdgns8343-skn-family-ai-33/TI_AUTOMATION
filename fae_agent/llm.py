"""LLM 호출 도우미. FAE_MODE=llm일 때만 사용됩니다."""

from __future__ import annotations

from functools import lru_cache
from typing import TypeVar

from pydantic import BaseModel

from .config import get_settings

T = TypeVar("T", bound=BaseModel)


def is_llm_mode() -> bool:
    return get_settings().fae_mode == "llm"


@lru_cache
def get_chat_model():
    """init_chat_model로 제공자에 상관없이 채팅 모델을 만듭니다 (예: openai:gpt-5.4-mini)."""
    from langchain.chat_models import init_chat_model

    model_name = get_settings().llm_model
    if not model_name:
        raise RuntimeError("LLM_MODEL이 비어 있습니다. .env에 예: LLM_MODEL=openai:gpt-5.4-mini")
    return init_chat_model(model_name)


def structured_call(schema: type[T], system: str, user: str) -> T:
    """시스템·사용자 메시지를 보내고 결과를 Pydantic 모델로 받습니다."""
    model = get_chat_model().with_structured_output(schema)
    result = model.invoke([("system", system), ("human", user)])
    if isinstance(result, dict):  # 제공자에 따라 dict로 오는 경우 대비
        result = schema.model_validate(result)
    return result
