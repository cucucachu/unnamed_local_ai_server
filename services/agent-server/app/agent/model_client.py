"""Builds the `ChatOpenAI` client pointed at `model-runner`'s OpenAI-compatible API."""

from langchain_openai import ChatOpenAI

from app.core.config import Settings


def build_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.model_base_url,
        api_key="none",
        model=settings.model_name,
        temperature=1.0,
        streaming=True,
        max_retries=1,
        timeout=600,
    )
