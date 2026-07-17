# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import typer

from helioryn.config import AppConfig
from helioryn.hasher import content_hash
from helioryn.ingest import (
    create_searcher,
    create_fetcher,
    create_normalizer,
    create_ingestor,
)
from helioryn.extract import extract_entities
from helioryn.models import (
    FetchedContent,
    NormalizedContent,
    SourceEvent,
    SourceSnapshot,
)
from helioryn.store import EventStore

app = typer.Typer()
ingest_app = typer.Typer()
topic_app = typer.Typer()
extract_app = typer.Typer()
discover_app = typer.Typer()
query_app = typer.Typer()
entity_app = typer.Typer()
rel_app = typer.Typer()
source_app = typer.Typer()
app.add_typer(ingest_app, name="ingest", help="Ingest commands")
app.add_typer(topic_app, name="topic", help="Topic management")
app.add_typer(extract_app, name="extract", help="Extract claims from sources")
app.add_typer(discover_app, name="discover", help="Discovery engine")
app.add_typer(query_app, name="query", help="Manage search queries")
app.add_typer(entity_app, name="entity", help="Manage government entities")
verify_app = typer.Typer()
app.add_typer(verify_app, name="verify", help="Verification and health checks")
app.add_typer(rel_app, name="rel", help="Claim relationship graph (Layer 3b/3c)")
app.add_typer(source_app, name="source", help="Source behavior and intelligence")

ledger_app = typer.Typer()
app.add_typer(ledger_app, name="ledger", help="Immutable evidence hash chain")


async def _link_entities(store: EventStore, claim, entities: list[dict]):
    if not entities:
        return
    async with store._pool.acquire() as conn:
        async with conn.transaction():
            for ed in entities:
                eid = await store.upsert_claim_entity(ed["name"], ed.get("type", "concept"), conn=conn)
                mention = ed.get("mention")
                await conn.execute(
                    "INSERT INTO claim_entity (claim_id, entity_id, mention) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    claim.claim_id, eid, mention,
                )


async def _get_store(config: AppConfig) -> EventStore:
    store = EventStore(config.database_url)
    try:
        await store.connect()
    except (FileNotFoundError, OSError, ConnectionRefusedError) as e:
        typer.echo(f"Error: Could not connect to database at {config.database_url}", err=True)
        typer.echo(f"  {e}", err=True)
        typer.echo("", err=True)
        typer.echo("  Helioryn CLI must run on the server where PostgreSQL is installed.", err=True)
        typer.echo("  SSH to M4 and run commands there:", err=True)
        typer.echo(f"    ssh localuser@m4 'source ~/helioryn/venv/bin/activate && helioryn <command>'", err=True)
        typer.echo("", err=True)
        typer.echo("  Or use connect.sh:", err=True)
        typer.echo("    ./connect.sh daemon status    # check daemon status", err=True)
        raise typer.Exit(1)
    await store.ensure_schema()
    return store


# --- Ingest URL --- #


