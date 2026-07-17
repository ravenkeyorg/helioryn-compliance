# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


def disk_usage(path: str = "/") -> dict:
    u = shutil.disk_usage(path)
    free_gb = u.free / (1024**3)
    total_gb = u.total / (1024**3)
    pct_free = u.free / u.total * 100
    return {
        "free_gb": round(free_gb, 1),
        "total_gb": round(total_gb, 1),
        "pct_free": round(pct_free, 1),
    }


PGDATA_PATHS = [
    Path("/opt/homebrew/var/postgresql@16"),
    Path("/opt/homebrew/var/postgresql@15"),
    Path("/var/lib/postgresql"),
]


def find_pgdata() -> Path | None:
    for p in PGDATA_PATHS:
        if (p / "PG_VERSION").exists():
            return p
    return None


def cleanup_pg_temp() -> int:
    pgdata = find_pgdata()
    if not pgdata:
        return 0
    tmp_dir = pgdata / "base" / "pgsql_tmp"
    if not tmp_dir.exists():
        return 0
    freed = 0
    for f in tmp_dir.iterdir():
        if f.is_file():
            freed += f.stat().st_size
            f.unlink()
    return freed


def rotate_log(path: Path, max_mb: int = 50) -> int:
    if not path.exists():
        return 0
    size = path.stat().st_size
    if size < max_mb * 1024 * 1024:
        return 0
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = path.with_name(f"{path.name}.{ts}")
    path.rename(archive)
    return size


def rotate_all_logs() -> list[dict]:
    results = []
    for log in Path("/tmp").glob("helioryn-*.log"):
        mb = rotate_log(log, max_mb=50)
        if mb:
            results.append({"path": str(log), "freed_bytes": mb})
    return results


async def vacuum_db(store) -> str:
    async with store._pool.acquire() as conn:
        await conn.execute("SET statement_timeout = '300s'")
        result = await conn.execute("VACUUM ANALYZE")
        return result


async def prune_old_events(store, days: int = 90) -> int:
    async with store._pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM source_ingested WHERE ingested_at < now() - make_interval(days => $1)",
            days,
        )
        return int(result.split()[-1]) if result else 0
