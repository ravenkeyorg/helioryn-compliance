# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
"""Ingest uploaded documents into the evidence store."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from uuid import uuid4

from helioryn.embed import generate_embedding


def chunk_text(text: str, max_chars: int = 1500) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if current_len + len(line_stripped) > max_chars and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line_stripped)
        current_len += len(line_stripped)
    if current:
        chunks.append("\n".join(current))
    return chunks if chunks else [text]


async def ingest_document(
    store,
    file_path: str,
    title: str | None = None,
    *,
    retrieval_method: str = "upload",
) -> dict:
    from helioryn.ingest.documents.parser import extract_text
    from helioryn.models import SourceEvent, Claim
    from helioryn.hasher import content_hash as compute_hash

    text = extract_text(file_path)
    if not text.strip():
        return {"ingested": 0, "error": "No text could be extracted"}

    if not title:
        title = os.path.basename(file_path)

    chunks = chunk_text(text)
    ingested = 0
    now = datetime.now(timezone.utc)

    for chunk in chunks:
        ch = compute_hash(chunk)
        known = await store.is_content_known(ch)
        if known:
            continue

        sid = uuid4()

        # Insert into source_ingested — the trigger will create/update source_snapshot
        event = SourceEvent(
            event_id=uuid4(),
            source_id=sid,
            source_url=f"file://{file_path}#chunk-{ingested}",
            title=f"{title} (chunk {ingested + 1})",
            retrieved_at=now,
            raw_text=chunk,
            content_hash=ch,
            metadata={"file_path": file_path, "chunk_index": ingested},
            retrieval_method=retrieval_method,
        )
        await store.append_event(event)

        # Insert claim
        cid = uuid4()
        claim = Claim(
            claim_id=cid,
            source_id=sid,
            source_url=f"file://{file_path}#chunk-{ingested}",
            extracted_at=now,
            canonical_text=chunk,
            original_text=chunk,
            extraction_confidence=1.0,
            claim_type="org_document",
            topic="org-compliance",
        )
        await store.insert_claim(claim, topic="org-compliance")

        # Embed and store
        emb = generate_embedding(chunk)
        await store.store_embedding(cid, emb)

        ingested += 1

    return {"ingested": ingested, "file_path": file_path, "title": title}
