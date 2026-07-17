# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from helioryn.config import AppConfig
from helioryn.store import EventStore


class StatBox(Static):
    def __init__(self, label: str, value: str = "0", color: str = "white", **kwargs):
        super().__init__("", **kwargs)
        self._label = label
        self._value = value
        self._color = color

    def render(self):
        return f"[bold {self._color}]{self._value}[/]\n[dim]{self._label}[/]"

    def set(self, value: str, color: str | None = None):
        self._value = value
        if color:
            self._color = color
        self.refresh()


class StatsPanel(Vertical):
    def update_stats(self, stats: dict, claim_count: int = 0,
                     query_count: int = 0, entity_count: int = 0):
        now = datetime.now(timezone.utc)
        oldest = stats.get("oldest_source")
        newest = stats.get("newest_source")

        self.query_one("#stat-sources", StatBox).set(
            str(stats.get("total_sources", 0)), "cyan")
        self.query_one("#stat-events", StatBox).set(
            str(stats.get("total_events", 0)), "cyan")
        self.query_one("#stat-claims", StatBox).set(
            str(stats.get("total_claims", 0)), "green")
        self.query_one("#stat-observations", StatBox).set(
            str(stats.get("total_observations", 0)), "green")
        self.query_one("#stat-queries", StatBox).set(
            str(query_count), "yellow")
        self.query_one("#stat-entities", StatBox).set(
            str(stats.get("total_entities", 0)), "magenta")
        self.query_one("#stat-narratives", StatBox).set(
            str(stats.get("total_narratives", 0)), "cyan")
        self.query_one("#stat-rate-1h", StatBox).set(
            f"{stats.get('rate_1h', 0)}/h", "yellow")
        self.query_one("#stat-rate-24h", StatBox).set(
            f"{stats.get('rate_24h', 0)}/d", "yellow")
        self.query_one("#stat-embeddings", StatBox).set(
            str(stats.get("total_embeddings", 0)), "yellow")
        self.query_one("#stat-relationships", StatBox).set(
            str(stats.get("total_relationships", 0)), "yellow")
        self.query_one("#stat-updated", StatBox).set(
            str(stats.get("updated_sources", 0)), "white")
        def _fmt(dt):
            if not dt:
                return "-"
            try:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local = dt.astimezone()
                return local.strftime("%m-%d %I:%M %p")
            except:
                return str(dt)[:16]
        self.query_one("#stat-oldest", StatBox).set(
            _fmt(oldest), "dim")
        self.query_one("#stat-newest", StatBox).set(
            _fmt(newest), "dim")


class ActivityLog(RichLog):
    def on_mount(self):
        self.load_log()

    def load_log(self):
        from helioryn.log import read_log
        events = read_log(100)
        self.clear()
        for e in reversed(events[-50:]):
            ts = e.get("ts", "")[11:19]
            ev = e.get("event", "")
            if ev == "run_started":
                topic = e.get("topic", "")
                self.write(f"[bold cyan]{ts}[/] ▶ Run: {topic[:50]}")
            elif ev == "source_ingested":
                title = e.get("title", "")[:55]
                self.write(f"[green]{ts}[/] ✓ {title}")
            elif ev == "source_skipped":
                reason = e.get("reason", "")
                self.write(f"[yellow]{ts}[/] ⊘ Skipped ({reason})")
            elif ev == "source_failed":
                err = str(e.get("error", ""))[:55]
                self.write(f"[red]{ts}[/] ✗ {err}")
            elif ev == "run_completed":
                ing = e.get("ingested", 0)
                skp = e.get("skipped", 0)
                errs = e.get("errors", 0)
                self.write(
                    f"[bold]{ts}[/] ✔ Done: {ing} in, "
                    f"{skp} skip, {errs} err"
                )
            else:
                self.write(f"  {ts}  {ev}")


class RecentSources(DataTable):
    def on_mount(self):
        self.add_columns("", "Date", "Author", "Title")

    def populate(self, sources: list):
        self.clear()
        self.add_columns("", "Date", "Author", "Title")
        for s in sources:
            title = (s.title or "(no title)")[:60]
            author = (s.author or "—")[:20]
            self.add_row(
                " ",
                s.last_updated_at.strftime("%m-%d %H:%M"),
                author,
                title,
            )


