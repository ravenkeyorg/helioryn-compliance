# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceRecord(BaseModel):
    source_id: UUID = Field(default_factory=uuid4)
    source_url: str
    title: str | None = None
    author: str | None = None
    publish_date: datetime | None = None
    retrieved_at: datetime
    raw_text: str
    raw_html: str | None = None
    content_hash: str
    metadata: dict = Field(default_factory=dict)
    retrieval_method: str


class SourceEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    source_url: str
    title: str | None = None
    author: str | None = None
    publish_date: datetime | None = None
    retrieved_at: datetime
    raw_text: str
    raw_html: str | None = None
    content_hash: str
    metadata: dict = Field(default_factory=dict)
    retrieval_method: str
    ingested_at: datetime | None = None


class SourceSnapshot(BaseModel):
    source_id: UUID
    source_url: str
    title: str | None = None
    author: str | None = None
    publish_date: datetime | None = None
    retrieved_at: datetime
    raw_text: str
    raw_html: str | None = None
    content_hash: str
    metadata: dict = Field(default_factory=dict)
    retrieval_method: str
    first_seen_at: datetime
    last_updated_at: datetime


class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str
    source: str  # 'searxng', 'brave', etc.


class FetchedContent(BaseModel):
    url: str
    status_code: int
    headers: dict = Field(default_factory=dict)
    raw_html: str
    fetch_timestamp: datetime = Field(default_factory=_utcnow)


class NormalizedContent(BaseModel):
    url: str
    title: str | None = None
    author: str | None = None
    publish_date: datetime | None = None
    body_text: str
    raw_html: str | None = None
    metadata: dict = Field(default_factory=dict)


class Claim(BaseModel):
    claim_id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    source_url: str
    extracted_at: datetime = Field(default_factory=_utcnow)
    canonical_text: str
    original_text: str
    extraction_confidence: float = 1.0
    entities: list[dict] = Field(default_factory=list)
    claim_type: str = "fact"
    context_sentence: str | None = None
    topic: str | None = None


class Observation(BaseModel):
    observation_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    source_id: UUID
    observed_at: datetime = Field(default_factory=_utcnow)
    observer: str = "helioryn-ingest"
    context: str | None = None


class Entity(BaseModel):
    entity_id: UUID = Field(default_factory=uuid4)
    name: str
    entity_type: str = "concept"
    external_ids: dict = Field(default_factory=dict)


class ClaimEntity(BaseModel):
    claim_id: UUID
    entity_id: UUID


class ClaimRelationship(BaseModel):
    relationship_id: UUID = Field(default_factory=uuid4)
    source_claim_id: UUID
    target_claim_id: UUID
    relationship_type: str
    confidence: float = 1.0
    detected_by: str = "rule"
    detected_at: datetime = Field(default_factory=_utcnow)
    evidence: str | None = None


class ClaimEmbedding(BaseModel):
    embedding_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    embedding: list[float]
    model_name: str = "all-MiniLM-L6-v2"
    created_at: datetime = Field(default_factory=_utcnow)


class SourceCitation(BaseModel):
    title: str
    excerpt: str
    source_id: str
    retrieval_method: str
    url: str = ""


class ChatRequest(BaseModel):
    question: str
    mode: str = "public"
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceCitation] = []
    mode: str = "public"