@ingest_app.command("url")
def ingest_url(
    url: str,
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Ingest a single URL."""
    asyncio.run(_ingest_url(url, config_path))


async def _ingest_url(url: str, config_path: str | None):
    import helioryn.log as log

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    archived = await store.is_url_archived(url)
    if archived:
        typer.echo(f"Already archived: {url}")
        await store.close()
        return

    fetcher = create_fetcher(config)
    normalizer = create_normalizer(config)
    ingestor = create_ingestor(config, store)

    fetched = await fetcher.fetch(url)
    normalized = await normalizer.normalize(fetched)
    result = await ingestor.ingest(normalized)

    if result:
        typer.echo(f"Ingested: {result.source_id}  ({normalized.url})")
        log.emit("source_ingested", url=url, source_id=str(result.source_id))
    else:
        typer.echo(f"Duplicate (same content): {url}")

    await store.close()


# --- Ingest File --- #


@ingest_app.command("file")
def ingest_file(
    file_path: str = typer.Argument(..., help="Path to file"),
    url: str | None = typer.Option(None, "--url", help="Source URL (optional)"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Ingest content from a local file."""
    asyncio.run(_ingest_file(file_path, url, config_path))


async def _ingest_file(file_path: str, source_url: str | None, config_path: str | None):
    import helioryn.log as log

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    path = Path(file_path)
    if not path.exists():
        typer.echo(f"File not found: {file_path}", err=True)
        raise typer.Exit(1)

    raw_text = path.read_text(encoding="utf-8")
    url = source_url or f"file://{path.absolute()}"

    normalized = NormalizedContent(
        url=url,
        body_text=raw_text,
        metadata={"file_path": str(path.absolute())},
    )
    ingestor = create_ingestor(config, store)
    result = await ingestor.ingest(normalized)

    if result:
        typer.echo(f"Ingested: {result.source_id}  ({url})")
        log.emit("source_ingested", url=url, source_id=str(result.source_id))
    else:
        typer.echo(f"Duplicate: {url}")

    await store.close()


# --- Ingest Stdin --- #


@ingest_app.command("stdin")
def ingest_stdin(
    url: str | None = typer.Option(None, "--url", help="Source URL (optional)"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Ingest content from stdin."""
    asyncio.run(_ingest_stdin(url, config_path))


async def _ingest_stdin(source_url: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    raw_text = sys.stdin.read()
    url = source_url or "stdin://local"

    normalized = NormalizedContent(
        url=url,
        body_text=raw_text,
        metadata={"source": "stdin"},
    )
    ingestor = create_ingestor(config, store)
    result = await ingestor.ingest(normalized)

    if result:
        typer.echo(f"Ingested: {result.source_id}  ({url})")
    else:
        typer.echo(f"Duplicate: {url}")

    await store.close()


# --- Ingest Run (full pipeline) --- #


@ingest_app.command("run")
def ingest_run(
    topic: str | None = typer.Option(None, "--topic", "-t", help="Override topic query"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run the full ingest pipeline: search → fetch → normalize → archive."""
    asyncio.run(_ingest_run(topic, config_path))


async def _ingest_run(topic: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    searcher = create_searcher(config)
    fetcher = create_fetcher(config)
    normalizer = create_normalizer(config)
    ingestor = create_ingestor(config, store)

    queries = [topic] if topic else [t.query for t in config.ingest.topics]
    if not queries:
        typer.echo("No topic configured. Use --topic or add topics to config.", err=True)
        raise typer.Exit(1)

    import helioryn.log as log

    for query in queries:
        run_id = log.RunEvent("run_started", topic=query).run_id
        log.emit("run_started", run_id=run_id, topic=query)
        typer.echo(f"Searching: {query}")
        results = await searcher.search(query)

        if not results:
            typer.echo("  No results found.")
            continue

        ingested_count = 0
        skipped_count = 0
        error_count = 0

        for result in results:
            archived = await store.is_url_archived(result.url)
            if archived:
                typer.echo(f"  Skipped (archived): {result.url}")
                log.emit("source_skipped", run_id=run_id, url=result.url, reason="url_archived")
                skipped_count += 1
                continue

            try:
                fetched = await fetcher.fetch(result.url)
                normalized = await normalizer.normalize(fetched)
                ingested = await ingestor.ingest(normalized)
                if ingested:
                    typer.echo(f"  Ingested: {result.title[:60]}")
                    log.emit("source_ingested", run_id=run_id, source_id=str(ingested.source_id), url=result.url, title=result.title)
                    ingested_count += 1
                else:
                    typer.echo(f"  Duplicate: {result.url}")
                    log.emit("source_skipped", run_id=run_id, url=result.url, reason="content_duplicate")
                    skipped_count += 1

                if config.ingest.fetch_delay > 0:
                    await asyncio.sleep(config.ingest.fetch_delay)
            except Exception as e:
                typer.echo(f"  Failed: {result.url} — {e}", err=True)
                log.emit("source_failed", run_id=run_id, url=result.url, error=str(e))
                error_count += 1

        log.emit("run_completed", run_id=run_id, ingested=ingested_count, skipped=skipped_count, errors=error_count)

    await store.close()


# --- Ingest Watch (daemon) --- #


@ingest_app.command("watch")
def ingest_watch(
    topic: str | None = typer.Option(None, "--topic", "-t", help="Topic to watch"),
    interval: int = typer.Option(360, "--interval", "-i", help="Minutes between runs"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run ingest pipeline on a schedule (daemon mode)."""
    import helioryn.log as log
    import schedule
    import time

    log.write_pid()

    def run_job():
        asyncio.run(_ingest_run(topic, config_path))

    if topic:
        schedule.every(interval).minutes.do(run_job)
        typer.echo(f"Watching topic '{topic}' every {interval} minutes. Ctrl+C to stop.")
        run_job()
    else:
        config = AppConfig.load(config_path)
        for t in config.ingest.topics:
            schedule.every(t.interval_minutes).minutes.do(run_job)
            typer.echo(f"Watching topic '{t.query}' every {t.interval_minutes} minutes.")
        if not config.ingest.topics:
            typer.echo("No topics configured. Use --topic or add topics to config.", err=True)
            raise typer.Exit(1)
        run_job()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
    finally:
        pass


# ── Annotation ───────────────────────────────────────────────────────────────


@app.command()
def annotate(
    target_type: str = typer.Argument(..., help="Target type: claim, source, entity, narrative"),
    target_id: str = typer.Argument(..., help="Target UUID"),
    body: str = typer.Option(..., "--body", "-b", help="Annotation body text"),
    author: str = typer.Option("admin", "--author", "-a", help="Your name or ID"),
    tags: str = typer.Option("", "--tags", "-t", help="Comma-separated tags"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Add an annotation to a claim, source, entity, or narrative."""
    asyncio.run(_annotate_cmd(target_type, UUID(target_id), body, author, tags, config_path))


async def _annotate_cmd(target_type: str, target_id: UUID, body: str,
                         author: str, tags: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        aid = await store.insert_annotation(target_type, target_id, author, body, tag_list)
        typer.echo(f"Annotation created: {aid}")
    finally:
        await store.close()


@app.command("resolve-annotation")
def resolve_annotation_cmd(
    annotation_id: str = typer.Argument(..., help="Annotation UUID to resolve"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Mark an annotation as resolved."""
    asyncio.run(_resolve_annotation_cmd(UUID(annotation_id), config_path))


async def _resolve_annotation_cmd(annotation_id: UUID, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        ok = await store.resolve_annotation(annotation_id)
        typer.echo(f"Annotation {'resolved' if ok else 'not found'}")
    finally:
        await store.close()


# ── Investigation ────────────────────────────────────────────────────────────


@app.command("investigation-create")
def investigation_create(
    name: str = typer.Option(..., "--name", "-n", help="Investigation name"),
    description: str = typer.Option("", "--description", "-d", help="Description"),
    owner: str = typer.Option("admin", "--owner", "-o", help="Owner name"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Create a new investigation."""
    asyncio.run(_investigation_create_cmd(name, description, owner, config_path))


async def _investigation_create_cmd(name: str, description: str, owner: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        iid = await store.create_investigation(name, description or None, owner)
        typer.echo(f"Investigation created: {iid}")
    finally:
        await store.close()


@app.command("investigation-close")
def investigation_close(
    investigation_id: str = typer.Argument(..., help="Investigation UUID"),
    resolution: str = typer.Option(..., "--resolution", "-r", help="Resolution notes"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Close an investigation."""
    asyncio.run(_investigation_close_cmd(UUID(investigation_id), resolution, config_path))


async def _investigation_close_cmd(investigation_id: UUID, resolution: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        ok = await store.close_investigation(investigation_id, resolution)
        typer.echo(f"Investigation {'closed' if ok else 'not found or already closed'}")
    finally:
        await store.close()


@app.command("investigation-list")
def investigation_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status (open/closed)"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List investigations."""
    asyncio.run(_investigation_list_cmd(status, config_path))


async def _investigation_list_cmd(status: str | None, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        items = await store.list_investigations(status)
        if not items:
            typer.echo("No investigations found.")
            return
        typer.echo(f"\nInvestigations ({len(items)}):")
        for inv in items:
            status_c = "○" if inv["status"] == "open" else "✓"
            owner = inv["owner"] or "?"
            n_claims = len(inv["claims"] or [])
            n_sources = len(inv["sources"] or [])
            typer.echo(f"  [{status_c}] {str(inv['investigation_id'])[:8]}  {inv['name']:<40}  by {owner}  ({n_claims}c, {n_sources}s)")
    finally:
        await store.close()


@app.command("investigation-add")
def investigation_add(
    investigation_id: str = typer.Argument(..., help="Investigation UUID"),
    claim_id: str | None = typer.Option(None, "--claim", "-c", help="Claim UUID to add"),
    source_id: str | None = typer.Option(None, "--source", "-s", help="Source UUID to add"),
    narrative_id: str | None = typer.Option(None, "--narrative", "-n", help="Narrative UUID to add"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Add claims, sources, or narratives to an investigation."""
    cids = [UUID(claim_id)] if claim_id else None
    sids = [UUID(source_id)] if source_id else None
    nids = [UUID(narrative_id)] if narrative_id else None
    asyncio.run(_investigation_add_cmd(UUID(investigation_id), cids, sids, nids, config_path))


async def _investigation_add_cmd(investigation_id: UUID, cids, sids, nids, config_path):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        ok = await store.add_to_investigation(investigation_id, cids, sids, nids)
        typer.echo(f"Added to investigation: {'OK' if ok else 'not found'}")
    finally:
        await store.close()


@app.command("investigation-note")
def investigation_note_cmd(
    investigation_id: str = typer.Argument(..., help="Investigation UUID"),
    body: str = typer.Option(..., "--body", "-b", help="Note body text"),
    author: str = typer.Option("analyst", "--author", "-a", help="Note author"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Add a note to an investigation."""
    asyncio.run(_investigation_note_cmd(UUID(investigation_id), author, body, config_path))


async def _investigation_note_cmd(investigation_id: UUID, author: str, body: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        note_id = await store.add_investigation_note(investigation_id, author, body)
        if note_id:
            typer.echo(f"Note added: {note_id}")
        else:
            typer.echo("Investigation not found.")
    finally:
        await store.close()


@app.command("investigation-detail")
def investigation_detail_cmd(
    investigation_id: str = typer.Argument(..., help="Investigation UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show investigation details with notes."""
    asyncio.run(_investigation_detail_cmd(UUID(investigation_id), config_path))


async def _investigation_detail_cmd(investigation_id: UUID, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        inv = await store.get_investigation_detail(investigation_id)
        if not inv:
            typer.echo("Investigation not found.")
            return
        typer.echo(f"\nInvestigation: {inv['name']}")
        typer.echo(f"  ID:     {inv['investigation_id']}")
        typer.echo(f"  Status: {inv['status']}")
        typer.echo(f"  Owner:  {inv['owner']}")
        if inv.get("description"):
            typer.echo(f"  Desc:   {inv['description']}")
        typer.echo(f"  Claims:      {len(inv.get('claims') or [])}")
        typer.echo(f"  Sources:     {len(inv.get('sources') or [])}")
        typer.echo(f"  Narratives:  {len(inv.get('narratives') or [])}")
        if inv.get("resolved_at"):
            typer.echo(f"  Resolved: {inv['resolved_at']} — {inv.get('resolution', '')}")

        notes = await store.list_investigation_notes(investigation_id)
        if notes:
            typer.echo(f"\n  Notes ({len(notes)}):")
            for n in notes:
                typer.echo(f"    [{n['created_at']:%Y-%m-%d %H:%M}] {n['author']}: {n['body'][:120]}")
    finally:
        await store.close()


# --- Staging Queue --- #


@app.command("staging-list")
def staging_list_cmd(
    status: str = typer.Option("pending", "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max items"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List items in the staging/review queue."""
    asyncio.run(_staging_list_cmd(status, limit, config_path))


async def _staging_list_cmd(status: str, limit: int, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        items = await store.list_staging(status, limit)
        if not items:
            typer.echo(f"No {status} staging items found.")
            return
        typer.echo(f"\nStaging Queue ({status}):")
        for it in items:
            typer.echo(f"  {str(it['queue_id'])[:8]}  {it['target_type']:<8}  "
                       f"{it['target_label'][:50]:<50}  "
                       f"by {it['submitted_by']}  [{it['submitted_at']:%Y-%m-%d %H:%M}]")
    finally:
        await store.close()


@app.command("staging-review")
def staging_review_cmd(
    queue_id: str = typer.Argument(..., help="Queue item UUID"),
    decision: str = typer.Argument(..., help="approved or rejected"),
    reviewer: str = typer.Option("analyst", "--reviewer", "-r", help="Reviewer name"),
    notes: str | None = typer.Option(None, "--notes", "-n", help="Review notes"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Review (approve/reject) a staging queue item."""
    asyncio.run(_staging_review_cmd(UUID(queue_id), reviewer, decision, notes, config_path))


async def _staging_review_cmd(queue_id: UUID, reviewer: str, decision: str, notes: str | None, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        ok = await store.review_staging_item(queue_id, reviewer, decision, notes)
        typer.echo(f"Review: {'approved' if ok else 'failed or already reviewed'}")
    finally:
        await store.close()


@app.command("staging-submit")
def staging_submit_cmd(
    target_type: str = typer.Argument(..., help="Type: claim, source, or narrative"),
    target_id: str = typer.Argument(..., help="Target UUID"),
    submitted_by: str = typer.Option("system", "--by", help="Submitter name"),
    notes: str | None = typer.Option(None, "--notes", "-n", help="Submission notes"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Submit an item to the staging/review queue."""
    asyncio.run(_staging_submit_cmd(target_type, UUID(target_id), submitted_by, notes, config_path))


async def _staging_submit_cmd(target_type: str, target_id: UUID, submitted_by: str, notes: str | None, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        qid = await store.submit_to_staging(target_type, target_id, submitted_by, notes)
        typer.echo(f"Submitted to staging: {qid}")
    finally:
        await store.close()


# --- Show --- #


@app.command()
def show_source(
    source_id: str = typer.Argument(..., help="Source UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show source detail and provenance."""
    asyncio.run(_show_source(UUID(source_id), config_path))


async def _show_source(source_id: UUID, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    snapshot = await store.get_snapshot(source_id)
    if not snapshot:
        typer.echo(f"Source not found: {source_id}")
        await store.close()
        raise typer.Exit(1)

    events = await store.get_events(source_id)

    typer.echo(f"Source ID:    {snapshot.source_id}")
    typer.echo(f"URL:          {snapshot.source_url}")
    typer.echo(f"Title:        {snapshot.title or '(none)'}")
    typer.echo(f"Author:       {snapshot.author or '(unknown)'}")
    typer.echo(f"Published:    {snapshot.publish_date or '(unknown)'}")
    typer.echo(f"First seen:   {snapshot.first_seen_at}")
    typer.echo(f"Last updated: {snapshot.last_updated_at}")
    typer.echo(f"Content hash: {snapshot.content_hash}")
    typer.echo(f"Method:       {snapshot.retrieval_method}")
    typer.echo(f"Versions:     {len(events)}")
    if snapshot.metadata:
        head = snapshot.metadata.get("head_meta", {})
        if head:
            typer.echo(f"Meta tags:    {len(head)} extracted")
        canonical = snapshot.metadata.get("canonical_url")
        if canonical:
            typer.echo(f"Canonical:    {canonical}")
        lang = snapshot.metadata.get("language")
        if lang:
            typer.echo(f"Language:     {lang}")
    typer.echo("---")
    typer.echo(snapshot.raw_text[:500])
    if len(snapshot.raw_text) > 500:
        typer.echo("... (truncated)")

    await store.close()


# --- Search --- #


@app.command()
def search(
    query: str = typer.Argument(..., help="Search archived content"),
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Search archived content."""
    asyncio.run(_search(query, limit, config_path))


async def _search(query: str, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    results = await store.search_content(query, limit=limit)
    if not results:
        typer.echo("No matches found.")
    else:
        for s in results:
            title = s.title or "(no title)"
            typer.echo(f"{s.source_id}  {s.last_updated_at.date()}  {title[:60]}")
    await store.close()


# --- History --- #


@app.command()
def history(
    limit: int = typer.Option(10, "--limit", "-l"),
):
    """Show recent ingest runs."""
    from helioryn.log import get_runs
    runs = get_runs(limit=limit)
    if not runs:
        typer.echo("No runs recorded yet.")
        return

    typer.echo(f"{'Run ID':<10} {'Started':<22} {'Topic':<20} {'In':>4} {'Skip':>4} {'Err':>4}")
    typer.echo("-" * 70)
    for r in runs:
        started = r.get("started", "")[11:19] if r.get("started") else ""
        topic = (r.get("topic", "") or "")[:20]
        ing = r.get("ingested", 0)
        skp = r.get("skipped", 0)
        err = r.get("errors", 0)
        typer.echo(
            f"{r['run_id']:<10} {started:<22} {topic:<20} {ing:>4} {skp:>4} {err:>4}"
        )


# --- Stats --- #


@app.command()
def stats(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show database statistics."""
    asyncio.run(_stats(config_path))


async def _stats(config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    s = await store.get_stats()
    query_count = await store.get_query_count()
    entity_count = await store.get_entity_count()

    from datetime import datetime, timezone
    def _local(ts):
        if not ts:
            return "-"
        try:
            dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%m-%d %I:%M %p")
        except:
            return str(ts)[:16]

    typer.echo(f"Sources:       {s['total_sources']}")
    typer.echo(f"Events:        {s['total_events']}")
    typer.echo(f"Claims:        {s['total_claims']}")
    typer.echo(f"Observations:  {s['total_observations']}")
    typer.echo(f"Embeddings:    {s.get('total_embeddings', 0)}")
    typer.echo(f"Relationships: {s.get('total_relationships', 0)}"
               f"  ({s.get('total_repeated_by', 0)} same,"
               f" {s.get('total_contradictions', 0)} conflicts)")
    typer.echo(f"Queries:       {query_count}")
    typer.echo(f"Gov entities:  {entity_count}")
    typer.echo(f"Entities:      {s.get('total_entities', 0)}")
    typer.echo(f"Narratives:    {s.get('total_narratives', 0)}")
    typer.echo(f"Updated:       {s['updated_sources']}")
    typer.echo(f"Ingest rate:   {s.get('rate_1h', 0)}/h  {s.get('rate_24h', 0)}/24h")
    typer.echo(f"Oldest:        {_local(s.get('oldest_source'))}")
    typer.echo(f"Newest:        {_local(s.get('newest_source'))}")
    await store.close()


# --- Status --- #


@app.command()
def status(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Check daemon status."""
    from helioryn.log import read_pid, get_runs

    pid = read_pid()
    if pid:
        import os
        try:
            os.kill(pid, 0)
            typer.echo(f"Daemon:  running (PID {pid})")
        except OSError:
            typer.echo("Daemon:  not running (stale PID file)")
    else:
        typer.echo("Daemon:  not running")

    runs = get_runs(limit=1)
    if runs:
        r = runs[0]
        typer.echo(f"Last run:  {r.get('started', '?')[:19]}")
        typer.echo(f"Topic:     {r.get('topic', '?')}")
        typer.echo(f"Result:    {r.get('ingested', 0)} ingested, "
                    f"{r.get('skipped', 0)} skipped, "
                    f"{r.get('errors', 0)} errors")
    else:
        typer.echo("Last run:  never")


# --- Daemon --- #


_shutdown_event: asyncio.Event | None = None


def _signal_handler(signum, frame):
    ev = _shutdown_event
    if ev is not None:
        ev.set()


# ── Process Watcher ──────────────────────────────────────────────────────────

def _make_log_fn(name: str):
    log_path = Path(f"/tmp/helioryn-{name}.log")
    def _log(msg: str):
        try:
            with open(log_path, "a") as f:
                f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
        except Exception:
            pass
    return _log, log_path


async def _process_watch(name: str, interval: int, config_path: str | None, cycle_fn, max_queries: int = 10, results_per_query: int = 5):
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    _log, _log_path = _make_log_fn(name)

    # Register signal handlers so SIGTERM/SIGINT shut down gracefully
    import signal as _sig
    try:
        _sig.signal(_sig.SIGTERM, _signal_handler)
        _sig.signal(_sig.SIGINT, _signal_handler)
    except ValueError:
        pass  # not in main thread

    # ── Cross-daemon trigger: listen for upstream daemon completion ──
    from helioryn.cache import CH_PIPELINE, CH_SCORER, CH_ANALYZER, CH_INTERPRETER, CH_GROUNDING, HeliorynCache
    _TRIGGER_MAP = {
        "scorer": CH_PIPELINE,
        "analyzer": CH_SCORER,
        "interpreter": CH_ANALYZER,
        "ground": CH_GROUNDING,
    }
    trigger_channel = _TRIGGER_MAP.get(name)

    _log(f"{name} watcher starting (PID={os.getpid()}, interval={interval}min)")

    while not _shutdown_event.is_set():
        try:
            kwargs = {}
            if name == "pipeline":
                kwargs = {"max_queries": max_queries, "results_per_query": results_per_query}
            await cycle_fn(config_path, _log, **kwargs)
        except asyncio.CancelledError:
            _log(f"{name} watcher cancelled")
            break
        except Exception as e:
            _log(f"{name} cycle failed: {e}")
            import traceback
            _log(traceback.format_exc())

        if _shutdown_event.is_set():
            break

        _log(f"Sleeping {interval} min until next {name} cycle")
        # Listen for trigger event from upstream daemon while sleeping
        pubsub = None
        cache = None
        if trigger_channel:
            try:
                _cfg = AppConfig.load(config_path)
                if _cfg.redis_url:
                    cache = HeliorynCache(_cfg.redis_url)
                    await cache.connect()
                    pubsub = await cache.subscribe(trigger_channel)
            except Exception:
                pass
        for _ in range(interval * 12):
            try:
                if pubsub:
                    msg = await cache.get_message(pubsub, timeout=4.9)
                    if msg and msg.get("status") == "completed":
                        _log(f"Triggered by upstream {trigger_channel} — running early")
                        break
                else:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=5)
                    break
            except asyncio.TimeoutError:
                pass
        if cache:
            try:
                await cache.close()
            except Exception:
                pass

    _log(f"{name} watcher stopped")


# ── Redis pub/sub helper ─────────────────────────────────────────────────────

async def _publish_completion(channel: str, msg: dict, redis_url: str | None):
    """Publish a completion event to Redis (best-effort, no-op if not configured)."""
    if not redis_url:
        return
    try:
        from helioryn.cache import HeliorynCache
        c = HeliorynCache(redis_url)
        await c.connect()
        await c.publish(channel, msg)
        await c.close()
    except Exception:
        pass  # Redis is optional — never break the cycle


async def _update_daemon_status(name: str, redis_url: str | None, ttl: int = 7200, **kw):
    """Update per-daemon status in Redis for the Operations tab (best-effort).

    Preserves last_result and last_completed_at from the previous status
    when writing a "processing" state, so "Last Run" doesn't clear mid-cycle.
    """
    if not redis_url:
        return
    try:
        from helioryn.cache import HeliorynCache
        import os as _os
        from datetime import datetime, timezone
        c = HeliorynCache(redis_url)
        await c.connect()
        if kw.get("state") == "processing":
            existing = await c.get_status(f"daemon:{name}")
            if existing:
                kw.setdefault("last_result", existing.get("last_result"))
                kw.setdefault("last_completed_at", existing.get("last_completed_at"))
        status = {"name": name, "pid": _os.getpid(), "updated_at": datetime.now(timezone.utc).isoformat(), **kw}
        await c.put_status(f"daemon:{name}", status, ttl=ttl)
        await c.close()
    except Exception:
        pass


# ── Pipeline Cycle ───────────────────────────────────────────────────────────

async def _pipeline_cycle(config_path: str | None, _log=None, max_queries: int = 10, results_per_query: int = 5):
    """One cycle of discovery → extract → enrich → embed → relate."""
    from helioryn.config import AppConfig
    from helioryn.discovery import run_discovery_cycle
    from helioryn.discovery.entity_db import auto_generate_queries_from_claim_entities
    from helioryn.housekeeping import disk_usage, cleanup_pg_temp, rotate_all_logs
    from helioryn.log import write_status
    from helioryn.store import EventStore

    if _log is None:
        _log = _make_log_fn("pipeline")[0]

    from helioryn.cache import CH_PIPELINE
    config = AppConfig.load(config_path)
    await _update_daemon_status("pipeline", config.redis_url, state="processing")
    store = EventStore(config.database_url)
    await store.connect()

    from helioryn.constants import TOPIC_BY_CATEGORY

    if config.ingest.topics:
        seeded = await store.seed_queries_from_config(config.ingest.topics)
        if seeded:
            _log(f"Seeded {seeded} queries from config")

    try:
        _disk = disk_usage()
        _free = _disk["pct_free"]
        _skip_deep = False
        if _free < 5:
            _log(f"CRITICAL: disk at {_free}% — running cleanup, skipping deep processing")
            await asyncio.to_thread(cleanup_pg_temp)
            rotate_all_logs()
            _skip_deep = True
        elif _free < 10:
            _log(f"WARNING: disk at {_free}% — skipping deep processing")
            _skip_deep = True

        failed_urls = set()

        def _disc_progress(msg: str):
            _log(f"  discovery: {msg}")

        ingested, skipped, errors = await run_discovery_cycle(
            config, store,
            max_queries=max_queries,
            results_per_query=results_per_query,
            search_pages=1,
            skip_urls=failed_urls,
            progress_callback=_disc_progress,
        )
        _log(f"Discovery done: ingested={ingested}, skipped={skipped}, errors={errors}")
        await _update_daemon_status("pipeline", config.redis_url, state="processing", progress=10,
            progress_msg=f"Discovery: {ingested} ingested")

        cycle_claims = 0
        cycle_embs = 0
        cycle_rels = ""

        if ingested > 0 and not _skip_deep:
            _log("Starting post-processing (extract → enrich → embed → relate)")
            try:
                from helioryn.extract import extract_claims, extract_entities
                from helioryn.extract.temporal import extract_temporal_references
                from helioryn.extract.uncertainty import detect_uncertainty
                from helioryn.embed import generate_batch_embeddings
                from helioryn.models import Observation

                async with store._pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT ss.source_id FROM source_snapshot ss "
                        "WHERE NOT EXISTS (SELECT 1 FROM claim c WHERE c.source_id = ss.source_id) "
                        "ORDER BY ss.last_updated_at DESC LIMIT 100"
                    )

                if rows:
                    _log(f"Extracting claims from {len(rows)} new sources")
                claim_ids: list[UUID] = []
                claim_texts: list[str] = []
                source_ok = 0
                claim_ok = 0
                claim_err = 0
                source_claim_counts: dict[UUID, int] = {}
                for row in rows:
                    sid = row["source_id"]
                    try:
                        snapshot = await store.get_snapshot(sid)
                        if not snapshot:
                            continue
                        category = (snapshot.metadata or {}).get("query_category", "")
                        topic = TOPIC_BY_CATEGORY.get(category, "") if category else ""
                        claims = extract_claims(snapshot)
                        source_ok += 1
                    except Exception as ex:
                        _log(f"  extract_claims failed for {str(sid)[:8]}: {ex}")
                        claim_err += len(claims) if 'claims' in dir() and claims else 0
                        continue
                    for c in claims:
                        try:
                            stored = await store.insert_claim(c, topic=topic or None)
                            if not stored:
                                continue
                            claim_ids.append(stored.claim_id)
                            claim_texts.append(c.canonical_text)
                            try:
                                entities = extract_entities(c.canonical_text)
                                await _link_entities(store, stored, entities)
                            except Exception as ex:
                                _log(f"  entity extraction/link failed for claim {str(stored.claim_id)[:8]}: {ex}")
                            try:
                                obs = Observation(claim_id=stored.claim_id, source_id=sid, observer="helioryn-pipeline", context=c.context_sentence)
                                await store.insert_observation(obs)
                            except Exception as ex:
                                _log(f"  observation insert failed: {ex}")
                            try:
                                await store.classify_claim_originality(stored.claim_id, sid)
                            except Exception as ex:
                                _log(f"  classify_claim_originality failed: {ex}")
                            claim_ok += 1
                            source_claim_counts[sid] = source_claim_counts.get(sid, 0) + 1
                        except Exception as ex:
                            _log(f"  claim insert failed for {str(sid)[:8]}: {ex}")
                            claim_err += 1
                if source_claim_counts:
                    for sid, n_claims in source_claim_counts.items():
                        try:
                            await store.update_source_behavior(sid, {"n_claims": n_claims})
                        except Exception as ex:
                            _log(f"  update_source_behavior failed for {str(sid)[:8]}: {ex}")
                if claim_ids:
                    _log(f"Extracted {claim_ok} claims (+{claim_err} errors) from {source_ok} sources")
                await _update_daemon_status("pipeline", config.redis_url, state="processing", progress=30,
                    progress_msg=f"Extracted {claim_ok} claims")

                cycle_claims = claim_ok

                enrich_count = 0
                enrich_err = 0
                for cid in claim_ids:
                    try:
                        claim = await store.get_claim(cid)
                        if not claim:
                            continue
                        text = claim["canonical_text"]
                        trefs = extract_temporal_references(text)
                        unc = detect_uncertainty(text)
                        ok = await store.enrich_claim(cid, trefs, unc["score"], unc["signals"])
                        if ok:
                            enrich_count += 1
                    except Exception as ex:
                        enrich_err += 1
                if enrich_count or enrich_err:
                    _log(f"Enriched {enrich_count} claims ({enrich_err} errors)")
                await _update_daemon_status("pipeline", config.redis_url, state="processing", progress=45,
                    progress_msg=f"Enriched {enrich_count} claims")

                unembedded = await store.get_claims_without_embeddings()
                cycle_embs = 0
                if unembedded:
                    _log(f"Generating {len(unembedded)} embeddings...")
                    try:
                        texts = [c["canonical_text"] for c in unembedded]
                        embs = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(None, generate_batch_embeddings, texts),
                            timeout=300
                        )
                        batch = [(c["claim_id"], emb, "all-MiniLM-L6-v2") for c, emb in zip(unembedded, embs)]
                        await store.store_embeddings_batch(batch)
                        cycle_embs = len(batch)
                        _log(f"Generated {cycle_embs} embeddings")
                        await _update_daemon_status("pipeline", config.redis_url, state="processing", progress=60,
                            progress_msg=f"Embeddings: {cycle_embs}")
                    except asyncio.TimeoutError:
                        _log("Embedding generation timed out after 300s — skipping")
                    except Exception as ex:
                        _log(f"Embedding generation failed: {ex}")

                cycle_rels = ""
                try:
                    rel_results = await asyncio.wait_for(
                        store.detect_all_relationships(),
                        timeout=600
                    )
                    parts = [f"{k}={v}" for k, v in rel_results.items() if v]
                    if parts:
                        cycle_rels = ", ".join(parts)
                        _log(f"Relationships: {cycle_rels}")
                    await _update_daemon_status("pipeline", config.redis_url, state="processing", progress=80,
                        progress_msg="Relationships done")
                except asyncio.TimeoutError:
                    _log("Relationship detection timed out after 600s — skipping")
                except Exception as ex:
                    _log(f"Relationship detection failed: {ex}")

            except Exception as pe:
                _log(f"Post-processing block error: {pe}")

            try:
                await store.clean_noise_entities(dry_run=False)
                backfilled = await store.backfill_query_categories()
                if backfilled["entity"] or backfilled["claim_entity"]:
                    _log(f"Backfilled categories: entity={backfilled['entity']} claim_entity={backfilled['claim_entity']}")
                gen_count = await auto_generate_queries_from_claim_entities(store)
                if gen_count:
                    _log(f"Auto-generated {gen_count} queries")
            except Exception as ge:
                _log(f"Auto-generate queries failed: {ge}")

        _log(f"Cycle: {ingested} ingested, {skipped} skipped, {errors} errors")
        write_status(ingested, skipped, errors,
                     claims=cycle_claims, embeddings=cycle_embs,
                     relationships=cycle_rels)
        await _update_daemon_status("pipeline", config.redis_url,
            state="completed", last_result=f"ingested={ingested}, skipped={skipped}, errors={errors}",
            last_completed_at=datetime.now(timezone.utc).isoformat())
        await _publish_completion(CH_PIPELINE, {
            "status": "completed", "ingested": ingested,
            "skipped": skipped, "errors": errors,
        }, config.redis_url)
    finally:
        await store.close()


# ── Score Cycle ──────────────────────────────────────────────────────────────

async def _score_cycle(config_path: str | None, _log=None):
    """One cycle of contradiction detection → corrections → confidence."""
    from helioryn.config import AppConfig
    from helioryn.store import EventStore

    if _log is None:
        _log = _make_log_fn("score")[0]

    from helioryn.cache import CH_SCORER
    config = AppConfig.load(config_path)
    await _update_daemon_status("scorer", config.redis_url, state="processing")
    store = EventStore(config.database_url)
    await store.connect()
    try:
        # Only process sources with activity in last 2 hours
        lookback_hours = 2
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT co.source_id FROM claim_observation co "
                "WHERE co.observed_at > now() - make_interval(hours => $1) "
                "AND EXISTS (SELECT 1 FROM claim c WHERE c.source_id = co.source_id)",
                lookback_hours
            )
        source_ids = [r["source_id"] for r in rows]
        if not source_ids:
            _log("No sources with recent activity — running full verify only")
        else:
            _log(f"Scoring {len(source_ids)} sources with recent activity")

            n_contra = 0
            n_corr = 0
            for sid in source_ids:
                try:
                    n_contra += await store.detect_source_contradictions(sid)
                except Exception as ex:
                    _log(f"  detect_source_contradictions failed for {str(sid)[:8]}: {ex}")
                try:
                    n_corr += await store.detect_source_corrections(sid)
                except Exception as ex:
                    _log(f"  detect_source_corrections failed for {str(sid)[:8]}: {ex}")
            if n_contra:
                _log(f"Source self-contradictions: {n_contra}")
            if n_corr:
                _log(f"Source corrections: {n_corr}")
            await _update_daemon_status("scorer", config.redis_url, state="processing", progress=30,
                progress_msg="Contradictions & corrections done")

            n_rel = 0
            for sid in source_ids:
                try:
                    await store.compute_source_reliability(sid)
                    n_rel += 1
                except Exception as ex:
                    _log(f"  compute_source_reliability failed for {str(sid)[:8]}: {ex}")
            _log(f"Source reliability computed for {n_rel} sources")
            await _update_daemon_status("scorer", config.redis_url, state="processing", progress=45,
                progress_msg="Source reliability done")

        try:
            result = await store.bulk_compute_confidence(skip_source_reliability=True)
            _log(f"Confidence factors computed: all 5 types")
        except Exception as ex:
            _log(f"  bulk_compute_confidence failed: {ex}")
        await _update_daemon_status("scorer", config.redis_url, state="processing", progress=65,
            progress_msg="Confidence factors done")

        # External consistency factors from grounding
        try:
            n_ext = await store.compute_external_consistency_factor()
            if n_ext:
                _log(f"External consistency factors: {n_ext}")
        except Exception as ex:
            _log(f"  external_consistency failed: {ex}")
        await _update_daemon_status("scorer", config.redis_url, state="processing", progress=75,
            progress_msg="External consistency done")

        try:
            async with store._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT c.claim_id FROM claim c "
                    "WHERE c.canonical_id IS NULL "
                    "AND EXISTS (SELECT 1 FROM claim_embedding ce WHERE ce.claim_id = c.claim_id) "
                    "LIMIT 5000"
                )
            if rows:
                n_canon = 0
                for r in rows:
                    try:
                        await store.assign_canonical_claim(r["claim_id"])
                        n_canon += 1
                    except Exception as ex:
                        pass
                _log(f"Assigned {n_canon} claims to canonical groups")
        except Exception as ex:
            _log(f"  canonical claim assignment failed: {ex}")
        await _update_daemon_status("scorer", config.redis_url, state="processing", progress=90,
            progress_msg="Canonical groups done")

        from helioryn.verify import verify_pipeline
        vresults = await verify_pipeline(store)
        if vresults:
            for vname, vchk in vresults.items():
                if not vchk["pass"]:
                    _log(f"VERIFY FAIL: {vname} — {vchk['detail']}")
                else:
                    _log(f"VERIFY PASS: {vname}")
        await _update_daemon_status("scorer", config.redis_url, state="completed", last_result="scoring completed",
            last_completed_at=datetime.now(timezone.utc).isoformat())
        await _publish_completion(CH_SCORER, {"status": "completed"}, config.redis_url)
    finally:
        from helioryn.log import write_status
        write_status(source="scorer")
        await store.close()


# ── Analyze Cycle (stub) ─────────────────────────────────────────────────────

async def _analyze_cycle(config_path: str | None, _log=None):
    """One analysis cycle (narrative evolution, mutation tracking, materialized views)."""
    if _log is None:
        _log = _make_log_fn("analyze")[0]
    from helioryn.cache import CH_ANALYZER
    from helioryn.config import AppConfig
    from helioryn.store import EventStore

    import asyncpg

    async def _run_step(name: str, fn, status_pct: int, status_msg: str, max_retries: int = 3):
        """Run an analyzer step with deadlock retry. Returns fn() result or None."""
        for attempt in range(1, max_retries + 1):
            try:
                result = await fn()
                _log(f"{name} completed")
                return result
            except asyncpg.exceptions.DeadlockDetectedError as ex:
                _log(f"  {name} deadlock (attempt {attempt}/{max_retries}): {ex}")
                if attempt < max_retries:
                    await asyncio.sleep(attempt * 2.0)
                else:
                    _log(f"  {name} failed after {max_retries} retries")
                    raise
            except asyncpg.exceptions.LockNotAvailableError as ex:
                _log(f"  {name} lock timeout (attempt {attempt}/{max_retries}): {ex}")
                if attempt < max_retries:
                    await asyncio.sleep(attempt * 1.0)
                else:
                    _log(f"  {name} gave up after {max_retries} attempts")
                    return None
            except Exception as ex:
                if "deadlock" in str(ex).lower() and attempt < max_retries:
                    _log(f"  {name} possible deadlock (attempt {attempt}/{max_retries}): {ex}")
                    await asyncio.sleep(attempt * 2.0)
                else:
                    _log(f"  {name} failed: {ex}")
                    return None

    config = AppConfig.load(config_path)
    await _update_daemon_status("analyzer", config.redis_url, state="processing")
    store = EventStore(config.database_url)
    await store.connect()
    try:
        await _run_step("evidence_density",
            lambda: store.refresh_evidence_density(), 10, "Evidence density done")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=10,
            progress_msg="Evidence density done")

        await _run_step("narrative_stability",
            lambda: _rel_narrative_stability(None, config_path), 30, "Narrative stability done")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=30,
            progress_msg="Narrative stability done")

        await _run_step("correlations",
            lambda: _rel_correlations(config_path), 40, "Correlations done")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=40,
            progress_msg="Correlations done")

        await _run_step("mutations",
            lambda: store.detect_cross_claim_mutations(limit=50), 55, "Mutation detection done")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=55,
            progress_msg="Mutation detection done")

        await _run_step("narrative_dynamics",
            lambda: _rel_populate_narrative_dynamics(None, config_path), 70, "Narrative dynamics done")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=70,
            progress_msg="Narrative dynamics done")

        na = await _run_step("narrative_assignment",
            lambda: store.assign_new_claims_to_narratives(), 85, "Narrative assignment done")
        if na:
            _log(f"Incremental narrative assignment: {na} claims")
        await _update_daemon_status("analyzer", config.redis_url, state="processing", progress=85,
            progress_msg="Narrative assignment done")

        drifted = await _run_step("semantic_drift",
            lambda: store.detect_all_drift(threshold=0.15), 95, "Semantic drift done")
        if drifted:
            flagged = [d for d in drifted if d["drift"] >= 0.15]
            if flagged:
                _log(f"Semantic drift: {len(flagged)}/{len(drifted)} canonical claims above threshold")

        _log("Analysis cycle complete")
        await _update_daemon_status("analyzer", config.redis_url, ttl=21600, state="completed", last_result="analysis complete",
            last_completed_at=datetime.now(timezone.utc).isoformat())
        await _publish_completion(CH_ANALYZER, {"status": "completed"}, config.redis_url)
    finally:
        from helioryn.log import write_status
        write_status(source="analyzer")
        await store.close()


# ── Interpret Engine ───────────────────────────────────────────────────────────

async def _interpret_cycle(config_path: str | None, _log=None, topics: list[str] | None = None):
    """Run one interpret cycle — produce intelligence briefs for all active topics."""
    from helioryn.config import AppConfig
    from helioryn.store import EventStore
    from helioryn.cache import CH_INTERPRETER
    from helioryn.constants import TOPICS

    config = AppConfig.load(config_path)
    await _update_daemon_status("interpreter", config.redis_url, state="processing")
    store = EventStore(config.database_url)
    await store.connect()
    log = _log or print
    try:
        topic_list = topics or list(TOPICS.keys())
        log(f"Interpret cycle: {len(topic_list)} topics")

        # Velocity alerts — check all narratives for rapid change
        alerts = await store.produce_velocity_alerts()
        for a in alerts:
            await store.store_interpretation(
                "velocity_alert", f"Velocity Alert: {a['narrative_name']}",
                a, topic=a["topic"], severity=a["severity"],
                narrative_ids=[UUID(a["narrative_id"])] if a.get("narrative_id") else None,
            )
        log(f"  Velocity alerts: {len(alerts)}")
        await _update_daemon_status("interpreter", config.redis_url, state="processing", progress=10,
            progress_msg=f"{len(alerts)} alerts")

        for idx, topic in enumerate(topic_list):
            brief = await store.produce_topic_brief(topic)
            if brief.get("total_claims", 0) == 0:
                continue
            log(f"  {topic}: {brief['total_claims']} claims, "
                f"{brief['total_narratives']} narratives, "
                f"{brief['contradictions']} contradictions")
            await _update_daemon_status("interpreter", config.redis_url, state="processing",
                progress=10 + int(70 * (idx + 1) / len(topic_list)),
                progress_msg=f"Topic {topic}")

        # Source intelligence for top topics by claim count
        top_topics = topic_list[:5]
        for topic in topic_list[:5]:
            try:
                await store.produce_source_intelligence_report(topic)
            except Exception:
                pass

        await _update_daemon_status("interpreter", config.redis_url,
            state="completed", last_result=f"{len(topic_list)} topics, {len(alerts)} alerts",
            last_completed_at=datetime.now(timezone.utc).isoformat())
        await _publish_completion(CH_INTERPRETER, {
            "status": "completed",
            "topics": len(topic_list),
            "alerts": len(alerts),
        }, config.redis_url)
    finally:
        from helioryn.log import write_status
        write_status(source="interpreter")
        await store.close()


# ── Pipeline ────────────────────────────────────────────────────────────────

@app.command("pipeline")
def pipeline_cmd(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run one pipeline cycle (discovery → extract → enrich → embed → relate)."""
    asyncio.run(_pipeline_cycle(config_path))


@app.command("pipeline-watch")
def pipeline_watch_cmd(
    interval: int = typer.Option(30, "--interval", "-i", help="Minutes between cycles"),
    max_queries: int = typer.Option(10, "--max-queries", "-q", help="Max queries per cycle"),
    results_per_query: int = typer.Option(5, "--results", "-r", help="Results per query"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run pipeline continuously on a schedule."""
    try:
        asyncio.run(_process_watch("pipeline", interval, config_path, _pipeline_cycle, max_queries=max_queries, results_per_query=results_per_query))
    except Exception:
        import traceback
        traceback.print_exc()


# ── Scorer ──────────────────────────────────────────────────────────────────

@app.command("score")
def score_cmd(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run one scoring cycle (contradictions → corrections → confidence)."""
    asyncio.run(_score_cycle(config_path))


@app.command("score-watch")
def score_watch_cmd(
    interval: int = typer.Option(60, "--interval", "-i", help="Minutes between cycles"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run scoring continuously on a schedule."""
    try:
        asyncio.run(_process_watch("score", interval, config_path, _score_cycle))
    except Exception:
        import traceback
        traceback.print_exc()


# ── Analyzer ────────────────────────────────────────────────────────────────

@app.command("analyze")
def analyze_cmd(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run one analysis cycle (narrative evolution, mutation tracking)."""
    asyncio.run(_analyze_cycle(config_path))


@app.command("analyze-watch")
def analyze_watch_cmd(
    interval: int = typer.Option(360, "--interval", "-i", help="Minutes between cycles"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run analysis continuously on a schedule."""
    try:
        asyncio.run(_process_watch("analyze", interval, config_path, _analyze_cycle))
    except Exception:
        import traceback
        traceback.print_exc()


@app.command("analyze-drift")
def analyze_drift_cmd(
    canonical_id: str | None = typer.Argument(None, help="Canonical claim ID (or blank for all)"),
    threshold: float = typer.Option(0.15, "--threshold", "-t", help="Drift threshold"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Detect semantic drift in canonical claims over time."""
    asyncio.run(_detect_drift(canonical_id, threshold, config_path))


# ── Interpret ────────────────────────────────────────────────────────────────

@app.command("interpret")
def interpret_cmd(
    topic: list[str] = typer.Option(None, "--topic", "-t", help="Topic(s) to interpret"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run one interpret cycle — produce intelligence briefs."""
    asyncio.run(_interpret_cycle(config_path, topics=topic))


@app.command("interpret-watch")
def interpret_watch_cmd(
    interval: int = typer.Option(60, "--interval", "-i", help="Minutes between cycles"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run interpret continuously on a schedule."""
    try:
        asyncio.run(_process_watch("interpret", interval, config_path, _interpret_cycle))
    except Exception:
        import traceback
        traceback.print_exc()


async def _detect_drift(canonical_id: str | None, threshold: float, config_path: str | None):
    from helioryn.config import AppConfig
    from helioryn.store import EventStore
    config = AppConfig.load(config_path)
    store = EventStore(config.database_url)
    await store.connect()
    try:
        if canonical_id:
            from uuid import UUID
            drift = await store.detect_semantic_drift(UUID(canonical_id), threshold)
            print(f"Canonical {canonical_id}: drift_score = {drift:.4f}")
        else:
            results = await store.detect_all_drift(threshold)
            flagged = [r for r in results if r["drift"] >= threshold]
            print(f"Checked {len(results)} canonical claims, {len(flagged)} above threshold {threshold}")
            for r in flagged:
                print(f"  Canonical {r['canonical_id'][:8]}: drift = {r['drift']:.4f}")
    finally:
        await store.close()


# ── Grounding ──────────────────────────────────────────────────────────────

@app.command("ground")
def ground_cmd(
    claim_id: str = typer.Argument(None, help="Claim UUID (omit to process all ungrounded)"),
    topic: str | None = typer.Option(None, "--topic", "-t", help="Topic to ground"),
    limit: int = typer.Option(500, "--limit", "-l", help="Max claims to ground"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Ground claims against Wikidata and external fact-checks."""
    asyncio.run(_ground_cycle(config_path, claim_id=claim_id, topic=topic, limit=limit))


@app.command("ground-watch")
def ground_watch_cmd(
    interval: int = typer.Option(120, "--interval", "-i", help="Minutes between cycles"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run grounding continuously on a schedule."""
    try:
        asyncio.run(_process_watch("ground", interval, config_path, _ground_cycle))
    except Exception:
        import traceback
        traceback.print_exc()


async def _ground_cycle(config_path: str | None, _log=None,
                         claim_id: str | None = None,
                         topic: str | None = None,
                         limit: int = 500):
    """Ground ungrounded claims — Wikidata entity resolution + Google FactCheck."""
    from helioryn.config import AppConfig
    from helioryn.store import EventStore
    from helioryn.cache import CH_GROUNDING
    from helioryn.grounding.wikidata import resolve_entity
    from helioryn.grounding.factcheck import search_google_factcheck
    from uuid import UUID

    config = AppConfig.load(config_path)
    await _update_daemon_status("ground", config.redis_url, state="processing")
    store = EventStore(config.database_url)
    await store.connect()
    log = _log or print

    google_key = config.grounding.factcheck_api_key if config.grounding.factcheck_enabled else ""
    google_today = await store.get_google_call_count_today()
    max_google = int((await store.get_setting("grounding_max_google_calls_per_day", "10000")))
    google_remaining = max(0, max_google - google_today)

    batch_size = int((await store.get_setting("grounding_google_batch_size", "500")))

    try:
        if claim_id:
            # Single claim mode
            cids = [UUID(claim_id)]
        else:
            cids = await store.get_ungrounded_claims(topic=topic, limit=limit)
            if not cids:
                log("All claims grounded")
                await _publish_completion(CH_GROUNDING, {"status": "completed", "grounded": 0}, config.redis_url)
                return

        log(f"Grounding {len(cids)} claims (Google remaining: {google_remaining})")
        wikidata_count = 0
        google_count = 0

        for i, cid in enumerate(cids):
            # Wikidata pass: resolve entities
            if config.grounding.wikidata_enabled:
                entities = await store.get_entities_for_claim(cid)
                for ent in entities:
                    name = ent.get("name", "")
                    if not name:
                        continue
                    result = await resolve_entity(name)
                    if result:
                        await store.store_grounding(
                            cid, "wikidata", result["qid"],
                            label=result["label"],
                            confidence=result["confidence"],
                        )
                        wikidata_count += 1

            # Google FactCheck pass
            if google_key and google_count < google_remaining:
                claim = await store.get_claim(cid)
                if claim:
                    text = claim.get("canonical_text", "")
                    if text:
                        checks = await search_google_factcheck(text, google_key)
                        for ch in checks:
                            await store.store_grounding(
                                cid, "google_factcheck", ch["review_url"],
                                label=ch["claim_text"],
                                rating=ch["rating"],
                            )
                            google_count += 1

            if (i + 1) % 100 == 0:
                pct = int(80 * (i + 1) / len(cids))
                await _update_daemon_status("ground", config.redis_url, state="processing", progress=pct,
                    progress_msg=f"Grounding {i+1}/{len(cids)}")
                log(f"  {i+1}/{len(cids)} claims processed")

        # Compute external consistency factors for grounded claims
        try:
            n_factors = await store.compute_external_consistency_factor()
            log(f"  External consistency factors: {n_factors}")
        except Exception as ex:
            log(f"  Factor computation failed: {ex}")

        log(f"  Wikidata: {wikidata_count}, Google: {google_count}")
        await _update_daemon_status("ground", config.redis_url,
            state="completed", last_result=f"claims={len(cids)}, wikidata={wikidata_count}, google={google_count}",
            last_completed_at=datetime.now(timezone.utc).isoformat())
        await _publish_completion(CH_GROUNDING, {
            "status": "completed",
            "claims": len(cids),
            "wikidata": wikidata_count,
            "google": google_count,
        }, config.redis_url)

    finally:
        from helioryn.log import write_status
        write_status(source="ground")
        await store.close()


# ── Unified Daemon ──────────────────────────────────────────────────────────

@app.command("daemon")
def daemon(
    interval: int = typer.Option(2, "--interval", "-i", help="Minutes between pipeline cycles"),
    max_queries: int = typer.Option(10, "--max-queries", "-q", help="Max queries per pipeline cycle"),
    results_per_query: int = typer.Option(5, "--results", "-r", help="Results per pipeline query"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
    no_pipeline: bool = typer.Option(False, "--no-pipeline", help="Do not run the pipeline"),
    no_scorer: bool = typer.Option(False, "--no-scorer", help="Do not run the scorer"),
    no_analyzer: bool = typer.Option(False, "--no-analyzer", help="Do not run the analyzer"),
    no_interpreter: bool = typer.Option(False, "--no-interpreter", help="Do not run the interpreter"),
    no_grounding: bool = typer.Option(False, "--no-grounding", help="Do not run the grounding cycle"),
):
    """Run all subprocesses as managed daemons."""
    asyncio.run(_daemon_manager(interval, max_queries, results_per_query, config_path, no_pipeline, no_scorer, no_analyzer, no_interpreter, no_grounding))


async def _daemon_manager(
    interval: int, max_queries: int, results_per_query: int,
    config_path: str | None,
    no_pipeline: bool, no_scorer: bool, no_analyzer: bool, no_interpreter: bool,
    no_grounding: bool = False,
):
    from helioryn.log import write_pid, clear_pid
    import os as _os
    import signal as _signal
    import subprocess as _sp
    import sys as _sys

    global _shutdown_event
    _shutdown_event = asyncio.Event()

    write_pid()
    pid = _os.getpid()
    typer.echo(f"Helioryn daemon started (PID {pid})")

    _signal.signal(_signal.SIGTERM, _signal_handler)
    _signal.signal(_signal.SIGINT, _signal_handler)

    cli = _os.path.join(_os.path.dirname(_sys.executable), "helioryn")
    config_args = ["-c", config_path] if config_path else []

    procs: dict[str, _sp.Popen] = {}
    try:
        if not no_pipeline:
            procs["pipeline"] = _sp.Popen(
                [cli, "pipeline-watch", "--interval", str(interval),
                 "--max-queries", str(max_queries),
                 "--results", str(results_per_query),
                 *config_args],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            typer.echo(f"  pipeline (PID {procs['pipeline'].pid})")
        if not no_scorer:
            score_interval = max(interval * 2, 60)
            procs["scorer"] = _sp.Popen(
                [cli, "score-watch", "--interval", str(score_interval), *config_args],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            typer.echo(f"  scorer (PID {procs['scorer'].pid})")
        if not no_analyzer:
            analyze_interval = max(interval * 12, 360)
            procs["analyzer"] = _sp.Popen(
                [cli, "analyze-watch", "--interval", str(analyze_interval), *config_args],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            typer.echo(f"  analyzer (PID {procs['analyzer'].pid})")
        if not no_interpreter:
            interpret_interval = max(interval * 2, 60)
            procs["interpreter"] = _sp.Popen(
                [cli, "interpret-watch", "--interval", str(interpret_interval), *config_args],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            typer.echo(f"  interpreter (PID {procs['interpreter'].pid})")
        if not no_grounding:
            ground_interval = max(interval * 4, 120)
            procs["ground"] = _sp.Popen(
                [cli, "ground-watch", "--interval", str(ground_interval), *config_args],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            typer.echo(f"  ground (PID {procs['ground'].pid})")

        while not _shutdown_event.is_set():
            for name, proc in list(procs.items()):
                if proc.poll() is not None:
                    typer.echo(f"{name} died (PID {proc.pid}, code {proc.returncode}) — restarting")
                    procs[name] = _sp.Popen(
                        proc.args, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    )
                    typer.echo(f"  {name} restarted (PID {procs[name].pid})")
            try:
                await asyncio.wait_for(_shutdown_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
    finally:
        for name, proc in procs.items():
            proc.terminate()
        for name, proc in procs.items():
            try:
                proc.wait(timeout=5)
            except _sp.TimeoutExpired:
                proc.kill()
        clear_pid()
        typer.echo("Daemon stopped.")


@app.command()
def housekeeping(
    prune_days: int = typer.Option(90, "--prune-days", "-d", help="Delete events older than N days"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run housekeeping: clean up disk space, vacuum DB, rotate logs."""
    asyncio.run(_run_housekeeping(prune_days, config_path))


async def _run_housekeeping(prune_days: int, config_path: str | None):
    from helioryn.housekeeping import (
        cleanup_pg_temp, disk_usage, rotate_all_logs, vacuum_db, prune_old_events,
    )
    from helioryn.config import AppConfig
    from helioryn.store import EventStore

    disk = disk_usage()
    typer.echo(f"Disk: {disk['free_gb']} GB free ({disk['pct_free']}%)")

    pg_freed = cleanup_pg_temp()
    if pg_freed:
        typer.echo(f"PG temp: freed {pg_freed // (1024*1024)} MB")

    logs = rotate_all_logs()
    if logs:
        for l in logs:
            typer.echo(f"Log rotated: {l['path']} ({l['freed_bytes'] // (1024*1024)} MB)")
    else:
        typer.echo("Logs: all under 50 MB, no rotation needed")

    config = AppConfig.load(config_path)
    store = EventStore(config.database_url)
    await store.connect()
    try:
        deleted = await prune_old_events(store, prune_days)
        typer.echo(f"Pruned {deleted} events older than {prune_days} days")

        vac = await vacuum_db(store)
        typer.echo(f"Vacuum: done")
    finally:
        await store.close()

    disk2 = disk_usage()
    typer.echo(f"After cleanup: {disk2['free_gb']} GB free ({disk2['pct_free']}%)")


# --- Dashboard --- #


@app.command()
def dashboard(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Launch the terminal dashboard."""
    from helioryn.dashboard import run_dashboard
    run_dashboard(config_path=config_path)


# --- Serve --- #


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address"),
    port: int = typer.Option(8765, "--port", "-p", help="Port"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Start the Helioryn API server."""
    import uvicorn

    if not config_path:
        from pathlib import Path as _Path
        candidates = [
            "helioryn/helioryn.toml",
            str(_Path.home() / ".helioryn" / "helioryn.toml"),
        ]
        for c in candidates:
            if _Path(c).exists():
                config_path = c
                break

    from helioryn import server as api_server

    api_server._CONFIG_PATH = config_path
    key = api_server._get_api_key()
    typer.echo(f"Helioryn API server starting...")
    typer.echo(f"API Key:  {key}")
    typer.echo(f"Listening on http://{host}:{port}")
    typer.echo(f"Docs at   http://{host}:{port}/docs")
    uvicorn.run("helioryn.server:app", host=host, port=port, log_level="info")


# --- Extract --- #


@extract_app.command("source")
def extract_source(
    source_id: str = typer.Argument(..., help="Source UUID to extract claims from"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Extract claims from a single source."""
    asyncio.run(_extract_source(UUID(source_id), config_path))


async def _extract_source(source_id: UUID, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    snapshot = await store.get_snapshot(source_id)
    if not snapshot:
        typer.echo(f"Source not found: {source_id}", err=True)
        await store.close()
        raise typer.Exit(1)

    from helioryn.constants import TOPIC_BY_CATEGORY
    from helioryn.extract import extract_claims
    from helioryn.models import Observation
    category = (snapshot.metadata or {}).get("query_category", "")
    topic = TOPIC_BY_CATEGORY.get(category, "") if category else ""
    claims = extract_claims(snapshot)

    for claim in claims:
        entities = extract_entities(claim.canonical_text)
        claim.entities = entities

    count = 0
    for claim in claims:
        await store.insert_claim(claim, topic=topic or None)
        await _link_entities(store, claim, claim.entities)
        obs = Observation(claim_id=claim.claim_id, source_id=source_id, observer="helioryn-ingest", context=claim.context_sentence)
        await store.insert_observation(obs)
        count += 1

    typer.echo(f"Extracted {count} claims from {snapshot.source_url}")
    await store.close()


@extract_app.command("all")
def extract_all(
    limit: int = typer.Option(50, "--limit", "-l", help="Max sources to process"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Extract claims from all unprocessed sources."""
    asyncio.run(_extract_all(limit, config_path))


async def _extract_all(limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    from helioryn.extract import extract_claims
    from helioryn.models import Observation

    sql = """
    SELECT ss.* FROM source_snapshot ss
    LEFT JOIN claim c ON c.source_id = ss.source_id
    WHERE c.claim_id IS NULL
    LIMIT $1
    """
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql, limit)

    if not rows:
        typer.echo("All sources already have claims extracted.")
        await store.close()
        return

    total_claims = 0
    for row in rows:
        snapshot = SourceSnapshot(**store._row_to_dict(row))
        claims = extract_claims(snapshot)
        for claim in claims:
            claim.entities = extract_entities(claim.canonical_text)
            await store.insert_claim(claim)
            await _link_entities(store, claim, claim.entities)
            obs = Observation(claim_id=claim.claim_id, source_id=snapshot.source_id, observer="helioryn-ingest", context=claim.context_sentence)
            await store.insert_observation(obs)
            total_claims += 1
        title = snapshot.title or snapshot.source_url[:40]
        typer.echo(f"  {len(claims)} claims from {title}")

    typer.echo(f"Total: {total_claims} claims extracted from {len(rows)} sources")
    await store.close()


# --- Discovery --- #


@discover_app.command("seed")
def discover_seed(
    config_path: str | None = typer.Option(None, "--config", "-c"),
    seed_file: str = typer.Option("data/governments.json", "--seed-file"),
):
    """Seed government entities and generate queries."""
    asyncio.run(_discover_seed(config_path, seed_file))


async def _discover_seed(config_path: str | None, seed_file: str):
    from pathlib import Path
    from helioryn.discovery.entity_db import load_seed_entities, generate_queries_from_entities

    config = AppConfig.load(config_path)
    store = await _get_store(config)
    seed_path = Path(seed_file)
    if not seed_path.is_absolute():
        seed_path = Path(__file__).parent.parent.parent / seed_file

    if not seed_path.exists():
        typer.echo(f"Seed file not found: {seed_path}")
        await store.close()
        raise typer.Exit(1)

    entities = await load_seed_entities(store, str(seed_path))
    queries = await generate_queries_from_entities(store)

    query_count = await store.get_query_count()
    entity_count = await store.get_entity_count()

    typer.echo(f"Entities: {entity_count} ({entities} new)")
    typer.echo(f"Queries:  {query_count}")
    await store.close()


@discover_app.command("seed-companies")
def seed_companies(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Seed AI company entities and generate queries."""
    asyncio.run(_seed_non_gov("companies.json", "company", config_path))


@discover_app.command("seed-researchers")
def seed_researchers(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Seed AI researcher entities and generate queries."""
    asyncio.run(_seed_non_gov("researchers.json", "person", config_path))


@discover_app.command("seed-investors")
def seed_investors(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Seed AI investor entities and generate queries."""
    asyncio.run(_seed_non_gov("investors.json", "investor", config_path))


async def _seed_non_gov(filename: str, entity_type: str, config_path: str | None):
    from pathlib import Path
    from helioryn.discovery.entity_db import load_seed_entities, generate_queries_from_entities

    config = AppConfig.load(config_path)
    store = await _get_store(config)
    seed_path = Path(__file__).parent.parent.parent / "data" / filename

    if not seed_path.exists():
        typer.echo(f"Seed file not found: {seed_path}")
        await store.close()
        raise typer.Exit(1)

    entities = await load_seed_entities(store, str(seed_path), entity_type=entity_type)
    queries = await generate_queries_from_entities(store)

    query_count = await store.get_query_count()
    entity_count = await store.get_entity_count()

    typer.echo(f"Entities: {entity_count} ({entities} new {entity_type})")
    typer.echo(f"Queries:  {query_count}")
    await store.close()


@discover_app.command("run")
def discover_run(
    config_path: str | None = typer.Option(None, "--config", "-c"),
    aggressive: bool = typer.Option(False, "--aggressive", "-a", help="Process up to 30 queries × 5 results"),
):
    """Run one discovery cycle."""
    asyncio.run(_discover_run(config_path, aggressive=aggressive))


async def _discover_run(config_path: str | None, aggressive: bool = False):
    from helioryn.discovery import run_discovery_cycle

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    def progress(msg: str):
        typer.echo(msg)

    ingested, skipped, errors = await run_discovery_cycle(
        config, store,
        max_queries=30 if aggressive else 10,
        results_per_query=5 if aggressive else 3,
        progress_callback=progress,
    )
    typer.echo(f"Discovery cycle: {ingested} ingested, {skipped} skipped, {errors} errors")

    # Extract claims from new sources
    from helioryn.extract import extract_claims
    from helioryn.models import Observation
    sql = """
    SELECT ss.* FROM source_snapshot ss
    LEFT JOIN claim c ON c.source_id = ss.source_id
    WHERE c.claim_id IS NULL
    """
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql)

    for row in rows:
        snapshot = SourceSnapshot(**store._row_to_dict(row))
        claims = extract_claims(snapshot)
        for claim in claims:
            claim.entities = extract_entities(claim.canonical_text)
            await store.insert_claim(claim)
            await _link_entities(store, claim, claim.entities)
            obs = Observation(claim_id=claim.claim_id, source_id=snapshot.source_id, observer="helioryn-ingest", context=claim.context_sentence)
            await store.insert_observation(obs)

    typer.echo(f"Extracted claims from {len(rows)} new sources")
    await store.close()


@discover_app.command("watch")
def discover_watch(
    interval: int = typer.Option(60, "--interval", "-i", help="Minutes between cycles"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run discovery continuously."""
    import helioryn.log as log
    import schedule
    import time

    log.write_pid()

    def cycle():
        asyncio.run(_discover_run(config_path))

    typer.echo(f"Discovery every {interval} minutes. Ctrl+C to stop.")
    schedule.every(interval).minutes.do(cycle)
    cycle()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
    finally:
        log.clear_pid()


# --- Query commands --- #


@query_app.command("list")
def query_list(
    limit: int = typer.Option(30, "--limit", "-l"),
    by_track: bool = typer.Option(False, "--by-track", help="Group curated queries by track"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List active search queries."""
    asyncio.run(_query_list(limit, by_track, config_path))


async def _query_list(limit: int, by_track: bool, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if by_track:
        # List curated queries from config grouped by track
        topics = config.ingest.topics
        if not topics:
            typer.echo("No curated queries in config.")
        else:
            tracks: dict[str, list] = {}
            for q in topics:
                cat = q.category or "Uncategorized"
                tracks.setdefault(cat, []).append(q)
            for cat in sorted(tracks.keys()):
                typer.echo(f"\n  [{cat}] ({len(tracks[cat])} queries)")
                for q in tracks[cat]:
                    typer.echo(f"    {q.query[:58]}  every {q.interval_minutes}min")
            typer.echo()
    else:
        queries = await store.list_queries(limit=limit)
        if not queries:
            typer.echo("No queries. Run 'discover seed' first.")
        else:
            for q in queries:
                last = (q.get("last_run") or "").strftime("%m-%d %H:%M") if q.get("last_run") else "never"
                prio = q.get("priority", 50)
                text = q.get("text", "")[:60]
                typer.echo(f"[p{prio:02d}] {last}  {text}")
    await store.close()


@query_app.command("add")
def query_add(
    text: str = typer.Argument(..., help="Search query text"),
    priority: int = typer.Option(50, "--priority", "-p"),
    source: str = typer.Option("human", "--source"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Add a manual search query."""
    asyncio.run(_query_add(text, priority, source, config_path))


async def _query_add(text: str, priority: int, source: str, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    result = await store.upsert_query(text=text, source=source, priority=priority)
    typer.echo(f"Added query: {text}")
    await store.close()


@query_app.command("validate")
def query_validate(
    config_path: str | None = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Test queries without storing results"),
    track: str | None = typer.Option(None, "--track", "-t", help="Filter by track/category"),
):
    """Validate curated search queries against SearXNG."""
    asyncio.run(_query_validate(config_path, dry_run, track))


async def _query_validate(config_path: str | None, dry_run: bool, track: str | None):
    import math

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    # Load curated queries from config
    all_queries = config.ingest.topics

    if not all_queries:
        typer.echo("No curated queries found in config.")
        await store.close()
        return

    # Filter by track if specified
    if track:
        config_queries = [q for q in all_queries if q.category.lower() == track.lower()]
        if not config_queries:
            tracks = sorted({q.category for q in all_queries if q.category})
            typer.echo(f"No queries found for track '{track}'. Available tracks: {', '.join(tracks)}")
            await store.close()
            return
    else:
        config_queries = all_queries

    # Group by track/category
    tracks: dict[str, list] = {}
    for q in config_queries:
        cat = q.category or "Uncategorized"
        tracks.setdefault(cat, []).append(q)

    # Display by track
    typer.echo(f"\n=== Query Tracks ({len(config_queries)} queries, {len(tracks)} tracks) ===\n")
    for cat in sorted(tracks.keys()):
        items = tracks[cat]
        typer.echo(f"  [{cat}] ({len(items)} queries)")
        for q in items:
            typer.echo(f"    {q.query[:58]:<58}  every {q.interval_minutes}min")
        # Coverage gap: track with < 3 queries
        if len(items) < 3:
            typer.echo(f"    ⚠  Coverage gap: only {len(items)} queries — consider adding more")
        typer.echo()

    # Query similarity check (duplicate detection)
    typer.echo(f"=== Similarity Check ===\n")
    all_texts = [q.query.lower() for q in config_queries]
    duplicates_found = False
    threshold = 0.7
    for i in range(len(all_texts)):
        for j in range(i + 1, len(all_texts)):
            # Simple word-overlap similarity for short query texts
            words_i = set(all_texts[i].split())
            words_j = set(all_texts[j].split())
            overlap = len(words_i & words_j) / max(len(words_i | words_j), 1)
            if overlap > threshold:
                typer.echo(f"  High overlap ({overlap:.0%}): \"{config_queries[i].query}\" <-> \"{config_queries[j].query}\"")
                duplicates_found = True
    if not duplicates_found:
        typer.echo("  No significant query overlap detected.")

    # Test queries against SearXNG (only if not dry_run)
    if not dry_run:
        searcher = create_searcher(config)
        typer.echo(f"\n=== Live Test (SearXNG) ===\n")
        zero_result_queries = []
        for cat in sorted(tracks.keys()):
            items = tracks[cat]
            typer.echo(f"  [{cat}]")
            for q in items:
                try:
                    results = await searcher.search(q.query, limit=5, pages=1)
                    count = len(results) if results else 0
                    if count == 0:
                        zero_result_queries.append(q)
                    typer.echo(f"    {count:>3d}  {q.query[:62]}")
                except Exception as e:
                    typer.echo(f"    FAIL  {q.query[:62]}  ({e})")
                    zero_result_queries.append(q)
            typer.echo()
        if zero_result_queries:
            typer.echo(f"  WARNING: {len(zero_result_queries)} queries returned 0 results:")
            for z in zero_result_queries:
                typer.echo(f"    [{z.category}] {z.query}")
        else:
            typer.echo("  All queries returned results.")

    # Summary
    tracks_with_gaps = [cat for cat, items in tracks.items() if len(items) < 3]
    if tracks_with_gaps:
        typer.echo(f"\n=== Coverage Gaps ===\n")
        for cat in tracks_with_gaps:
            typer.echo(f"  [{cat}] only {len(tracks[cat])} queries — low coverage")

    await store.close()
    typer.echo("\nValidation complete.")


# --- Entity search (extracted claim entities) --- #


@app.command()
def entity_search(
    query: str = typer.Argument(..., help="Search extracted entities"),
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Search entities extracted from claims."""
    asyncio.run(_entity_search(query, limit, config_path))


async def _entity_search(query: str, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    entities = await store.search_claim_entities(query, limit=limit)
    if not entities:
        typer.echo(f"No entities matching '{query}'")
    else:
        typer.echo(f"Entities matching '{query}':")
        for e in entities:
            name = e.get("name", "")[:40]
            etype = e.get("entity_type", "")
            count = e.get("claim_count", 0)
            typer.echo(f"  [{etype:<12}] {name:<40} ({count} claims)")
    await store.close()


@app.command()
def entity_list_all(
    limit: int = typer.Option(30, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List all extracted entities, ranked by claim count."""
    asyncio.run(_entity_list_all(limit, config_path))


async def _entity_list_all(limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    entities = await store.list_claim_entities(limit=limit)
    if not entities:
        typer.echo("No entities extracted yet. Run 'extract all' first.")
    else:
        typer.echo(f"Top {limit} entities (by claim count):")
        for e in entities:
            name = e.get("name", "")[:40]
            etype = e.get("entity_type", "")
            count = e.get("claim_count", 0)
            typer.echo(f"  [{etype:<12}] {name:<40} ({count} claims)")
    await store.close()


# --- Entity commands (government entities from discovery engine) --- #


@entity_app.command("list")
def entity_list(
    level: str | None = typer.Option(None, "--level", help="Filter by level (country, state, city, etc)"),
    limit: int = typer.Option(30, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List government entities."""
    asyncio.run(_entity_list(level, limit, config_path))


async def _entity_list(level: str | None, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    entities = await store.list_entities(level=level, limit=limit)

    if not entities:
        typer.echo("No entities. Run 'discover seed' first.")
    else:
        for e in entities:
            etype = e.get("entity_type", "?")
            name = e.get("name", "")[:40]
            country = e.get("country", "")
            typer.echo(f"[{etype:<15}] {name:<40} {country}")
    await store.close()


# --- Observations --- #


@app.command()
def observations(
    claim_id: str | None = typer.Option(None, "--claim", "-c", help="Filter by claim UUID"),
    source_id: str | None = typer.Option(None, "--source", "-s", help="Filter by source UUID"),
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List claim observations."""
    asyncio.run(_observations(claim_id, source_id, limit, config_path))


async def _observations(claim_id: str | None, source_id: str | None, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if claim_id:
        obs = await store.get_observations_for_claim(UUID(claim_id), limit=limit)
        label = f"claim {claim_id[:8]}"
    elif source_id:
        obs = await store.get_observations_for_source(UUID(source_id), limit=limit)
        label = f"source {source_id[:8]}"
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT observation_id, claim_id, source_id, observed_at, observer "
                "FROM claim_observation ORDER BY observed_at DESC LIMIT $1", limit
            )
            obs = [dict(r) for r in rows]
        label = "all"

    if not obs:
        typer.echo("No observations found.")
    else:
        typer.echo(f"Observations ({label}):")
        for o in obs:
            ts = str(o.get("observed_at", ""))[11:19]
            cid = str(o.get("claim_id", ""))[:8]
            sid = str(o.get("source_id", ""))[:8]
            obsr = o.get("observer", "")
            typer.echo(f"  {ts}  claim={cid}  source={sid}  observer={obsr}")
    await store.close()


# --- Claims --- #


@extract_app.command("list")
def list_claims(
    source_id: str | None = typer.Option(None, "--source", "-s", help="Filter by source UUID"),
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List extracted claims."""
    asyncio.run(_list_claims(source_id, limit, config_path))


async def _list_claims(source_id: str | None, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if source_id:
        claims = await store.get_claims_for_source(UUID(source_id), limit=limit)
        title = source_id[:8]
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT claim_id, source_id, canonical_text, extraction_confidence, claim_type "
                "FROM claim ORDER BY extracted_at DESC LIMIT $1", limit
            )
            claims = [dict(r) for r in rows]
        title = "all sources"

    if not claims:
        typer.echo("No claims found.")
    else:
        typer.echo(f"Claims ({title}):")
        for c in claims:
            text = c.get("canonical_text", "")[:80]
            sid = str(c.get("source_id", ""))[:8]
            conf = c.get("extraction_confidence", 0)
            typer.echo(f"  [{sid}] ({conf}) {text}")
    await store.close()


# --- Relationship Graph (Layer 3b/3c) ---


@rel_app.command("embed")
def rel_embed(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Generate embeddings for all claims without them."""
    asyncio.run(_rel_embed(config_path))


async def _rel_embed(config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    claims = await store.get_claims_without_embeddings()
    if not claims:
        typer.echo("All claims already have embeddings.")
        await store.close()
        return

    typer.echo(f"Generating embeddings for {len(claims)} claims...")
    from helioryn.embed import generate_batch_embeddings

    texts = [c["canonical_text"] for c in claims]
    embs = generate_batch_embeddings(texts)

    batch = []
    for c, emb in zip(claims, embs):
        batch.append((c["claim_id"], emb, "all-MiniLM-L6-v2"))

    await store.store_embeddings_batch(batch)
    typer.echo(f"Stored {len(batch)} embeddings.")
    await store.close()


@rel_app.command("contradictions")
def rel_contradictions(
    sim_threshold: float = typer.Option(0.75, "--sim", "-s", help="Min embedding similarity between claims"),
    sim_max: float = typer.Option(0.80, "--sim-max", help="Max embedding similarity (avoid same-text matches)"),
    numeric_diff: float = typer.Option(0.15, "--diff", "-d", help="Relative numeric difference threshold"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Detect contradictions: claims sharing entities + high similarity + conflicting numbers."""
    asyncio.run(_rel_contradictions(sim_threshold, sim_max, numeric_diff, config_path))


async def _rel_contradictions(sim_threshold: float, sim_max: float, numeric_diff: float, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    count = await store.detect_contradictions(sim_threshold=sim_threshold, sim_max=sim_max, numeric_diff=numeric_diff)
    typer.echo(f"Created {count} contradicts relationships.")
    await store.close()


@rel_app.command("clear-contradictions")
def rel_clear_contradictions(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Delete all existing contradicts relationships (to re-run with cleaner rules)."""
    asyncio.run(_rel_clear_contradictions(config_path))


async def _rel_clear_contradictions(config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    async with store._pool.acquire() as conn:
        result = await conn.execute("DELETE FROM claim_relationship WHERE relationship_type = 'contradicts'")
        typer.echo(f"Cleared {result.split()[-1]} contradicts relationships.")
    await store.close()


@rel_app.command("detect")
def rel_detect(
    threshold: float = typer.Option(0.88, "--threshold", "-t", help="Cosine similarity threshold"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Detect same claims across sources using embedding similarity."""
    asyncio.run(_rel_detect(threshold, config_path))


async def _rel_detect(threshold: float, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    count = await store.detect_same_claims(threshold=threshold)
    typer.echo(f"Created {count} repeated_by relationships (threshold={threshold}).")
    await store.close()


@rel_app.command("list")
def rel_list(
    claim_id: str | None = typer.Option(None, "--claim", "-c", help="Filter by claim UUID"),
    limit: int = typer.Option(30, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List claim relationships."""
    asyncio.run(_rel_list(claim_id, limit, config_path))


async def _rel_list(claim_id: str | None, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if claim_id:
        rels = await store.get_relationships_for_claim(UUID(claim_id), limit=limit)
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT r.*, src.canonical_text AS source_text, "
                "tgt.canonical_text AS target_text "
                "FROM claim_relationship r "
                "JOIN claim src ON src.claim_id = r.source_claim_id "
                "JOIN claim tgt ON tgt.claim_id = r.target_claim_id "
                "ORDER BY r.confidence DESC LIMIT $1", limit
            )
            rels = [dict(r) for r in rows]

    if not rels:
        typer.echo("No relationships found.")
    else:
        typer.echo(f"Relationships ({len(rels)}):")
        for r in rels:
            rtype = r.get("relationship_type", "")
            conf = r.get("confidence", 0)
            src = str(r.get("source_claim_id", ""))[:8]
            tgt = str(r.get("target_claim_id", ""))[:8]
            src_text = (r.get("source_text") or "")[:60]
            tgt_text = (r.get("target_text") or "")[:60]
            typer.echo(f"  [{rtype}] {src} -> {tgt} (conf={conf:.3f})")
            typer.echo(f"    src: {src_text}")
            typer.echo(f"    tgt: {tgt_text}")
    await store.close()


@rel_app.command("narratives")
def rel_narratives(
    k: int = typer.Option(10, "--topics", "-k", help="Number of narrative topics"),
    min_claims: int = typer.Option(5, "--min", "-m", help="Minimum claims per narrative"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Detect narrative clusters via LDA topic modeling."""
    asyncio.run(_rel_narratives(k, min_claims, config_path))


async def _rel_narratives(k: int, min_claims: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    typer.echo(f"Running LDA topic modeling with k={k}...")
    count = await store.detect_narratives(k=k, min_claims=min_claims)
    typer.echo(f"Created {count} narratives.")
    await store.close()


@rel_app.command("narratives-list")
def rel_narratives_list(
    limit: int = typer.Option(10, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List detected narratives."""
    asyncio.run(_rel_narratives_list(limit, config_path))


async def _rel_narratives_list(limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    narratives = await store.list_narratives(limit=limit)
    if not narratives:
        typer.echo("No narratives found. Run 'rel narratives' to detect.")
    else:
        typer.echo(f"Narratives ({len(narratives)}):")
        for n in narratives:
            nid = str(n.get("narrative_id", ""))[:8]
            name = str(n.get("name", ""))[:50]
            terms = ", ".join(str(t) for t in (n.get("top_terms", [])[:4]))
            typer.echo(f"  [{nid}] {name} ({n.get('claim_count', 0)} claims)")
            typer.echo(f"    terms: {terms}")
    await store.close()


@rel_app.command("assign-narratives")
def rel_assign_narratives(
    threshold: float = typer.Option(0.50, "--threshold", "-t", help="Min similarity for assignment"),
    batch: int = typer.Option(5000, "--batch", "-b", help="Claims to process"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Assign unassigned claims to existing narratives via embedding centroid similarity."""
    asyncio.run(_rel_assign_narratives(threshold, batch, config_path))


async def _rel_assign_narratives(threshold: float, batch: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        n = await store.assign_new_claims_to_narratives(threshold=threshold, batch_size=batch)
        typer.echo(f"Assigned {n} claims to existing narratives.")
    finally:
        await store.close()


@rel_app.command("clean-html")
def rel_clean_html(
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Preview without deleting"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Remove claims that are HTML boilerplate (Readability failures)."""
    asyncio.run(_rel_clean_html(dry_run, config_path))


async def _rel_clean_html(dry_run: bool, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    sql = "SELECT claim_id, LEFT(canonical_text, 60) AS preview FROM claim WHERE canonical_text ~ '^\\s*<(!DOCTYPE|html|script|style|\\[CDATA)'"
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql)
    if not rows:
        typer.echo("No HTML claims found.")
        await store.close()
        return
    if dry_run:
        typer.echo(f"Would remove {len(rows)} HTML claims:")
        for r in rows[:10]:
            typer.echo(f"  {r['claim_id']}  {r['preview']}")
        if len(rows) > 10:
            typer.echo(f"  ... and {len(rows)-10} more")
    else:
        ids = tuple(r["claim_id"] for r in rows)
        async with store._pool.acquire() as conn:
            await conn.execute("DELETE FROM claim_observation WHERE claim_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM claim_entity WHERE claim_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM narrative_claim WHERE claim_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM claim_relationship WHERE source_claim_id = ANY($1::uuid[]) OR target_claim_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM claim_embedding WHERE claim_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM claim WHERE claim_id = ANY($1::uuid[])", ids)
        typer.echo(f"Removed {len(rows)} HTML claims.")
    await store.close()


@rel_app.command("clean-entities")
def rel_clean_entities(
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Preview without deleting"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Remove noise entities (common English words misidentified as entities)."""
    asyncio.run(_rel_clean_entities(dry_run, config_path))


async def _rel_clean_entities(dry_run: bool, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    count = await store.clean_noise_entities(dry_run=dry_run)
    if dry_run:
        typer.echo(f"Would remove {count} noise entities. Run with --execute to apply.")
    else:
        typer.echo(f"Removed {count} noise entities.")
    await store.close()


@rel_app.command("similar")
def rel_similar(
    claim_id: str = typer.Argument(..., help="Claim UUID"),
    threshold: float = typer.Option(0.85, "--threshold", "-t"),
    limit: int = typer.Option(10, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Find similar claims to a given claim."""
    asyncio.run(_rel_similar(claim_id, threshold, limit, config_path))


async def _rel_similar(claim_id: str, threshold: float, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    similar = await store.find_similar_claims(UUID(claim_id), threshold=threshold, limit=limit)
    if not similar:
        typer.echo(f"No similar claims found (threshold={threshold}).")
    else:
        typer.echo(f"Similar claims to {claim_id[:8]}:")
        for s in similar:
            sim = s.get("similarity", 0)
            text = s.get("canonical_text", "")[:80]
            typer.echo(f"  sim={sim:.4f}  {text}")
    await store.close()


@topic_app.command("list")
def topic_list(
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List narratives with top terms and claim counts."""
    asyncio.run(_topic_list(limit, config_path))


async def _topic_list(limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    narratives = await store.list_narratives(limit=limit)
    if not narratives:
        typer.echo("No narratives.")
    else:
        typer.echo(f"Narratives ({len(narratives)}):")
        typer.echo(f"  {'ID':<8}  {'Claims':>6}  {'Name':<30}  Top Terms")
        typer.echo(f"  {'-'*8}  {'-'*6}  {'-'*30}  {'-'*30}")
        for n in narratives:
            nid = str(n.get("narrative_id", ""))[:8]
            cnt = n.get("claim_count", 0)
            name = n.get("name", "")[:30]
            terms = ", ".join(str(t) for t in (n.get("top_terms", [])[:6]))
            typer.echo(f"  {nid}  {cnt:>6}  {name:<30}  {terms}")
    await store.close()


@topic_app.command("show")
def topic_show(
    narrative_id: str = typer.Argument(..., help="Narrative UUID"),
    limit: int = typer.Option(20, "--limit", "-l"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show claims within a narrative."""
    asyncio.run(_topic_show(narrative_id, limit, config_path))


async def _resolve_nid(store: EventStore, raw: str) -> UUID | None:
    """Resolve a narrative ID from full UUID or prefix."""
    try:
        return UUID(raw)
    except ValueError:
        pass
    try:
        return await store.resolve_narrative_prefix(raw)
    except ValueError as e:
        typer.echo(f"Error: {e}")
        return None


async def _topic_show(narrative_id: str, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    nid = await _resolve_nid(store, narrative_id)
    if not nid:
        typer.echo("Narrative not found.")
        await store.close()
        return
    claims = await store.get_narrative_claims(nid, limit=limit)
    if not claims:
        typer.echo("No claims in this narrative.")
    else:
        typer.echo(f"Claims in narrative {narrative_id[:8]} ({len(claims)}):")
        for c in claims:
            weight = c.get("weight", 0)
            text = c.get("canonical_text", "")[:90]
            typer.echo(f"  w={weight:.3f}  {text}")
    await store.close()


@topic_app.command("rename")
def topic_rename(
    narrative_id: str = typer.Argument(..., help="Narrative UUID"),
    name: str = typer.Argument(..., help="New human-readable name"),
    description: str | None = typer.Option(None, "--description", "-d"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Rename a narrative with a human-readable label."""
    asyncio.run(_topic_rename(narrative_id, name, description, config_path))


async def _topic_rename(narrative_id: str, name: str, description: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    nid = await _resolve_nid(store, narrative_id)
    if not nid:
        typer.echo("Narrative not found.")
        await store.close()
        return
    ok = await store.rename_narrative(nid, name, description=description)
    if ok:
        typer.echo(f"Renamed narrative {narrative_id[:8]} to '{name}'.")
    else:
        typer.echo("Narrative not found or inactive.")
    await store.close()


@topic_app.command("delete")
def topic_delete(
    narrative_id: str = typer.Argument(..., help="Narrative UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Delete a noise narrative and all its claim assignments."""
    asyncio.run(_topic_delete(narrative_id, config_path))


async def _topic_delete(narrative_id: str, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    nid = await _resolve_nid(store, narrative_id)
    if not nid:
        typer.echo("Narrative not found.")
        await store.close()
        return
    ok = await store.delete_narrative(nid)
    if ok:
        typer.echo(f"Deleted narrative {narrative_id[:8]}.")
    else:
        typer.echo("Narrative not found.")
    await store.close()


@rel_app.command("assign-canonical")
def rel_assign_canonical(
    threshold: float = typer.Option(0.92, "--threshold", "-t", help="Embedding similarity threshold"),
    limit: int = typer.Option(500, "--limit", "-l", help="Max claims to process"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Assign claims to canonical claim groups based on embedding similarity."""
    asyncio.run(_rel_assign_canonical(threshold, limit, config_path))


async def _rel_assign_canonical(threshold: float, limit: int, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    sql = """
    SELECT c.claim_id
    FROM claim c
    JOIN claim_embedding ce ON ce.claim_id = c.claim_id
    WHERE c.canonical_id IS NULL
    LIMIT $1
    """
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql, limit)

    if not rows:
        typer.echo("No claims to assign. All already have canonical groups.")
        await store.close()
        return

    count = 0
    for row in rows:
        cid = row["claim_id"]
        await store.assign_canonical_claim(cid, similarity_threshold=threshold)
        count += 1

    typer.echo(f"Assigned {count} claims to canonical groups (threshold={threshold}).")
    await store.close()


@rel_app.command("enrich")
def rel_enrich(
    limit: int = typer.Option(500, "--limit", "-l", help="Max claims to process"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Enrich claims with temporal references and uncertainty scores."""
    asyncio.run(_rel_enrich(limit, config_path))


async def _rel_enrich(limit: int, config_path: str | None):
    from helioryn.extract.temporal import extract_temporal_references
    from helioryn.extract.uncertainty import detect_uncertainty

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    sql = """
    SELECT c.claim_id, c.canonical_text FROM claim c
    WHERE (c.temporal_references IS NULL OR c.temporal_references = '[]'::jsonb)
       OR NOT EXISTS (SELECT 1 FROM confidence_factor cf WHERE cf.target_type = 'claim' AND cf.target_id = c.claim_id)
    LIMIT $1
    """
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(sql, limit)

    if not rows:
        typer.echo("No claims to enrich.")
        await store.close()
        return

    count = 0
    for row in rows:
        text = row["canonical_text"]
        temporal_refs = extract_temporal_references(text)
        uncertainty = detect_uncertainty(text)
        await store.enrich_claim(
            row["claim_id"],
            temporal_refs,
            uncertainty["score"],
            uncertainty["signals"],
        )
        count += 1

    typer.echo(f"Enriched {count} claims with temporal and uncertainty data.")
    await store.close()


# --- Phase 2 Area 3: Confidence Explainability ---


@rel_app.command("confidence")
def rel_confidence(
    claim_id: str = typer.Argument(..., help="Claim UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show confidence breakdown for a claim."""
    asyncio.run(_rel_confidence(claim_id, config_path))


async def _rel_confidence(claim_id: str, config_path: str | None):
    from uuid import UUID

    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        cid = UUID(claim_id)
    except ValueError:
        typer.echo("Invalid claim ID.")
        await store.close()
        raise typer.Exit(1)

    factors = await store.get_confidence_factors("claim", cid)
    result = await store.compute_claim_confidence(cid)
    composite = result["composite"]

    typer.echo(f"\nConfidence for claim {claim_id[:8]}:")
    typer.echo(f"  Composite: {composite*100:.1f}%\n")
    if not factors:
        typer.echo("  No factors computed yet. Run 'rel enrich' first.")
    else:
        typer.echo(f"  {'Factor':<25} {'Value':>7} {'Weight':>8} {'Explanation'}")
        typer.echo(f"  {'-'*25} {'-'*7} {'-'*8} {'-'*40}")
        for f in factors:
            typer.echo(f"  {f['factor_type']:<25} {f['value']*100:>6.1f}% {f['weight']:>8.1f}  {f.get('explanation', '')[:40]}")
    await store.close()


# --- Phase 2 Area 4: Source Behavior Intelligence ---


@source_app.command("behavior")
def source_behavior(
    source_id: str = typer.Argument(..., help="Source UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show source behavior metrics and timeline."""
    asyncio.run(_source_behavior(UUID(source_id), config_path))


async def _source_behavior(source_id: UUID, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    behavior = await store.get_source_behavior(source_id)
    events = await store.get_source_behavior_events(source_id, limit=10)
    snapshot = await store.get_snapshot(source_id)

    if not snapshot:
        typer.echo(f"Source not found: {source_id}")
        await store.close()
        raise typer.Exit(1)

    typer.echo(f"\nSource Behavior: {snapshot.title or snapshot.source_url[:50]}")
    typer.echo(f"  ID: {source_id}")
    if behavior:
        typer.echo(f"  Claims:      {behavior['n_claims']}")
        typer.echo(f"  Contradictions: {behavior['n_contradictions']} (rate: {float(behavior['contradiction_rate'])*100:.1f}%)")
        typer.echo(f"  Originality: {float(behavior['originality_ratio'])*100:.1f}%")
        typer.echo(f"  Reliability: {float(behavior['reliability_score'])*100:.1f}%")
        typer.echo(f"  First seen:  {behavior['first_seen']}")
        typer.echo(f"  Last seen:   {behavior['last_seen']}")
    else:
        typer.echo("  No behavior metrics recorded yet.")

    if events:
        typer.echo(f"\n  Recent events ({len(events)}):")
        for e in events:
            ts = str(e["observed_at"])[11:19]
            typer.echo(f"    {ts}  {e['event_type']}")
    else:
        typer.echo("\n  No behavior events recorded yet.")

    await store.close()


# --- Phase 3 Area 6: Claim Mutation Detection ---


@rel_app.command("detect-mutations")
def rel_detect_mutations(
    canonical_id: str | None = typer.Option(None, "--canonical", "-c", help="Canonical claim UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Detect claim mutations within canonical groups using edit distance."""
    asyncio.run(_rel_detect_mutations(canonical_id, config_path))


async def _rel_detect_mutations(canonical_id: str | None, config_path: str | None):
    import editdistance

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if canonical_id:
        canon_ids = [UUID(canonical_id)]
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT canonical_id FROM canonical_claim ORDER BY n_sources DESC LIMIT 50"
            )
            canon_ids = [r["canonical_id"] for r in rows]

    total_mutations = 0
    for cid in canon_ids:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT claim_id, canonical_text, extracted_at FROM claim WHERE canonical_id = $1 ORDER BY extracted_at",
                cid,
            )
        texts = [dict(r) for r in rows]
        for i, a in enumerate(texts):
            for j, b in enumerate(texts[i + 1:], i + 1):
                dist = editdistance.eval(a["canonical_text"], b["canonical_text"])
                max_len = max(len(a["canonical_text"]), len(b["canonical_text"]), 1)
                norm_dist = dist / max_len
                if 0 < norm_dist < 0.5:
                    sql_check = """
                    SELECT 1 FROM claim_mutation
                    WHERE source_claim_id = $1 AND target_claim_id = $2
                    """
                    async with store._pool.acquire() as conn:
                        exists = await conn.fetchval(sql_check, a["claim_id"], b["claim_id"])
                    if not exists:
                        sql = """
                        INSERT INTO claim_mutation (source_claim_id, target_claim_id, canonical_id,
                            mutation_type, edit_distance, embedding_similarity, detected_by)
                        VALUES ($1, $2, $3, 'paraphrase', $4,
                          COALESCE((SELECT 1 - (e1.embedding <=> e2.embedding)
                                    FROM claim_embedding e1, claim_embedding e2
                                    WHERE e1.claim_id = $1 AND e2.claim_id = $2
                                      AND e1.model_name = e2.model_name LIMIT 1), 0.0),
                          'rule')
                        """
                        async with store._pool.acquire() as conn:
                            await conn.execute(sql, a["claim_id"], b["claim_id"], cid, norm_dist)
                        total_mutations += 1

    typer.echo(f"Detected {total_mutations} mutations across {len(canon_ids)} canonical groups.")
    await store.close()


# --- Phase 3 Area 8: Narrative Stability ---


@rel_app.command("narrative-stability")
def rel_narrative_stability(
    narrative_id: str | None = typer.Option(None, "--narrative", "-n", help="Narrative UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Compute narrative stability scores."""
    asyncio.run(_rel_narrative_stability(narrative_id, config_path))


async def _rel_narrative_stability(narrative_id: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if narrative_id:
        nar_ids = [UUID(narrative_id)]
    else:
        narratives = await store.list_narratives(limit=100)
        nar_ids = [UUID(str(n["narrative_id"])) for n in narratives if n.get("narrative_id")]

    updated = 0
    for nid in nar_ids:
        async with store._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(DISTINCT c.claim_id) AS claim_count,
                       COUNT(DISTINCT s.source_id) AS source_count,
                       COUNT(DISTINCT r.relationship_id) FILTER (WHERE r.relationship_type = 'contradicts') AS contra_count
                FROM narrative n
                LEFT JOIN narrative_claim nc ON n.narrative_id = nc.narrative_id
                LEFT JOIN claim c ON nc.claim_id = c.claim_id
                LEFT JOIN claim_relationship r ON c.claim_id IN (r.source_claim_id, r.target_claim_id)
                LEFT JOIN source_snapshot s ON c.source_id = s.source_id
                WHERE n.narrative_id = $1
            """, nid)
            if not row or row["claim_count"] == 0:
                continue

            claim_count = row["claim_count"]
            contra_count = row["contra_count"]
            source_count = row["source_count"]
            contra_density = contra_count / claim_count if claim_count else 0
            source_diversity = source_count / claim_count if claim_count else 0
            stability = max(0.0, min(1.0, 1.0 - contra_density * 1.0 + source_diversity * 0.3))

            label = "stable" if stability > 0.8 else "evolving" if stability > 0.6 else "disputed" if stability > 0.4 else "volatile"

            await conn.execute("""
                UPDATE narrative SET
                    stability_score = $1, stability_label = $2,
                    contradiction_density = $3, source_count = $4, source_diversity = $5
                WHERE narrative_id = $6
            """, stability, label, contra_density, source_count, source_diversity, nid)
            updated += 1

    typer.echo(f"Updated stability scores for {updated} narratives.")
    await store.close()


@rel_app.command("narrative-dynamics")
def rel_narrative_dynamics(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Populate narrative momentum, velocity, and divergence metrics."""
    asyncio.run(_rel_populate_narrative_dynamics(None, config_path))


async def _rel_populate_narrative_dynamics(narrative_id: str | None = None, config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if narrative_id:
        nar_ids = [UUID(narrative_id)]
    else:
        narratives = await store.list_narratives(limit=200)
        nar_ids = [UUID(str(n["narrative_id"])) for n in narratives if n.get("narrative_id")]

    updated = 0
    for nid in nar_ids:
        async with store._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(DISTINCT c.claim_id) AS total_claims,
                    COUNT(DISTINCT c.claim_id) FILTER (WHERE c.extracted_at > NOW() - interval '24 hours') AS recent_claims,
                    COUNT(DISTINCT c.claim_id) FILTER (WHERE c.extracted_at > NOW() - interval '7 days') AS week_claims
                FROM narrative n
                LEFT JOIN narrative_claim nc ON n.narrative_id = nc.narrative_id
                LEFT JOIN claim c ON nc.claim_id = c.claim_id
                WHERE n.narrative_id = $1
            """, nid)
            if not row or row["total_claims"] == 0:
                continue

            tc = row["total_claims"]
            recent = row["recent_claims"] or 0
            week = row["week_claims"] or 0
            momentum = min(1.0, recent / max(tc, 1))
            velocity = min(1.0, week / max(tc, 1))
            divergence = max(0.0, 1.0 - (tc - week) / max(tc, 1)) if tc > 0 else 0.0

            await conn.execute("""
                UPDATE narrative SET
                    momentum = $1, velocity = $2, divergence = $3
                WHERE narrative_id = $4
            """, momentum, velocity, divergence, nid)
            updated += 1

    typer.echo(f"Updated dynamics for {updated} narratives.")
    await store.close()


# --- Phase 4 Area 7: Cross-Domain Correlation ---


@rel_app.command("correlations")
def rel_correlations(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Compute cross-domain narrative overlaps and detect anomalies."""
    asyncio.run(_rel_correlations(config_path))


async def _rel_correlations(config_path: str | None):
    import statistics

    config = AppConfig.load(config_path)
    store = await _get_store(config)

    narratives = await store.list_narratives(limit=50)
    nar_ids = [UUID(str(n["narrative_id"])) for n in narratives if n.get("narrative_id")]

    if len(nar_ids) < 2:
        typer.echo("Need at least 2 narratives for correlation analysis.")
        await store.close()
        return

    scores = []
    for i in range(len(nar_ids)):
        for j in range(i + 1, len(nar_ids)):
            a_id, b_id = nar_ids[i], nar_ids[j]
            async with store._pool.acquire() as conn:
                entities_a = await conn.fetch(
                    "SELECT DISTINCT e.entity_id FROM claim_entity ce JOIN entity e ON e.entity_id = ce.entity_id "
                    "JOIN narrative_claim nc ON nc.claim_id = ce.claim_id WHERE nc.narrative_id = $1",
                    a_id,
                )
                entities_b = await conn.fetch(
                    "SELECT DISTINCT e.entity_id FROM claim_entity ce JOIN entity e ON e.entity_id = ce.entity_id "
                    "JOIN narrative_claim nc ON nc.claim_id = ce.claim_id WHERE nc.narrative_id = $1",
                    b_id,
                )
                sources_a = await conn.fetch(
                    "SELECT DISTINCT c.source_id FROM claim c "
                    "JOIN narrative_claim nc ON nc.claim_id = c.claim_id WHERE nc.narrative_id = $1",
                    a_id,
                )
                sources_b = await conn.fetch(
                    "SELECT DISTINCT c.source_id FROM claim c "
                    "JOIN narrative_claim nc ON nc.claim_id = c.claim_id WHERE nc.narrative_id = $1",
                    b_id,
                )
                daily_a = await conn.fetch(
                    "SELECT date_trunc('day', c.extracted_at) AS d, count(*) AS n "
                    "FROM claim c JOIN narrative_claim nc ON nc.claim_id = c.claim_id "
                    "WHERE nc.narrative_id = $1 AND c.extracted_at > NOW() - interval '30 days' "
                    "GROUP BY d ORDER BY d",
                    a_id,
                )
                daily_b = await conn.fetch(
                    "SELECT date_trunc('day', c.extracted_at) AS d, count(*) AS n "
                    "FROM claim c JOIN narrative_claim nc ON nc.claim_id = c.claim_id "
                    "WHERE nc.narrative_id = $1 AND c.extracted_at > NOW() - interval '30 days' "
                    "GROUP BY d ORDER BY d",
                    b_id,
                )

            set_a = {r["entity_id"] for r in entities_a}
            set_b = {r["entity_id"] for r in entities_b}
            set_sa = {r["source_id"] for r in sources_a}
            set_sb = {r["source_id"] for r in sources_b}
            entity_overlap = len(set_a & set_b) / max(len(set_a | set_b), 1)
            source_overlap = len(set_sa & set_sb) / max(len(set_sa | set_sb), 1)

            temporal_r = 0.0
            if daily_a and daily_b:
                day_map_a = {r["d"]: r["n"] for r in daily_a}
                day_map_b = {r["d"]: r["n"] for r in daily_b}
                all_days = sorted(set(day_map_a.keys()) | set(day_map_b.keys()))
                if len(all_days) >= 3:
                    va = [day_map_a.get(d, 0) for d in all_days]
                    vb = [day_map_b.get(d, 0) for d in all_days]
                    mean_a = sum(va) / len(va)
                    mean_b = sum(vb) / len(vb)
                    num = sum((va[i] - mean_a) * (vb[i] - mean_b) for i in range(len(va)))
                    den_a = sum((x - mean_a) ** 2 for x in va) ** 0.5
                    den_b = sum((x - mean_b) ** 2 for x in vb) ** 0.5
                    if den_a > 0 and den_b > 0:
                        temporal_r = num / (den_a * den_b)

            if entity_overlap > 0.2:
                scores.append(entity_overlap)
                anomaly_z = 0.0
                if len(scores) > 1:
                    m = statistics.mean(scores)
                    s = statistics.stdev(scores)
                    anomaly_z = (entity_overlap - m) / s if s > 0 else 0.0
                sql = """
                INSERT INTO narrative_overlap (narrative_a_id, narrative_b_id,
                    overlap_score, shared_entities, shared_sources, temporal_r, anomaly_score)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (narrative_a_id, narrative_b_id) DO UPDATE SET
                    overlap_score = $3, shared_sources = $5, temporal_r = $6, anomaly_score = $7
                """
                async with store._pool.acquire() as conn:
                    await conn.execute(sql, a_id, b_id, entity_overlap,
                                       list(set_a & set_b), list(set_sa & set_sb), temporal_r, anomaly_z)

    if scores:
        mean = statistics.mean(scores)
        std = statistics.stdev(scores) if len(scores) > 1 else 0
        anomalies = [(s, i) for i, s in enumerate(scores) if s > mean + 2 * std] if std > 0 else []

        typer.echo(f"Computed {len(scores)} narrative overlaps.")
        typer.echo(f"  Mean overlap: {mean:.3f}")
        typer.echo(f"  Std deviation: {std:.3f}")
        if anomalies:
            typer.echo(f"  Anomalous overlaps (>2σ): {len(anomalies)}")
            for s, idx in anomalies:
                typer.echo(f"    overlap={s:.3f}")
    else:
        typer.echo("No significant overlaps found.")

    await store.close()


# --- Phase 4 Area 9: Evidence Density ---


@verify_app.command("confidence")
def verify_confidence(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run confidence verification and report distribution."""
    asyncio.run(_verify_confidence(config_path))


async def _verify_confidence(config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    # Fast aggregate queries instead of per-claim iteration
    async with store._pool.acquire() as conn:
        total_claims = await conn.fetchval("SELECT count(*) FROM claim")

        essential = ["source_reliability", "evidence_diversity", "temporal_stability", "extraction_method"]
        n_essential = len(essential)
        missing = await conn.fetchval("""
            SELECT count(*) FROM claim c
            WHERE (
                SELECT COUNT(DISTINCT cf.factor_type) FROM confidence_factor cf
                WHERE cf.target_type = 'claim' AND cf.target_id = c.claim_id
                  AND cf.factor_type = ANY($1::text[])
            ) < $2
        """, essential, n_essential)

        dist = await conn.fetch("""
            SELECT
                CASE
                    WHEN v < 0.2 THEN '0%-20%'
                    WHEN v < 0.4 THEN '20%-40%'
                    WHEN v < 0.6 THEN '40%-60%'
                    WHEN v < 0.8 THEN '60%-80%'
                    ELSE '80%-100%'
                END AS bucket,
                count(*) AS n
            FROM (
                SELECT
                    (SUM(cf.value * cf.weight) / NULLIF(SUM(cf.weight), 0)) AS v
                FROM claim c
                JOIN confidence_factor cf ON cf.target_type = 'claim' AND cf.target_id = c.claim_id
                GROUP BY c.claim_id
            ) sub
            GROUP BY bucket
            ORDER BY bucket
        """)

        mean_conf = await conn.fetchval("""
            SELECT AVG(sub.v) FROM (
                SELECT
                    (SUM(cf.value * cf.weight) / NULLIF(SUM(cf.weight), 0)) AS v
                FROM claim c
                JOIN confidence_factor cf ON cf.target_type = 'claim' AND cf.target_id = c.claim_id
                GROUP BY c.claim_id
            ) sub
        """)

    if total_claims:
        dist_map = {r["bucket"]: r["n"] for r in dist}
        bins = ["0%-20%", "20%-40%", "40%-60%", "60%-80%", "80%-100%"]
        max_bin = max(dist_map.values()) if dist_map else 1

        typer.echo(f"\nConfidence Distribution ({total_claims} claims):")
        for b in bins:
            v = dist_map.get(b, 0)
            bar_len = int(v * 40 / max_bin) if max_bin > 0 else 0
            typer.echo(f"  {b}: {v:>6} {'#' * bar_len}")

        typer.echo(f"\n  Mean confidence: {float(mean_conf)*100:.1f}%" if mean_conf else "\n  Mean confidence: N/A")
        typer.echo(f"  Missing essential factors: {missing}")

    pct_missing = (missing / total_claims * 100) if total_claims else 0
    if pct_missing < 1:
        typer.echo("\n  Confidence check: PASS (< 1% missing factors)")
    else:
        typer.echo(f"\n  Confidence check: FAIL ({pct_missing:.1f}% missing factors)")

    await store.close()


@app.command()
def recompute_confidence(
    source_id: str | None = typer.Option(None, "--source-id", "-s", help="Recompute for a single source"),
    narrative_id: str | None = typer.Option(None, "--narrative-id", "-n", help="Recompute for a single narrative"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Recompute confidence for claims."""
    asyncio.run(_recompute_confidence(source_id, narrative_id, config_path))


async def _recompute_confidence(source_id: str | None, narrative_id: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    claim_ids = []
    if source_id:
        sid = UUID(source_id)
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT claim_id FROM claim WHERE source_id = $1", sid
            )
            claim_ids = [r["claim_id"] for r in rows]
        typer.echo(f"Recomputing confidence for {len(claim_ids)} claims from source {source_id[:8]}")
    elif narrative_id:
        nid = UUID(narrative_id)
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT claim_id FROM narrative_claim WHERE narrative_id = $1", nid
            )
            claim_ids = [r["claim_id"] for r in rows]
        typer.echo(f"Recomputing confidence for {len(claim_ids)} claims in narrative {narrative_id[:8]}")
    else:
        rows = await store._get_all_claim_ids()
        claim_ids = [r["claim_id"] for r in rows]
        typer.echo(f"Recomputing confidence for all {len(claim_ids)} claims...")

    ok = 0
    err = 0
    for cid in claim_ids:
        try:
            await store.compute_claim_confidence(cid)
            ok += 1
        except Exception as ex:
            err += 1

    typer.echo(f"Done: {ok} recomputed, {err} errors")
    await store.close()


# Add _get_all_claim_ids helper
# (added to store.py directly)

@app.command()
def backfill_confidence(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Backfill source behavior and confidence factors for all existing data."""
    asyncio.run(_backfill_confidence(config_path))


async def _backfill_confidence(config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    typer.echo("Phase 1: Backfilling source behavior...")
    src_rows = await store._get_all_source_ids_with_claims()
    for i, row in enumerate(src_rows):
        sid = row["source_id"]
        try:
            async with store._pool.acquire() as conn:
                n_claims = await conn.fetchval(
                    "SELECT count(*) FROM claim WHERE source_id = $1", sid
                )
            await store.update_source_behavior(sid, {"n_claims": n_claims or 0})
            await store.detect_source_contradictions(sid)
            await store.detect_source_corrections(sid)
        except Exception as ex:
            typer.echo(f"  Error on source {str(sid)[:8]}: {ex}")
        if (i + 1) % 25 == 0:
            typer.echo(f"  ... {i + 1}/{len(src_rows)} sources")

    typer.echo(f"  Done: {len(src_rows)} sources")

    typer.echo("\nPhase 2: Backfilling source reliability scores...")
    for i, row in enumerate(src_rows):
        sid = row["source_id"]
        try:
            await store.compute_source_reliability(sid)
        except Exception as ex:
            typer.echo(f"  Error on source {str(sid)[:8]}: {ex}")

    typer.echo(f"  Done: {len(src_rows)} sources")

    typer.echo("\nPhase 3: Bulk computing claim confidence factors...")
    try:
        result = await store.bulk_compute_confidence()
        typer.echo(f"  Done: {result.get('total_claims', '?')} sources processed, "
                   f"{result.get('factors_inserted', '?')} factor types inserted")
    except Exception as ex:
        typer.echo(f"  Bulk confidence computation failed: {ex}")
        import traceback
        typer.echo(traceback.format_exc())

    typer.echo("\nPhase 4: Running verification...")
    from helioryn.verify import verify_pipeline, format_verification
    vresults = await verify_pipeline(store)
    typer.echo(format_verification(vresults))

    await store.close()


@app.command("confidence-cleanup")
def confidence_cleanup(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Remove legacy confidence factors (temporal_precision, uncertainty)
    that were replaced by the 5-factor system."""
    asyncio.run(_confidence_cleanup(config_path))


async def _confidence_cleanup(config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        n = await store.clear_old_confidence_factors()
        typer.echo(f"Removed {n} old scaffolding factors.")
        typer.echo("Run 'helioryn backfill-confidence' to recompute all factors.")
    finally:
        await store.close()


@verify_app.command("source-reliability")
def verify_source_reliability(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Compute and report source reliability scores for all sources."""
    asyncio.run(_verify_source_reliability(config_path))


async def _verify_source_reliability(config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    rows = await store._get_all_source_ids_with_claims()
    if not rows:
        typer.echo("No sources with claims found.")
        await store.close()
        return

    typer.echo(f"\nScoring reliability for {len(rows)} sources...")
    scores = []
    for row in rows:
        sid = row["source_id"]
        try:
            score = await store.compute_source_reliability(sid)
            behavior = await store.get_source_behavior(sid)
            scores.append((score, sid, behavior))
        except Exception as ex:
            typer.echo(f"  Error for {str(sid)[:8]}: {ex}")

    scores.sort(key=lambda x: x[0], reverse=True)

    typer.echo(f"\nSource Reliability Report ({len(scores)} sources):")
    typer.echo(f"  {'Source':<10} {'Score':>7} {'Claims':>7} {'Contra':>7} {'Orig':>6} {'Repeat':>7}")
    typer.echo(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*7}")
    for score, sid, b in scores:
        sid_str = str(sid)[:8]
        nc = b["n_claims"] if b else 0
        nct = b["n_contradictions"] if b else 0
        no = b["n_original_claims"] if b else 0
        nr = b["n_repeated_claims"] if b else 0
        typer.echo(f"  {sid_str:<10} {score*100:>6.1f}% {nc:>7} {nct:>7} {no:>6} {nr:>7}")

    mean_score = sum(s[0] for s in scores) / len(scores) if scores else 0
    typer.echo(f"\n  Mean reliability: {mean_score*100:.1f}%")
    typer.echo(f"  Lowest: {scores[-1][0]*100:.1f}% (source {str(scores[-1][1])[:8]})")
    typer.echo(f"  Highest: {scores[0][0]*100:.1f}% (source {str(scores[0][1])[:8]})")

    await store.close()


@verify_app.command("pipeline")
def verify_pipeline_cmd(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Run all pipeline health checks and report pass/fail for each."""
    asyncio.run(_verify_pipeline(config_path))


async def _verify_pipeline(config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        from helioryn.verify import verify_pipeline, format_verification
        vresults = await verify_pipeline(store)
        typer.echo(format_verification(vresults))
    finally:
        await store.close()


@verify_app.command("calibrate")
def verify_calibrate(
    sample: int = typer.Option(10, "--sample", "-n", help="Claims per bucket"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Sample claims from each confidence bucket for manual calibration."""
    asyncio.run(_verify_calibrate(sample, config_path))


async def _verify_calibrate(sample: int, config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        buckets = ["0%-20%", "20%-40%", "40%-60%", "60%-80%", "80%-100%"]
        typer.echo(f"\nConfidence Calibration Sample ({sample} claims per bucket)")
        typer.echo("=" * 72)

        async with store._pool.acquire() as conn:
            for bucket in buckets:
                lo, hi = bucket.split("%-")
                lo_f = float(lo) / 100
                hi_f = float(hi.rstrip("%")) / 100 if hi.rstrip("%") else 1.0
                typer.echo(f"\n  Bucket {bucket}:")

                rows = await conn.fetch("""
                    SELECT c.claim_id, c.canonical_text, c.source_id,
                           sub.v AS confidence
                    FROM (
                        SELECT cf.target_id AS cid,
                               (SUM(cf.value * cf.weight) / NULLIF(SUM(cf.weight), 0)) AS v
                        FROM confidence_factor cf
                        WHERE cf.target_type = 'claim'
                        GROUP BY cf.target_id
                    ) sub
                    JOIN claim c ON c.claim_id = sub.cid
                    WHERE sub.v >= $1 AND sub.v < $2
                    ORDER BY random()
                    LIMIT $3
                """, lo_f, hi_f, sample)

                if not rows:
                    typer.echo("    (no claims in this bucket)")
                    continue

                for r in rows:
                    factors = await conn.fetch("""
                        SELECT factor_type, value, weight, explanation
                        FROM confidence_factor
                        WHERE target_type = 'claim' AND target_id = $1
                    """, r["claim_id"])
                    src = await conn.fetchrow(
                        "SELECT source_url, title FROM source_snapshot WHERE source_id = $1", r["source_id"]
                    )
                    src_label = (src["title"] or src["source_url"] or str(r["source_id"])[:8])[:60] if src else str(r["source_id"])[:8]
                    typer.echo(f"\n  ── Claim {str(r['claim_id'])[:8]} [{r['confidence']*100:.1f}%]")
                    typer.echo(f"     Source: {src_label}")
                    typer.echo(f"     Text: {r['canonical_text'][:120]}")
                    for f in factors:
                        typer.echo(f"       {f['factor_type']}: {f['value']:.3f} × {f['weight']:.2f}  {f['explanation'] or ''}")
                    typer.echo(f"     Assessment:  ___accurate  ___overconfident  ___underconfident  ___wrong")

        typer.echo("\n" + "=" * 72)
        typer.echo("For each claim above, mark assessment and note any issues.")
    finally:
        await store.close()


@verify_app.command("entity-quality")
def verify_entity_quality(
    sample: int = typer.Option(50, "--sample", "-n", help="Claims to sample"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Sample claims with entity extractions for manual precision/recall audit."""
    asyncio.run(_verify_entity_quality(sample, config_path))


async def _verify_entity_quality(sample: int, config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        typer.echo(f"\nEntity Quality Sample ({sample} claims)")
        typer.echo("=" * 72)

        async with store._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.claim_id, c.canonical_text,
                       ARRAY_AGG(DISTINCT e.name) AS entities,
                       ARRAY_AGG(DISTINCT e.entity_type) AS entity_types
                FROM claim c
                JOIN claim_entity ce ON ce.claim_id = c.claim_id
                JOIN entity e ON e.entity_id = ce.entity_id
                GROUP BY c.claim_id
                ORDER BY random()
                LIMIT $1
            """, sample)

        for i, r in enumerate(rows, 1):
            text = r["canonical_text"][:150] if r["canonical_text"] else "(empty)"
            typer.echo(f"\n  [{i}] Claim {str(r['claim_id'])[:8]}")
            typer.echo(f"      Text: {text}")
            typer.echo(f"      Entities Found: {r['entities'] or '(none)'}")
            typer.echo(f"      Entity Types:   {r['entity_types'] or '(none)'}")
            typer.echo(f"      Missing:  ___none  ___org  ___person  ___location  ___event")
            typer.echo(f"      Wrong:    ___none  ___org  ___person  ___location  ___event")

        typer.echo("\n" + "=" * 72)
        typer.echo("For each claim: mark any entities missed (missing) or incorrectly extracted (wrong).")
    finally:
        await store.close()


@verify_app.command("narrative-coherence")
def verify_narrative_coherence(
    claims_per_narrative: int = typer.Option(15, "--claims", "-n", help="Claims per narrative"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Sample top claims from each narrative for manual cluster quality assessment."""
    asyncio.run(_verify_narrative_coherence(claims_per_narrative, config_path))


async def _verify_narrative_coherence(claims_per_narrative: int, config_path: str | None = None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        narratives = await store.list_narratives(limit=50)
        if not narratives:
            typer.echo("No narratives found.")
            return

        typer.echo(f"\nNarrative Coherence Assessment ({claims_per_narrative} claims each)")
        typer.echo("=" * 72)

        async with store._pool.acquire() as conn:
            for nar in narratives:
                nid = nar["narrative_id"]
                name = nar.get("name", "Unnamed")[:50]
                stability = nar.get("stability_label", "unknown")
                momentum = nar.get("momentum", 0)
                typer.echo(f"\n  Narrative: {name}")
                typer.echo(f"  Stability: {stability}  |  Momentum: {momentum:.2f}")

                rows = await conn.fetch("""
                    SELECT c.claim_id, c.canonical_text, c.source_id,
                           COALESCE(ss.title, ss.source_url, 'unknown') AS source_label
                    FROM narrative_claim nc
                    JOIN claim c ON c.claim_id = nc.claim_id
                    LEFT JOIN source_snapshot ss ON ss.source_id = c.source_id
                    WHERE nc.narrative_id = $1
                    ORDER BY c.extracted_at DESC
                    LIMIT $2
                """, nid, claims_per_narrative)

                for r in rows:
                    text = (r["canonical_text"] or "")[:120]
                    src = r["source_label"][:40]
                    typer.echo(f"    • [{str(r['claim_id'])[:8]}] ({src}) {text}")

                typer.echo(f"    Assessment: ___coherent  ___mostly  ___unrelated  ___topic_drift")

        typer.echo("\n" + "=" * 72)
        typer.echo("For each narrative: rate how well the claims form a coherent topic.")
    finally:
        await store.close()


@app.command()
def evidence_density(
    narrative_id: str | None = typer.Option(None, "--narrative", "-n", help="Narrative UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """View evidence density metrics."""
    asyncio.run(_evidence_density(narrative_id, config_path))


async def _evidence_density(narrative_id: str | None, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    if narrative_id:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM evidence_density WHERE narrative_id = $1", UUID(narrative_id)
            )
    else:
        async with store._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM evidence_density ORDER BY claim_count DESC LIMIT 20")

    if not rows:
        typer.echo("No evidence density data. Run 'rel narratives' first.")
    else:
        typer.echo(f"\nEvidence Density ({len(rows)} narratives):")
        typer.echo(f"  {'Narrative':<30} {'Claims':>6} {'Sources':>7} {'Diversity':>9} {'Echo':>6}")
        typer.echo(f"  {'-'*30} {'-'*6} {'-'*7} {'-'*9} {'-'*6}")
        for r in rows:
            name = (r.get("narrative_name") or "")[:28]
            claims = r.get("claim_count", 0)
            sources = r.get("source_count", 0)
            div = r.get("source_diversity", 0)
            echo = r.get("echo_chamber_score", 0)
            typer.echo(f"  {name:<30} {claims:>6} {sources:>7} {div:>9.3f} {echo:>6.3f}")

    await store.close()


# --- Phase 5 Area 10: Annotations ---


@rel_app.command("annotations")
def rel_annotations(
    target_type: str = typer.Option("claim", "--type", "-t", help="Target type: claim, source, narrative"),
    target_id: str = typer.Option(..., "--id", help="Target UUID"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """List annotations for a target."""
    asyncio.run(_rel_annotations(target_type, UUID(target_id), config_path))


async def _rel_annotations(target_type: str, target_id: UUID, config_path: str | None):
    config = AppConfig.load(config_path)
    store = await _get_store(config)

    async with store._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT annotation_id, author, body, tags, is_resolved, created_at "
            "FROM annotation WHERE target_type = $1 AND target_id = $2 ORDER BY created_at DESC",
            target_type, target_id,
        )

    if not rows:
        typer.echo("No annotations for this target.")
    else:
        typer.echo(f"Annotations ({len(rows)}):")
        for r in rows:
            tags = ", ".join(r["tags"]) if r["tags"] else ""
            resolved = "✓" if r["is_resolved"] else "○"
            typer.echo(f"  [{resolved}] {r['author']} ({str(r['created_at'])[:16]})")
            typer.echo(f"    {r['body'][:100]}")
            if tags:
                typer.echo(f"    tags: {tags}")

    await store.close()


# ── Claim Mutations ──────────────────────────────────────────────────────────


@app.command("claim-mutations")
def claim_mutations_cmd(
    claim_id: str = typer.Argument(..., help="Claim UUID to show mutations for"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show text mutations between claim versions."""
    asyncio.run(_claim_mutations(UUID(claim_id), config_path))


async def _claim_mutations(claim_id: UUID, config_path: str | None):
    from helioryn.config import AppConfig

    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        claim = await store.get_claim(claim_id)
        if not claim:
            typer.echo(f"Claim not found: {claim_id}")
            return

        mutations = await store.detect_claim_mutations(claim_id)
        if not mutations:
            typer.echo(f"No mutations found for claim {str(claim_id)[:8]} (only 1 version)")
            return

        typer.echo(f"\nClaim Mutations ({str(claim_id)[:8]}):")
        typer.echo(f"  {'From':>5} → {'To':<5} {'Type':<15} {'Edit Ratio':>10} {'When'}")
        typer.echo(f"  {'-'*5} {'-'*5} {'-'*15} {'-'*10} {'-'*22}")
        for m in mutations:
            when = str(m["to_extracted_at"])[:19] if m["to_extracted_at"] else "?"
            typer.echo(f"  v{m['from_version']:>3} → v{m['to_version']:<3} {m['type']:<15} {m['edit_ratio']*100:>9.1f}%  {when}")
    finally:
        await store.close()


# ── Timeline ─────────────────────────────────────────────────────────────────


@app.command()
def timeline(
    claim_id: str = typer.Argument(..., help="Claim UUID to show timeline for"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show temporal timeline for a claim — observations, events, confidence."""
    asyncio.run(_timeline_cmd(UUID(claim_id), config_path))


async def _timeline_cmd(claim_id: UUID, config_path: str | None):
    from helioryn.config import AppConfig

    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        claim = await store.get_claim(claim_id)
        if not claim:
            typer.echo(f"Claim not found: {claim_id}")
            return

        tl = await store.get_claim_timeline(claim_id)

        typer.echo(f"\nTimeline for claim {str(claim_id)[:8]}:")
        typer.echo(f"  Text: {claim['canonical_text'][:120]}...")

        typer.echo(f"\n  Observations ({len(tl['observations'])}):")
        typer.echo(f"  {'When':<22} {'Source':<40} {'Observer':<20} {'Context'}")
        typer.echo(f"  {'-'*22} {'-'*40} {'-'*20} {'-'*40}")
        for o in tl["observations"]:
            src = (o["source_title"] or o["source_url"] or "?")[:38]
            ctx = (o["context"] or "")[:40]
            typer.echo(f"  {str(o['observed_at'])[:19]:<22} {src:<40} {str(o['observer']):<20} {ctx}")

        if tl["behavior_events"]:
            typer.echo(f"\n  Behavior Events ({len(tl['behavior_events'])}):")
            for e in tl["behavior_events"]:
                typer.echo(f"  [{str(e['observed_at'])[:19]}] {e['event_type']}: {e['detail'] or ''}")

        if tl["confidence_factors"]:
            typer.echo(f"\n  Confidence Factors:")
            typer.echo(f"  {'Type':<25} {'Value':>7} {'Weight':>7}")
            typer.echo(f"  {'-'*25} {'-'*7} {'-'*7}")
            for f in tl["confidence_factors"]:
                typer.echo(f"  {f['factor_type']:<25} {f['value']*100:>6.1f}% {f['weight']:>7.1f}")
    finally:
        await store.close()


# ── Ledger CLI ────────────────────────────────────────────────────────────────

@ledger_app.command("verify")
def ledger_verify(
    claim_id: str = typer.Argument(..., help="Claim UUID to verify"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Verify the integrity of a claim's hash chain."""
    asyncio.run(_ledger_verify(claim_id, config_path))


async def _ledger_verify(claim_id: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        results = await store.verify_chain(claim_id=UUID(claim_id))
        if not results:
            typer.echo("No ledger entries found for this claim.")
            return
        broken = [r for r in results if not r["valid"]]
        if broken:
            typer.echo(f"Chain INTEGRITY FAILURE — {len(broken)} broken links:")
            for b in broken:
                typer.echo(f"  Entry #{b['id']} ({b['entry_type']}): {'; '.join(b['errors'])}")
        else:
            typer.echo(f"Chain INTACT — {len(results)} entries verified.")
    finally:
        await store.close()


@ledger_app.command("export")
def ledger_export(
    claim_id: str = typer.Argument(..., help="Claim UUID to export"),
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Export a human-readable evidence audit trail for a claim."""
    asyncio.run(_ledger_export(claim_id, config_path))


async def _ledger_export(claim_id: str, config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        report = await store.export_chain(UUID(claim_id))
        typer.echo(report)
    finally:
        await store.close()


@ledger_app.command("status")
def ledger_status(
    config_path: str | None = typer.Option(None, "--config", "-c"),
):
    """Show ledger health statistics."""
    asyncio.run(_ledger_status(config_path))


async def _ledger_status(config_path: str | None):
    from helioryn.config import AppConfig
    config = AppConfig.load(config_path)
    store = await _get_store(config)
    try:
        status = await store.ledger_status()
        typer.echo(f"Total entries:  {status['total_entries']}")
        typer.echo(f"Broken links:   {status['broken_links']}")
        typer.echo(f"Latest entry:   {status['latest_entry'] or 'none'}")
        if status["by_type"]:
            typer.echo("\nBy type:")
            for t, c in sorted(status["by_type"].items(), key=lambda x: -x[1]):
                typer.echo(f"  {t}: {c}")
    finally:
        await store.close()

