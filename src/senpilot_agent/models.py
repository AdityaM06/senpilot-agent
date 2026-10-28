from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class DocumentType(StrEnum):
    EXHIBITS = "Exhibits"
    KEY_DOCUMENTS = "Key Documents"
    OTHER_DOCUMENTS = "Other Documents"
    TRANSCRIPTS = "Transcripts"
    RECORDINGS = "Recordings"

    @classmethod
    def parse(cls, value: str) -> DocumentType:
        normalized = re.sub(r"[_-]+", " ", value).strip().casefold()
        for member in cls:
            if normalized == member.value.casefold():
                return member
        raise ValueError(f"Choose one of: {', '.join(cls)}")


class DocumentRequest(BaseModel):
    matter_number: str = Field(pattern=r"^M\d{5}$")
    document_type: DocumentType

    @field_validator("matter_number", mode="before")
    @classmethod
    def normalize_matter(cls, value: str) -> str:
        return value.strip().upper()


class InboundEmail(BaseModel):
    gmail_id: str
    thread_id: str = ""
    message_id: str = ""
    sender: str
    subject: str = ""
    text: str = ""
    received_at: datetime = Field(default_factory=utc_now)
    automated: bool = False


class ParseResult(BaseModel):
    status: Literal["ready", "clarify", "unsupported"]
    matter_number: str | None
    document_type: DocumentType | None
    explanation: str

    @field_validator("matter_number", mode="before")
    @classmethod
    def normalize_matter(cls, value: str | None) -> str | None:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_ready(self) -> ParseResult:
        if self.status == "ready":
            if not self.matter_number or not self.document_type:
                raise ValueError("A ready request needs a matter number and document type")
            DocumentRequest(matter_number=self.matter_number, document_type=self.document_type)
        return self

    def request(self) -> DocumentRequest:
        if self.status != "ready":
            raise ValueError("This request is not ready for retrieval")
        return DocumentRequest(matter_number=self.matter_number, document_type=self.document_type)


class DocumentRecord(BaseModel):
    document_id: str
    title: str
    date: str = ""
    extension: str = ""
    public: bool = True
    categories: set[DocumentType] = Field(default_factory=set)


class MatterSnapshot(BaseModel):
    matter_number: str
    metadata: dict[str, str]
    source_url: str
    collected_at: datetime = Field(default_factory=utc_now)
    category_counts: dict[DocumentType, int]
    inventories: dict[DocumentType, list[DocumentRecord]]
    inventory_errors: dict[DocumentType, str] = Field(default_factory=dict)

    @property
    def unique_total(self) -> int | None:
        if self.inventory_errors or any(
            len({r.document_id for r in self.inventories.get(category, [])}) != count
            for category, count in self.category_counts.items()
        ):
            return None
        return len({r.document_id for rows in self.inventories.values() for r in rows})


class DownloadedFile(BaseModel):
    document: DocumentRecord
    path: Path
    byte_size: int
    sha256: str


class DownloadFailure(BaseModel):
    document_id: str
    reason: str


class RetrievalResult(BaseModel):
    request: DocumentRequest
    snapshot: MatterSnapshot
    files: list[DownloadedFile] = Field(default_factory=list)
    failures: list[DownloadFailure] = Field(default_factory=list)


class DeliveryPackage(BaseModel):
    zip_path: Path | None
    included_ids: list[str]
    omitted: list[DownloadFailure] = Field(default_factory=list)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SENDING = "sending"
    REPLIED = "replied"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    IGNORED = "ignored"


class Job(BaseModel):
    gmail_id: str
    state: JobState
    attempts: int
    outbound_id: str | None = None
    last_error: str | None = None
