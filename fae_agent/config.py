"""프로젝트 설정: .env 파일이나 환경변수에서 값을 읽습니다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # mock: API 키 없이 규칙 기반으로 전체 흐름 확인 / llm: 실제 LLM 사용
    fae_mode: Literal["mock", "llm"] = "mock"
    # langchain init_chat_model 형식 "제공자:모델"
    llm_model: str = "openai:gpt-5.4-mini"
    # local: 도구 함수를 직접 호출 / mcp: MCP 서버(stdio)를 거쳐 호출
    tool_mode: Literal["local", "mcp"] = "local"
    # True면 사람 승인 단계를 자동으로 통과 (평가·테스트용)
    auto_approve: bool = False
    # 검수 실패 시 원인 분석을 다시 돌리는 최대 횟수
    max_qa_retries: int = 2
    # 데이터 폴더 (기본: 프로젝트/data)
    data_dir: Path = PROJECT_ROOT / "data"
    # 승인 뒤 문서(고객 메일 초안, 내부 보고서, 티켓 요약)를 만들지 여부
    generate_documents: bool = True
    # ngspice 실행 파일 경로 (비우면 PATH에서 ngspice_con → ngspice 순서로 찾음)
    ngspice_path: str = ""

    @property
    def docs_dirs(self) -> list[Path]:
        """지식 베이스로 읽을 문서 폴더: 직접 받은 문서 + 샘플 문서."""
        return [self.data_dir / "raw" / "docs", self.data_dir / "samples" / "docs"]

    @property
    def chunks_path(self) -> Path:
        return self.data_dir / "processed" / "chunks.jsonl"

    @property
    def parts_dir(self) -> Path:
        return self.data_dir / "parts"

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def eval_path(self) -> Path:
        return self.data_dir / "eval" / "eval_cases.jsonl"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tickets.db"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def circuits_dir(self) -> Path:
        return PROJECT_ROOT / "circuits"


@lru_cache
def get_settings() -> Settings:
    # .env의 API 키(OPENAI_API_KEY 등)도 환경변수로 올려 LangChain이 읽을 수 있게 합니다.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return Settings()