class HeliorynDashboard(App):
    CSS = """
    Screen {
        layout: grid;
        grid-size: 3 3;
        grid-rows: auto 1fr 2fr;
    }
    #header { column-span: 3; }
    #stats-panel {
        column-span: 1;
        border: solid $primary;
        padding: 1;
    }
    StatsPanel { overflow-y: auto; }
    StatBox {
        padding: 0 1;
        height: 3;
    }
    #activity-panel {
        column-span: 2;
        border: solid $secondary;
        padding: 1;
    }
    ActivityLog { height: 100%; }
    #sources-panel {
        column-span: 2;
        border: solid $primary;
        padding: 1;
        row-span: 2;
    }
    DataTable { height: 100%; }
    #footer { column-span: 3; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._config_path = config_path
        self._store: EventStore | None = None

    def compose(self) -> ComposeResult:
        yield Header(name="Helioryn", id="header")
        with Container(id="stats-panel"):
            yield StatsPanel(
                StatBox("Sources", "—", "cyan", id="stat-sources"),
                StatBox("Events", "—", "cyan", id="stat-events"),
                StatBox("Claims", "—", "green", id="stat-claims"),
                StatBox("Observations", "—", "green", id="stat-observations"),
                StatBox("Queries", "—", "yellow", id="stat-queries"),
                StatBox("Entities", "—", "magenta", id="stat-entities"),
                StatBox("Embeddings", "—", "yellow", id="stat-embeddings"),
                StatBox("Relationships", "—", "yellow", id="stat-relationships"),
                StatBox("Narratives", "—", "cyan", id="stat-narratives"),
                StatBox("Rate 1h", "—", "yellow", id="stat-rate-1h"),
                StatBox("Rate 24h", "—", "yellow", id="stat-rate-24h"),
                StatBox("Updated", "—", "white", id="stat-updated"),
                StatBox("Oldest", "-", "dim", id="stat-oldest"),
                StatBox("Newest", "-", "dim", id="stat-newest"),
            )
        with Container(id="activity-panel"):
            yield ActivityLog(id="activity", max_lines=50, highlight=True)
        with Container(id="sources-panel"):
            yield RecentSources(id="sources")
        yield Footer()

    def on_mount(self):
        config = AppConfig.load(self._config_path)
        self._store = EventStore(config.database_url)
        asyncio.create_task(self._connect_and_refresh())
        self.set_interval(5, self._refresh_all)

    async def _ready(self) -> bool:
        return self._store is not None and self._store._pool is not None

    async def _connect_and_refresh(self):
        try:
            await self._store.connect()
            self.query_one("#activity", ActivityLog).write(
                "[green]● Connected[/]"
            )
            await self._refresh_all()
        except Exception as exc:
            self.query_one("#activity", ActivityLog).write(
                f"[red]✗ Connection error: {exc}[/]"
            )

    async def _refresh_all(self):
        try:
            if not await self._ready():
                return
            stats = await self._store.get_stats()
            claim_count = await self._store.get_claim_count()
            query_count = await self._store.get_query_count()
            entity_count = await self._store.get_entity_count()
            self.query_one(StatsPanel).update_stats(
                stats, claim_count=claim_count,
                query_count=query_count, entity_count=entity_count,
            )
            sources = await self._store.list_snapshots(limit=30)
            self.query_one("#sources", RecentSources).populate(sources)
            self.query_one("#activity", ActivityLog).load_log()
        except Exception as exc:
            self.query_one("#activity", ActivityLog).write(
                f"[red]✗ Refresh error: {exc}[/]"
            )

    async def action_refresh(self):
        await self._refresh_all()

    def on_unmount(self):
        if self._store:
            asyncio.create_task(self._store.close())


def run_dashboard(config_path: str | None = None):
    app = HeliorynDashboard(config_path=config_path)
    app.run()
