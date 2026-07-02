# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from uuid import UUID

import asyncpg

from helioryn.models import Claim, ClaimRelationship, Observation, SourceEvent, SourceSnapshot


class EventStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=12,
            init=self._init_connection,
        )

    @staticmethod
    async def _init_connection(conn):
        await conn.set_type_codec(
            "jsonb",
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
        )
        await conn.execute("SET lock_timeout = '10s'")

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def ensure_schema(self):
        sql = """
        CREATE TABLE IF NOT EXISTS source_ingested (
            event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id       UUID NOT NULL,
            source_url      TEXT NOT NULL,
            title           TEXT,
            author          TEXT,
            publish_date    TIMESTAMPTZ,
            retrieved_at    TIMESTAMPTZ NOT NULL,
            raw_text        TEXT NOT NULL,
            raw_html        TEXT,
            content_hash    TEXT NOT NULL,
            metadata        JSONB DEFAULT '{}'::jsonb,
            retrieval_method TEXT NOT NULL,
            ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_source_ingested_source_id
            ON source_ingested (source_id);
        CREATE INDEX IF NOT EXISTS idx_source_ingested_content_hash
            ON source_ingested (content_hash);
        CREATE INDEX IF NOT EXISTS idx_source_ingested_ingested_at
            ON source_ingested (ingested_at);

        CREATE TABLE IF NOT EXISTS source_snapshot (
            source_id       UUID PRIMARY KEY,
            source_url      TEXT NOT NULL,
            title           TEXT,
            author          TEXT,
            publish_date    TIMESTAMPTZ,
            retrieved_at    TIMESTAMPTZ NOT NULL,
            raw_text        TEXT NOT NULL,
            raw_html        TEXT,
            content_hash    TEXT NOT NULL,
            metadata        JSONB DEFAULT '{}'::jsonb,
            retrieval_method TEXT NOT NULL,
            first_seen_at   TIMESTAMPTZ NOT NULL,
            last_updated_at TIMESTAMPTZ NOT NULL
        );

        ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS author TEXT;
        ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS publish_date TIMESTAMPTZ;
        ALTER TABLE source_ingested ADD COLUMN IF NOT EXISTS raw_html TEXT;

        ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS author TEXT;
        ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS publish_date TIMESTAMPTZ;
        ALTER TABLE source_snapshot ADD COLUMN IF NOT EXISTS raw_html TEXT;

        ALTER TABLE source_ingested ALTER COLUMN metadata SET DEFAULT '{}'::jsonb;
        ALTER TABLE source_snapshot ALTER COLUMN metadata SET DEFAULT '{}'::jsonb;

        CREATE OR REPLACE FUNCTION update_source_snapshot()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO source_snapshot (
                source_id, source_url, title, author, publish_date,
                retrieved_at, raw_text, raw_html, content_hash,
                metadata, retrieval_method, first_seen_at, last_updated_at
            ) VALUES (
                NEW.source_id, NEW.source_url, NEW.title, NEW.author, NEW.publish_date,
                NEW.retrieved_at, NEW.raw_text, NEW.raw_html, NEW.content_hash,
                NEW.metadata, NEW.retrieval_method, NEW.ingested_at, NEW.ingested_at
            )
            ON CONFLICT (source_id) DO UPDATE SET
                source_url = EXCLUDED.source_url,
                title = EXCLUDED.title,
                author = EXCLUDED.author,
                publish_date = EXCLUDED.publish_date,
                retrieved_at = EXCLUDED.retrieved_at,
                raw_text = EXCLUDED.raw_text,
                raw_html = EXCLUDED.raw_html,
                content_hash = EXCLUDED.content_hash,
                metadata = EXCLUDED.metadata,
                retrieval_method = EXCLUDED.retrieval_method,
                last_updated_at = EXCLUDED.last_updated_at;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_update_source_snapshot ON source_ingested;
        CREATE TRIGGER trg_update_source_snapshot
            AFTER INSERT ON source_ingested
            FOR EACH ROW
            EXECUTE FUNCTION update_source_snapshot();

        CREATE TABLE IF NOT EXISTS claim (
            claim_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id        UUID NOT NULL,
            source_url       TEXT NOT NULL,
            extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            canonical_text   TEXT NOT NULL,
            original_text    TEXT NOT NULL,
            extraction_confidence REAL DEFAULT 1.0,
            entities         JSONB DEFAULT '[]'::jsonb,
            claim_type       TEXT DEFAULT 'fact',
            context_sentence TEXT,
            current_version  INT NOT NULL DEFAULT 1
        );
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS topic TEXT;
        CREATE INDEX IF NOT EXISTS idx_claim_topic ON claim (topic);
        CREATE INDEX IF NOT EXISTS idx_claim_source_id ON claim (source_id);
        CREATE INDEX IF NOT EXISTS idx_claim_canonical_text ON claim USING hash (canonical_text);
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS current_version INT NOT NULL DEFAULT 1;

        CREATE TABLE IF NOT EXISTS claim_version (
            version_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            claim_id        UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
            version         INT NOT NULL,
            canonical_text  TEXT NOT NULL,
            original_text   TEXT NOT NULL,
            source_id       UUID NOT NULL,
            extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_by   UUID,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_cv_claim ON claim_version (claim_id, version);

        CREATE TABLE IF NOT EXISTS search_query (
            query_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            text         TEXT NOT NULL,
            language     TEXT DEFAULT 'all',
            source       TEXT,
            parent_query TEXT,
            priority     INT DEFAULT 50,
            interval_m   INT DEFAULT 360,
            last_run     TIMESTAMPTZ,
            active       BOOLEAN DEFAULT TRUE,
            created_at   TIMESTAMPTZ DEFAULT now(),
            category     TEXT DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_search_query_text ON search_query (text);

        CREATE TABLE IF NOT EXISTS government_entity (
            entity_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name          TEXT NOT NULL,
            entity_type   TEXT NOT NULL DEFAULT 'government',
            level         TEXT,
            country       TEXT,
            region        TEXT,
            search_name   TEXT NOT NULL,
            aliases       TEXT[] DEFAULT '{}',
            active        BOOLEAN DEFAULT TRUE,
            discovered_by TEXT,
            last_searched TIMESTAMPTZ,
            created_at    TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_gov_entity_level ON government_entity (level);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_entity_name ON government_entity (name);
        ALTER TABLE government_entity ADD COLUMN IF NOT EXISTS entity_type TEXT DEFAULT 'government';
        ALTER TABLE government_entity ALTER COLUMN level DROP NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_gov_entity_type ON government_entity (entity_type);

        CREATE TABLE IF NOT EXISTS claim_observation (
            observation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            claim_id       UUID NOT NULL,
            source_id      UUID NOT NULL,
            observed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            observer       TEXT NOT NULL DEFAULT 'helioryn-ingest',
            context        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_obs_claim_id ON claim_observation (claim_id);
        CREATE INDEX IF NOT EXISTS idx_obs_source_id ON claim_observation (source_id);
        CREATE INDEX IF NOT EXISTS idx_obs_observed_at ON claim_observation (observed_at);

        ALTER TABLE claim_observation ADD COLUMN IF NOT EXISTS observer TEXT;
        ALTER TABLE claim_observation ADD COLUMN IF NOT EXISTS version_id UUID;

        CREATE TABLE IF NOT EXISTS entity (
            entity_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 TEXT NOT NULL,
            entity_type          TEXT NOT NULL DEFAULT 'concept',
            external_ids         JSONB DEFAULT '{}'::jsonb,
            government_entity_id UUID REFERENCES government_entity(entity_id),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_name ON entity (name);

        CREATE TABLE IF NOT EXISTS claim_entity (
            claim_id  UUID NOT NULL,
            entity_id UUID NOT NULL,
            mention   TEXT,
            PRIMARY KEY (claim_id, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_entity (claim_id);
        CREATE INDEX IF NOT EXISTS idx_ce_entity_id ON claim_entity (entity_id);

        ALTER TABLE entity ADD COLUMN IF NOT EXISTS external_ids JSONB DEFAULT '{}'::jsonb;
        ALTER TABLE entity ADD COLUMN IF NOT EXISTS government_entity_id UUID REFERENCES government_entity(entity_id);
        ALTER TABLE entity ADD COLUMN IF NOT EXISTS type_votes JSONB DEFAULT '{}'::jsonb;
        ALTER TABLE claim_entity ADD COLUMN IF NOT EXISTS mention TEXT;

        CREATE TABLE IF NOT EXISTS claim_embedding (
            embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            claim_id     UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
            embedding    vector(384) NOT NULL,
            model_name   TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_ce_claim_id ON claim_embedding (claim_id);
        CREATE INDEX IF NOT EXISTS idx_ce_embedding ON claim_embedding
            USING hnsw (embedding vector_cosine_ops);

        CREATE TABLE IF NOT EXISTS claim_relationship (
            relationship_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_claim_id   UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
            target_claim_id   UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
            relationship_type TEXT NOT NULL,
            confidence        REAL NOT NULL DEFAULT 1.0,
            detected_by       TEXT NOT NULL DEFAULT 'rule',
            detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            evidence          TEXT,
            CONSTRAINT chk_no_self CHECK (source_claim_id != target_claim_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rel_source ON claim_relationship (source_claim_id);
        CREATE INDEX IF NOT EXISTS idx_rel_target ON claim_relationship (target_claim_id);
        CREATE INDEX IF NOT EXISTS idx_rel_type ON claim_relationship (relationship_type);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_pair
            ON claim_relationship (source_claim_id, target_claim_id, relationship_type);

        CREATE TABLE IF NOT EXISTS narrative (
            narrative_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name          TEXT NOT NULL,
            description   TEXT,
            top_terms     TEXT[] DEFAULT '{}',
            claim_count   INT DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_active     BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS narrative_claim (
            narrative_id UUID NOT NULL REFERENCES narrative(narrative_id) ON DELETE CASCADE,
            claim_id     UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
            weight       REAL DEFAULT 0.0,
            PRIMARY KEY (narrative_id, claim_id)
        );
        CREATE INDEX IF NOT EXISTS idx_nc_narrative ON narrative_claim (narrative_id);
        CREATE INDEX IF NOT EXISTS idx_nc_claim ON narrative_claim (claim_id);

        CREATE TABLE IF NOT EXISTS canonical_claim (
            canonical_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_text  TEXT NOT NULL,
            entity_ids      UUID[] DEFAULT '{}',
            n_sources       INT DEFAULT 0,
            n_observations  INT DEFAULT 0,
            first_seen      TIMESTAMPTZ,
            last_seen       TIMESTAMPTZ,
            drift_score     REAL DEFAULT 0.0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS canonical_id UUID REFERENCES canonical_claim(canonical_id);
        CREATE INDEX IF NOT EXISTS idx_claim_canonical_id ON claim (canonical_id);

        CREATE TABLE IF NOT EXISTS entity_alias (
            alias               TEXT PRIMARY KEY,
            entity_id           UUID NOT NULL REFERENCES entity(entity_id),
            canonical_entity_id UUID NOT NULL REFERENCES government_entity(entity_id),
            source              TEXT DEFAULT 'manual'
        );

        ALTER TABLE claim ADD COLUMN IF NOT EXISTS temporal_references JSONB DEFAULT '[]'::jsonb;
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS temporal_range TSTZRANGE;
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS uncertainty_score REAL DEFAULT 0.0;
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS uncertainty_signals JSONB DEFAULT '[]'::jsonb;
        ALTER TABLE claim ADD COLUMN IF NOT EXISTS extraction_method TEXT DEFAULT 'sentence_split';
        ALTER TABLE canonical_claim ADD COLUMN IF NOT EXISTS drift_score REAL DEFAULT 0.0;

        CREATE TABLE IF NOT EXISTS confidence_factor (
            factor_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_type   TEXT NOT NULL,
            target_id     UUID NOT NULL,
            factor_type   TEXT NOT NULL,
            value         REAL NOT NULL,
            weight        REAL NOT NULL DEFAULT 1.0,
            explanation   TEXT NOT NULL,
            computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cf_unique ON confidence_factor (target_type, target_id, factor_type);

        CREATE TABLE IF NOT EXISTS source_behavior (
            source_id               UUID PRIMARY KEY REFERENCES source_snapshot(source_id),
            n_claims                INT DEFAULT 0,
            n_contradictions        INT DEFAULT 0,
            n_corrections           INT DEFAULT 0,
            n_original_claims       INT DEFAULT 0,
            n_repeated_claims       INT DEFAULT 0,
            originality_ratio       REAL DEFAULT 0.0,
            contradiction_rate      REAL DEFAULT 0.0,
            first_seen              TIMESTAMPTZ,
            last_seen               TIMESTAMPTZ,
            avg_propagation_lag_h   REAL,
            reliability_score       REAL DEFAULT 0.5,
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS source_behavior_event (
            event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id    UUID NOT NULL REFERENCES source_snapshot(source_id),
            event_type   TEXT NOT NULL,
            claim_id     UUID REFERENCES claim(claim_id),
            detail       TEXT,
            observed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_sbe_source ON source_behavior_event (source_id);

        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS stability_score REAL DEFAULT 0.5;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS stability_label TEXT DEFAULT 'unknown';
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS source_count INT DEFAULT 0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS source_diversity REAL DEFAULT 0.0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS momentum REAL DEFAULT 0.0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS velocity REAL DEFAULT 0.0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS divergence REAL DEFAULT 0.0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS contradiction_density REAL DEFAULT 0.0;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS current_version INT DEFAULT 1;
        ALTER TABLE narrative ADD COLUMN IF NOT EXISTS last_updated_at TIMESTAMPTZ;

        CREATE TABLE IF NOT EXISTS narrative_version (
            version_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            narrative_id  UUID NOT NULL REFERENCES narrative(narrative_id) ON DELETE CASCADE,
            version       INT NOT NULL,
            name          TEXT NOT NULL,
            top_terms     TEXT[] DEFAULT '{}',
            claim_ids     UUID[] DEFAULT '{}',
            n_claims      INT DEFAULT 0,
            snapshot_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_by UUID
        );
        CREATE INDEX IF NOT EXISTS idx_nv_narrative ON narrative_version (narrative_id, version);

        CREATE TABLE IF NOT EXISTS narrative_contradiction_history (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            narrative_id    UUID NOT NULL REFERENCES narrative(narrative_id),
            contradiction_count INT NOT NULL,
            snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_nch_narrative ON narrative_contradiction_history (narrative_id);

        CREATE TABLE IF NOT EXISTS claim_mutation (
            mutation_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_claim_id   UUID NOT NULL REFERENCES claim(claim_id),
            target_claim_id   UUID NOT NULL REFERENCES claim(claim_id),
            canonical_id      UUID REFERENCES canonical_claim(canonical_id),
            mutation_type     TEXT NOT NULL,
            edit_distance     REAL NOT NULL,
            embedding_similarity REAL NOT NULL,
            detected_by       TEXT NOT NULL DEFAULT 'rule',
            detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_no_self_mutation CHECK (source_claim_id != target_claim_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cm_canonical ON claim_mutation (canonical_id);
        CREATE INDEX IF NOT EXISTS idx_cm_source ON claim_mutation (source_claim_id);

        CREATE TABLE IF NOT EXISTS narrative_overlap (
            overlap_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            narrative_a_id  UUID NOT NULL REFERENCES narrative(narrative_id),
            narrative_b_id  UUID NOT NULL REFERENCES narrative(narrative_id),
            overlap_score   REAL NOT NULL DEFAULT 0.0,
            shared_entities UUID[] DEFAULT '{}',
            shared_sources  UUID[] DEFAULT '{}',
            temporal_r      REAL,
            anomaly_score   REAL,
            detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_no_self CHECK (narrative_a_id != narrative_b_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_no_pair ON narrative_overlap (narrative_a_id, narrative_b_id);

        CREATE TABLE IF NOT EXISTS annotation (
            annotation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_type   TEXT NOT NULL,
            target_id     UUID NOT NULL,
            author        TEXT NOT NULL,
            body          TEXT NOT NULL,
            tags          TEXT[] DEFAULT '{}',
            is_resolved   BOOLEAN DEFAULT FALSE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            version       INT NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_ann_target ON annotation (target_type, target_id);

        CREATE TABLE IF NOT EXISTS annotation_version (
            version_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            annotation_id UUID NOT NULL REFERENCES annotation(annotation_id) ON DELETE CASCADE,
            version       INT NOT NULL,
            body          TEXT NOT NULL,
            tags          TEXT[] DEFAULT '{}',
            changed_by    TEXT NOT NULL,
            changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS investigation (
            investigation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name             TEXT NOT NULL,
            description      TEXT,
            status           TEXT NOT NULL DEFAULT 'open',
            owner            TEXT NOT NULL,
            claims           UUID[] DEFAULT '{}',
            sources          UUID[] DEFAULT '{}',
            narratives       UUID[] DEFAULT '{}',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at      TIMESTAMPTZ,
            resolution       TEXT
        );

        CREATE TABLE IF NOT EXISTS investigation_note (
            note_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id UUID NOT NULL REFERENCES investigation(investigation_id) ON DELETE CASCADE,
            author           TEXT NOT NULL,
            body             TEXT NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS staging_queue (
            queue_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_type   TEXT NOT NULL,
            target_id     UUID NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            submitted_by  TEXT NOT NULL DEFAULT 'system',
            reviewer      TEXT,
            notes         TEXT,
            submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at   TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_sq_status ON staging_queue (status);
        CREATE INDEX IF NOT EXISTS idx_sq_target ON staging_queue (target_type, target_id);

        ALTER TABLE claim ADD COLUMN IF NOT EXISTS review_status TEXT DEFAULT 'auto_approved';

        CREATE MATERIALIZED VIEW IF NOT EXISTS evidence_density AS
        SELECT
            n.narrative_id,
            n.name AS narrative_name,
            COUNT(DISTINCT c.claim_id) AS claim_count,
            COUNT(DISTINCT ss.source_id) AS source_count,
            CASE WHEN COUNT(DISTINCT c.claim_id) > 0
                 THEN COUNT(DISTINCT ss.source_id)::REAL / COUNT(DISTINCT c.claim_id)
                 ELSE 0 END AS source_diversity,
            COUNT(DISTINCT e.entity_id) AS entity_count,
            CASE WHEN COUNT(r.relationship_id) > 0
                 THEN SUM(CASE WHEN r.relationship_type = 'repeated_by' THEN 1 ELSE 0 END)::REAL
                      / COUNT(r.relationship_id)
                 ELSE 0 END AS echo_chamber_score,
            NOW() AS refreshed_at
        FROM narrative n
        LEFT JOIN narrative_claim nc ON n.narrative_id = nc.narrative_id
        LEFT JOIN claim c ON nc.claim_id = c.claim_id
        LEFT JOIN claim_entity ce ON c.claim_id = ce.claim_id
        LEFT JOIN entity e ON ce.entity_id = e.entity_id
        LEFT JOIN claim_relationship r ON c.claim_id IN (r.source_claim_id, r.target_claim_id)
        LEFT JOIN source_snapshot ss ON c.source_id = ss.source_id
        GROUP BY n.narrative_id, n.name;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ed_narrative ON evidence_density (narrative_id);

        CREATE TABLE IF NOT EXISTS app_user (
            user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'viewer',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS interpretation (
            interpretation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_type      TEXT NOT NULL,
            topic             TEXT,
            narrative_id      UUID REFERENCES narrative(narrative_id),
            title             TEXT NOT NULL,
            payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
            claim_ids         UUID[] DEFAULT '{}',
            source_ids        UUID[] DEFAULT '{}',
            narrative_ids     UUID[] DEFAULT '{}',
            severity          TEXT DEFAULT 'info',
            produced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_int_product_type ON interpretation (product_type);
        CREATE INDEX IF NOT EXISTS idx_int_topic ON interpretation (topic);
        CREATE INDEX IF NOT EXISTS idx_int_produced_at ON interpretation (produced_at DESC);
        CREATE INDEX IF NOT EXISTS idx_int_severity ON interpretation (severity);
        CREATE INDEX IF NOT EXISTS idx_int_narrative ON interpretation (narrative_id);

        CREATE TABLE IF NOT EXISTS interpretation_history (
            history_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            interpretation_id UUID NOT NULL REFERENCES interpretation(interpretation_id) ON DELETE CASCADE,
            product_type      TEXT NOT NULL,
            topic             TEXT,
            narrative_id      UUID,
            payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
            snapshot_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_ih_parent ON interpretation_history (interpretation_id);
        CREATE INDEX IF NOT EXISTS idx_ih_type ON interpretation_history (product_type);
        CREATE INDEX IF NOT EXISTS idx_ih_topic ON interpretation_history (topic);
        CREATE INDEX IF NOT EXISTS idx_ih_snapshot ON interpretation_history (snapshot_at DESC);

        CREATE TABLE IF NOT EXISTS app_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS api_credential (
            credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            service_name  TEXT NOT NULL UNIQUE,
            api_key       TEXT NOT NULL,
            base_url      TEXT DEFAULT '',
            description   TEXT DEFAULT '',
            is_active     BOOLEAN NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS project (
            project_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_project_user ON project (user_id);

        CREATE TABLE IF NOT EXISTS chat_session (
            session_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
            project_id  UUID REFERENCES project(project_id) ON DELETE SET NULL,
            title       TEXT NOT NULL DEFAULT 'New Chat',
            mode        TEXT NOT NULL DEFAULT 'public',
            messages    JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_chat_session_user ON chat_session (user_id);
        CREATE INDEX IF NOT EXISTS idx_chat_session_project ON chat_session (project_id);
        CREATE INDEX IF NOT EXISTS idx_chat_session_updated ON chat_session (updated_at DESC);
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def insert_annotation(self, target_type: str, target_id: UUID,
                                 author: str, body: str, tags: list[str] | None = None) -> UUID:
        sql = """
        INSERT INTO annotation (target_type, target_id, author, body, tags)
        VALUES ($1, $2, $3, $4, $5::text[])
        RETURNING annotation_id
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, target_type, target_id, author, body, tags or [])

    async def get_annotations(self, target_type: str, target_id: UUID,
                              limit: int = 50) -> list[dict]:
        sql = """
        SELECT annotation_id, target_type, target_id, author, body, tags,
               is_resolved, created_at, updated_at
        FROM annotation
        WHERE target_type = $1 AND target_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, target_type, target_id, limit)
            return [dict(r) for r in rows]

    async def resolve_annotation(self, annotation_id: UUID) -> bool:
        sql = "UPDATE annotation SET is_resolved = true, updated_at = now() WHERE annotation_id = $1"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, annotation_id)
            return result != "UPDATE 0"

    async def create_investigation(self, name: str, description: str | None,
                                    owner: str) -> UUID:
        sql = """
        INSERT INTO investigation (name, description, owner)
        VALUES ($1, $2, $3) RETURNING investigation_id
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, name, description or "", owner)

    async def close_investigation(self, investigation_id: UUID, resolution: str) -> bool:
        sql = """
        UPDATE investigation SET status = 'closed', resolved_at = now(), resolution = $2
        WHERE investigation_id = $1 AND status = 'open'
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, investigation_id, resolution)
            return result != "UPDATE 0"

    async def list_investigations(self, status: str | None = None) -> list[dict]:
        if status:
            sql = "SELECT * FROM investigation WHERE status = $1 ORDER BY created_at DESC"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, status)
        else:
            sql = "SELECT * FROM investigation ORDER BY created_at DESC"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql)
        return [dict(r) for r in rows]

    async def add_to_investigation(self, investigation_id: UUID,
                                    claim_ids: list[UUID] | None = None,
                                    source_ids: list[UUID] | None = None,
                                    narrative_ids: list[UUID] | None = None) -> bool:
        sets = []
        args: list[str | UUID] = [investigation_id]
        if claim_ids:
            sets.append(f"claims = claims || ${len(args) + 1}::uuid[]")
            args.append(claim_ids)
        if source_ids:
            sets.append(f"sources = sources || ${len(args) + 1}::uuid[]")
            args.append(source_ids)
        if narrative_ids:
            sets.append(f"narratives = narratives || ${len(args) + 1}::uuid[]")
            args.append(narrative_ids)
        if not sets:
            return False
        sql = f"UPDATE investigation SET {', '.join(sets)} WHERE investigation_id = $1"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *args)
            return result != "UPDATE 0"

    async def add_investigation_note(self, investigation_id: UUID, author: str, body: str) -> UUID | None:
        sql = """
        INSERT INTO investigation_note (investigation_id, author, body)
        VALUES ($1, $2, $3) RETURNING note_id
        """
        async with self._pool.acquire() as conn:
            inv = await conn.fetchval("SELECT 1 FROM investigation WHERE investigation_id = $1", investigation_id)
            if not inv:
                return None
            return await conn.fetchval(sql, investigation_id, author, body)

    async def list_investigation_notes(self, investigation_id: UUID) -> list[dict]:
        sql = """
        SELECT note_id, author, body, created_at
        FROM investigation_note
        WHERE investigation_id = $1
        ORDER BY created_at DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, investigation_id)
        return [dict(r) for r in rows]

    async def get_investigation_detail(self, investigation_id: UUID) -> dict | None:
        sql = "SELECT * FROM investigation WHERE investigation_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, investigation_id)
        return dict(row) if row else None

    async def submit_to_staging(self, target_type: str, target_id: UUID,
                                 submitted_by: str = "system", notes: str | None = None) -> UUID:
        sql = """
        INSERT INTO staging_queue (target_type, target_id, submitted_by, notes)
        VALUES ($1, $2, $3, $4) RETURNING queue_id
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, target_type, target_id, submitted_by, notes)

    async def list_staging(self, status: str = "pending", limit: int = 100) -> list[dict]:
        sql = """
        SELECT sq.*,
            COALESCE(c.canonical_text, ss.title, n.name, 'unknown') AS target_label
        FROM staging_queue sq
        LEFT JOIN claim c ON sq.target_type = 'claim' AND sq.target_id = c.claim_id
        LEFT JOIN source_snapshot ss ON sq.target_type = 'source' AND sq.target_id = ss.source_id
        LEFT JOIN narrative n ON sq.target_type = 'narrative' AND sq.target_id = n.narrative_id
        WHERE sq.status = $1
        ORDER BY sq.submitted_at ASC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, status, limit)
        return [dict(r) for r in rows]

    async def review_staging_item(self, queue_id: UUID, reviewer: str,
                                   decision: str, notes: str | None = None) -> bool:
        if decision not in ("approved", "rejected"):
            return False
        new_status = "approved" if decision == "approved" else "rejected"
        sql = """
        UPDATE staging_queue SET status = $1, reviewer = $2, notes = COALESCE($3, notes),
            reviewed_at = now()
        WHERE queue_id = $4 AND status = 'pending'
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, new_status, reviewer, notes, queue_id)
            if result != "UPDATE 0":
                row = await conn.fetchrow(
                    "SELECT target_type, target_id FROM staging_queue WHERE queue_id = $1", queue_id
                )
                if row:
                    tt = row["target_type"]
                    tid = row["target_id"]
                    if decision == "approved" and tt == "claim":
                        await conn.execute(
                            "UPDATE claim SET review_status = 'approved' WHERE claim_id = $1", tid
                        )
                    try:
                        await self.append_ledger("staging_review", {
                            "queue_id": str(queue_id),
                            "decision": decision,
                            "reviewer": reviewer,
                            "notes": notes,
                        }, claim_id=tid if tt == "claim" else None,
                           source_id=tid if tt == "source" else None)
                    except Exception:
                        pass
            return result != "UPDATE 0"

    async def refresh_evidence_density(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY evidence_density")

    async def is_url_archived(self, url: str) -> bool:
        sql = "SELECT 1 FROM source_snapshot WHERE source_url = $1 LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(sql, url)
            return row is not None

    async def is_content_known(self, content_hash: str) -> UUID | None:
        sql = "SELECT source_id FROM source_ingested WHERE content_hash = $1 LIMIT 1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, content_hash)

    async def append_event(self, event: SourceEvent) -> SourceEvent:
        sql = """
        INSERT INTO source_ingested (
            event_id, source_id, source_url, title, author, publish_date,
            retrieved_at, raw_text, raw_html, content_hash, metadata, retrieval_method
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING ingested_at
        """
        async with self._pool.acquire() as conn:
            ingested_at = await conn.fetchval(
                sql,
                event.event_id,
                event.source_id,
                event.source_url,
                event.title,
                event.author,
                event.publish_date,
                event.retrieved_at,
                event.raw_text,
                event.raw_html,
                event.content_hash,
                event.metadata,
                event.retrieval_method,
            )
            event.ingested_at = ingested_at
            try:
                await self.append_ledger("source_fetch", {
                    "url": event.source_url,
                    "content_hash": event.content_hash,
                    "retrieval_method": event.retrieval_method,
                    "title": event.title,
                }, source_id=event.source_id, metadata={"event_id": str(event.event_id)})
            except Exception:
                pass
            return event

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        return d

    async def list_snapshots(
        self, limit: int = 50, offset: int = 0
    ) -> list[SourceSnapshot]:
        sql = """
        SELECT source_id, source_url, title, author, publish_date,
               retrieved_at, raw_text, raw_html,
               content_hash, metadata, retrieval_method,
               first_seen_at, last_updated_at
        FROM source_snapshot
        ORDER BY last_updated_at DESC
        LIMIT $1 OFFSET $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit, offset)
            return [SourceSnapshot(**self._row_to_dict(r)) for r in rows]

    async def get_snapshot(self, source_id: UUID) -> SourceSnapshot | None:
        sql = """
        SELECT source_id, source_url, title, author, publish_date,
               retrieved_at, raw_text, raw_html,
               content_hash, metadata, retrieval_method,
               first_seen_at, last_updated_at
        FROM source_snapshot
        WHERE source_id = $1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, source_id)
            return SourceSnapshot(**self._row_to_dict(row)) if row else None

    async def get_events(
        self, source_id: UUID, limit: int = 100
    ) -> list[SourceEvent]:
        sql = """
        SELECT event_id, source_id, source_url, title, author, publish_date,
               retrieved_at, raw_text, raw_html,
               content_hash, metadata, retrieval_method, ingested_at
        FROM source_ingested
        WHERE source_id = $1
        ORDER BY ingested_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id, limit)
            return [SourceEvent(**self._row_to_dict(r)) for r in rows]

    async def search_content(self, query: str, limit: int = 20) -> list[SourceSnapshot]:
        sql = """
        SELECT source_id, source_url, title, author, publish_date,
               retrieved_at, raw_text, raw_html,
               content_hash, metadata, retrieval_method,
               first_seen_at, last_updated_at
        FROM source_snapshot
        WHERE raw_text ILIKE $1 OR title ILIKE $1
        LIMIT $2
        """
        pattern = f"%{query}%"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, pattern, limit)
            return [SourceSnapshot(**self._row_to_dict(r)) for r in rows]

    async def get_claim(self, claim_id: UUID) -> dict | None:
        sql = """
        SELECT claim_id, source_id, source_url, extracted_at,
               canonical_text, original_text, extraction_confidence,
               entities, claim_type, context_sentence, current_version,
               temporal_references, uncertainty_score, uncertainty_signals,
               review_status
        FROM claim WHERE claim_id = $1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, claim_id)
            return dict(row) if row else None

    async def find_claim_by_text(self, source_id: UUID, canonical_text: str) -> UUID | None:
        sql = """
        SELECT claim_id FROM claim
        WHERE source_id = $1 AND canonical_text = $2
        LIMIT 1
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, source_id, canonical_text)

    async def create_claim_version(self, claim_id: UUID, version: int,
                                    canonical_text: str, original_text: str,
                                    source_id: UUID) -> dict:
        sql = """
        INSERT INTO claim_version (claim_id, version, canonical_text, original_text, source_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING version_id, version
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, claim_id, version, canonical_text, original_text, source_id)
            await conn.execute(
                "UPDATE claim SET current_version = $1 WHERE claim_id = $2",
                version, claim_id,
            )
            return dict(row) if row else {}

    async def get_claim_versions(self, claim_id: UUID) -> list[dict]:
        sql = """
        SELECT version_id, version, canonical_text, original_text, extracted_at, superseded_by
        FROM claim_version
        WHERE claim_id = $1
        ORDER BY version DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id)
            return [dict(r) for r in rows]

    async def insert_claim(self, claim: Claim, topic: str | None = None) -> Claim | None:
        if self._is_boilerplate(claim.canonical_text):
            return None
        claim.topic = claim.topic or topic

        # Check if a claim with same text already exists from this source
        existing_id = await self.find_claim_by_text(claim.source_id, claim.canonical_text)
        if existing_id:
            existing_claim = await self.get_claim(existing_id)
            next_version = (existing_claim.get("current_version", 0) if existing_claim else 0) + 1
            await self.create_claim_version(
                existing_id, next_version,
                claim.canonical_text, claim.original_text,
                claim.source_id,
            )
            claim.claim_id = existing_id
            try:
                await self.append_ledger("claim_extraction", {
                    "canonical_text": claim.canonical_text,
                    "original_text": (claim.original_text or "")[:200],
                    "extraction_confidence": claim.extraction_confidence,
                    "claim_type": claim.claim_type,
                    "is_new_version": True,
                    "version": next_version,
                }, claim_id=claim.claim_id, source_id=claim.source_id)
            except Exception:
                pass
            return claim

        sql = """
        INSERT INTO claim (
            claim_id, source_id, source_url, extracted_at,
            canonical_text, original_text, extraction_confidence,
            entities, claim_type, context_sentence, current_version, topic
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, 1, $11)
        RETURNING extracted_at
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                claim.claim_id,
                claim.source_id,
                claim.source_url,
                claim.extracted_at,
                claim.canonical_text,
                claim.original_text,
                claim.extraction_confidence,
                json.dumps(claim.entities),
                claim.claim_type,
                claim.context_sentence,
                claim.topic,
            )
            try:
                await self.append_ledger("claim_extraction", {
                    "canonical_text": claim.canonical_text,
                    "original_text": (claim.original_text or "")[:200],
                    "extraction_confidence": claim.extraction_confidence,
                    "claim_type": claim.claim_type,
                }, claim_id=claim.claim_id, source_id=claim.source_id)
            except Exception:
                pass
            return claim

    async def get_claim_count_for_source(self, source_id: UUID) -> int:
        sql = "SELECT COUNT(*) FROM claim WHERE source_id = $1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, source_id)

    async def get_claims_for_source(
        self, source_id: UUID, limit: int = 100
    ) -> list[dict]:
        sql = """
        SELECT claim_id, source_id, source_url, extracted_at,
               canonical_text, original_text, extraction_confidence,
               entities, claim_type, context_sentence
        FROM claim
        WHERE source_id = $1
        ORDER BY extracted_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id, limit)
            return [dict(r) for r in rows]

    async def get_claim_count(self) -> int:
        sql = "SELECT COUNT(*) FROM claim"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def get_snapshot_count(self) -> int:
        sql = "SELECT COUNT(*) FROM source_snapshot"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def insert_observation(self, obs: Observation) -> Observation:
        sql = """
        INSERT INTO claim_observation (observation_id, claim_id, source_id, observed_at, observer, context)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING observed_at
        """
        async with self._pool.acquire() as conn:
            await conn.fetchval(
                sql,
                obs.observation_id,
                obs.claim_id,
                obs.source_id,
                obs.observed_at,
                obs.observer,
                obs.context,
            )
            return obs

    async def get_observations_for_claim(self, claim_id: UUID, limit: int = 50) -> list[dict]:
        sql = """
        SELECT observation_id, claim_id, source_id, observed_at, observer, context
        FROM claim_observation
        WHERE claim_id = $1
        ORDER BY observed_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id, limit)
            return [dict(r) for r in rows]

    async def get_observations_for_source(self, source_id: UUID, limit: int = 50) -> list[dict]:
        sql = """
        SELECT observation_id, claim_id, source_id, observed_at, observer, context
        FROM claim_observation
        WHERE source_id = $1
        ORDER BY observed_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id, limit)
            return [dict(r) for r in rows]

    async def get_observation_count(self) -> int:
        sql = "SELECT COUNT(*) FROM claim_observation"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def upsert_claim_entity(self, name: str, entity_type: str = "concept",
                                   conn=None) -> UUID:
        gov_id = await self._resolve_government_entity(name, conn=conn)
        sql = """
        INSERT INTO entity (name, entity_type, government_entity_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET
            entity_type = CASE
                WHEN entity.entity_type = 'concept' THEN EXCLUDED.entity_type
                ELSE entity.entity_type
            END,
            government_entity_id = COALESCE(entity.government_entity_id, EXCLUDED.government_entity_id),
            type_votes = CASE
                WHEN entity.entity_type NOT IN ('concept') AND entity.entity_type IS DISTINCT FROM EXCLUDED.entity_type
                THEN jsonb_set(
                    COALESCE(entity.type_votes, '{}'::jsonb),
                    ARRAY[EXCLUDED.entity_type],
                    to_jsonb(COALESCE((entity.type_votes->>EXCLUDED.entity_type)::int, 0) + 1)
                )
                ELSE COALESCE(entity.type_votes, '{}'::jsonb)
            END
        RETURNING entity_id
        """
        if conn is not None:
            return await conn.fetchval(sql, name, entity_type, gov_id)
        async with self._pool.acquire() as c:
            return await c.fetchval(sql, name, entity_type, gov_id)

    async def _resolve_government_entity(self, name: str, conn=None) -> UUID | None:
        sql = """
        SELECT entity_id FROM government_entity
        WHERE LOWER(name) = LOWER($1)
        LIMIT 1
        """
        if conn is not None:
            return await conn.fetchval(sql, name)
        async with self._pool.acquire() as c:
            return await c.fetchval(sql, name)

    async def link_entity_to_claim(self, claim_id: UUID, entity_id: UUID):
        sql = """
        INSERT INTO claim_entity (claim_id, entity_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, claim_id, entity_id)

    async def get_entities_for_claim(self, claim_id: UUID) -> list[dict]:
        sql = """
        SELECT e.entity_id, e.name, e.entity_type, e.external_ids
        FROM entity e
        JOIN claim_entity ce ON ce.entity_id = e.entity_id
        WHERE ce.claim_id = $1
        ORDER BY e.name
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id)
            return [dict(r) for r in rows]

    async def search_claim_entities(self, query: str, limit: int = 50) -> list[dict]:
        sql = """
        SELECT e.entity_id, e.name, e.entity_type, e.external_ids,
               COUNT(ce.claim_id) AS claim_count
        FROM entity e
        LEFT JOIN claim_entity ce ON ce.entity_id = e.entity_id
        WHERE e.name ILIKE $1
        GROUP BY e.entity_id
        ORDER BY claim_count DESC, e.name
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, f"%{query}%", limit)
            return [dict(r) for r in rows]

    async def get_claim_entity_count(self) -> int:
        sql = "SELECT COUNT(*) FROM entity"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def list_claim_entities(self, limit: int = 50) -> list[dict]:
        sql = """
        SELECT e.entity_id, e.name, e.entity_type, e.external_ids,
               COUNT(ce.claim_id) AS claim_count
        FROM entity e
        LEFT JOIN claim_entity ce ON ce.entity_id = e.entity_id
        GROUP BY e.entity_id
        ORDER BY claim_count DESC, e.name
        LIMIT $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [dict(r) for r in rows]

    # --- Discovery: search queries ---

    async def upsert_query(self, text: str, language: str = "all",
                           source: str = "seed", parent: str | None = None,
                           priority: int = 50, interval_m: int = 360,
                           category: str = "") -> dict:
        sql = """
        INSERT INTO search_query (text, language, source, parent_query, priority, interval_m, category)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (text) DO UPDATE SET
            priority = LEAST(search_query.priority, $5),
            interval_m = LEAST(search_query.interval_m, $6),
            category = CASE WHEN $7 != '' THEN $7 ELSE search_query.category END,
            active = TRUE,
            last_run = CASE WHEN search_query.interval_m <> $6 THEN NULL ELSE search_query.last_run END
        RETURNING query_id, text, priority, interval_m, category
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, text, language, source, parent, priority, interval_m, category)
            return dict(row) if row else {"text": text}

    async def get_next_queries(self, limit: int = 50) -> list[dict]:
        sql = """
        SELECT query_id, text, language, source, priority, interval_m, last_run, category
        FROM search_query
        WHERE active = TRUE
          AND (last_run IS NULL
               OR last_run + (interval_m * interval '1 minute') < now())
        ORDER BY priority ASC, last_run ASC NULLS FIRST
        LIMIT $1
        FOR UPDATE SKIP LOCKED
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [dict(r) for r in rows]

    async def mark_query_run(self, query_id: UUID):
        sql = "UPDATE search_query SET last_run = now() WHERE query_id = $1"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, query_id)

    async def get_query_count(self) -> int:
        sql = "SELECT COUNT(*) FROM search_query WHERE active = TRUE"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def list_queries(self, limit: int = 200) -> list[dict]:
        sql = """
        SELECT query_id, text, language, source, priority, interval_m,
               last_run, active, created_at, category
        FROM search_query
        ORDER BY priority ASC, last_run DESC NULLS FIRST
        LIMIT $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [dict(r) for r in rows]

    async def delete_query(self, query_id: UUID) -> bool:
        sql = "DELETE FROM search_query WHERE query_id = $1"
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, query_id)
            return r != "DELETE 0"

    async def delete_queries(self, query_ids: list[UUID]) -> int:
        if not query_ids:
            return 0
        sql = "DELETE FROM search_query WHERE query_id = ANY($1::uuid[])"
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, query_ids)
            parts = r.split()
            return int(parts[-1]) if parts and parts[-1].isdigit() else 0

    async def seed_queries_from_config(self, topics: list) -> int:
        count = 0
        for t in topics:
            row = await self.upsert_query(
                text=t.query,
                language=t.language,
                source="config",
                priority=10,
                interval_m=t.interval_minutes,
                category=t.category or "",
            )
            if row:
                count += 1
        return count

    async def backfill_query_categories(self) -> dict:
        """Set categories on existing entity-derived queries that have none.

        Uses the entity type from government_entity (for source='entity')
        and entity (for source='claim_entity') to backfill categories.
        """
        result = {"entity": 0, "claim_entity": 0}
        async with self._pool.acquire() as conn:
            # Government entity queries (seed entities)
            sql = """
                UPDATE search_query sq
                SET category = CASE
                    WHEN ge.level IN ('country', 'international') THEN 'Geopolitics'
                    WHEN ge.entity_type = 'company' THEN 'Models'
                    WHEN ge.entity_type = 'investor' THEN 'Funding'
                    WHEN ge.entity_type = 'person' THEN 'ScienceDiscovery'
                    WHEN ge.level IN ('state', 'agency') THEN 'RegulationPolicy'
                    WHEN ge.level = 'city' THEN 'CriticalInfra'
                    ELSE 'Models'
                END
                FROM government_entity ge
                WHERE sq.source = 'entity'
                  AND sq.parent_query = ge.search_name
                  AND (sq.category IS NULL OR sq.category = '')
            """
            result["entity"] = await conn.execute(sql)

            # Claim entity queries (NER entities)
            sql = """
                UPDATE search_query sq
                SET category = CASE
                    WHEN e.entity_type = 'GPE' THEN 'Geopolitics'
                    WHEN e.entity_type = 'ORG' THEN 'Models'
                    WHEN e.entity_type = 'PERSON' THEN 'ScienceDiscovery'
                    ELSE 'AI Research'
                END
                FROM entity e
                WHERE sq.source = 'claim_entity'
                  AND sq.parent_query = e.name
                  AND (sq.category IS NULL OR sq.category = '')
            """
            result["claim_entity"] = await conn.execute(sql)
        return result

    # --- Discovery: government entities ---

    async def upsert_entity(self, name: str, search_name: str,
                            entity_type: str = "government",
                            level: str | None = None,
                            country: str | None = None, region: str | None = None,
                            discovered_by: str = "seed", aliases: list[str] | None = None) -> dict:
        sql = """
        INSERT INTO government_entity (name, level, country, region, search_name, entity_type, discovered_by, aliases)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (name) DO UPDATE SET active = TRUE, entity_type = EXCLUDED.entity_type
        RETURNING entity_id, name, level
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, name, level, country, region, search_name, entity_type, discovered_by, aliases or [])
            return dict(row) if row else {"name": name}

    async def get_entity_count(self) -> int:
        sql = "SELECT COUNT(*) FROM government_entity WHERE active = TRUE"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def list_entities(self, level: str | None = None, limit: int = 50) -> list[dict]:
        if level:
            sql = "SELECT * FROM government_entity WHERE level = $1 AND active = TRUE ORDER BY name LIMIT $2"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, level, limit)
                return [dict(r) for r in rows]
        sql = "SELECT * FROM government_entity WHERE active = TRUE ORDER BY name LIMIT $1"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [dict(r) for r in rows]

    # --- Layer 3b: Embeddings ---

    async def get_all_claim_texts(self) -> list[dict]:
        sql = """
        SELECT claim_id, canonical_text
        FROM claim
        ORDER BY extracted_at
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]

    async def get_claims_without_embeddings(self, model_name: str = "all-MiniLM-L6-v2") -> list[dict]:
        sql = """
        SELECT c.claim_id, c.canonical_text
        FROM claim c
        LEFT JOIN claim_embedding ce ON ce.claim_id = c.claim_id AND ce.model_name = $1
        WHERE ce.embedding_id IS NULL
        ORDER BY c.extracted_at
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, model_name)
            return [dict(r) for r in rows]

    async def store_embedding(self, claim_id: UUID, embedding: list[float], model_name: str = "all-MiniLM-L6-v2"):
        sql = """
        INSERT INTO claim_embedding (claim_id, embedding, model_name)
        VALUES ($1, $2::vector, $3)
        ON CONFLICT DO NOTHING
        """
        emb_str = str(embedding)
        async with self._pool.acquire() as conn:
            await conn.execute(sql, claim_id, emb_str, model_name)

    async def store_embeddings_batch(self, embeddings: list[tuple[UUID, list[float], str]]):
        sql = """
        INSERT INTO claim_embedding (claim_id, embedding, model_name)
        VALUES ($1, $2::vector, $3)
        ON CONFLICT DO NOTHING
        """
        rows = [(cid, str(emb), model) for cid, emb, model in embeddings]
        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)

    async def get_embedding_count(self) -> int:
        sql = "SELECT COUNT(*) FROM claim_embedding"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def find_similar_claims(
        self, claim_id: UUID, threshold: float = 0.85, limit: int = 20,
        model_name: str = "all-MiniLM-L6-v2"
    ) -> list[dict]:
        sql = """
        SELECT
            c.claim_id, c.canonical_text, c.source_url,
            c.extracted_at,
            1 - (ce.embedding <=> (SELECT embedding FROM claim_embedding WHERE claim_id = $1)) AS similarity
        FROM claim_embedding ce
        JOIN claim c ON c.claim_id = ce.claim_id
        WHERE ce.model_name = $3
          AND ce.claim_id != $1
          AND 1 - (ce.embedding <=> (SELECT embedding FROM claim_embedding WHERE claim_id = $1)) >= $2
        ORDER BY similarity DESC
        LIMIT $4
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id, threshold, model_name, limit)
            return [dict(r) for r in rows]

    # --- Layer 3b/3c: Relationships ---

    async def insert_relationship(self, rel: ClaimRelationship) -> ClaimRelationship:
        sql = """
        INSERT INTO claim_relationship (source_claim_id, target_claim_id, relationship_type, confidence, detected_by, evidence)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (source_claim_id, target_claim_id, relationship_type) DO UPDATE SET
            confidence = GREATEST(claim_relationship.confidence, $4),
            detected_by = CASE WHEN $5 = 'embedding' AND claim_relationship.detected_by = 'rule'
                              THEN 'embedding' ELSE claim_relationship.detected_by END,
            evidence = COALESCE(claim_relationship.evidence, $6),
            detected_at = now()
        RETURNING relationship_id
        """
        async with self._pool.acquire() as conn:
            rid = await conn.fetchval(
                sql, rel.source_claim_id, rel.target_claim_id,
                rel.relationship_type, rel.confidence, rel.detected_by, rel.evidence
            )
            rel.relationship_id = rid
            return rel

    async def get_relationships_for_claim(
        self, claim_id: UUID, limit: int = 50
    ) -> list[dict]:
        sql = """
        SELECT r.relationship_id, r.source_claim_id, r.target_claim_id,
               r.relationship_type, r.confidence, r.detected_by, r.detected_at, r.evidence,
               src.canonical_text AS source_text,
               tgt.canonical_text AS target_text
        FROM claim_relationship r
        JOIN claim src ON src.claim_id = r.source_claim_id
        JOIN claim tgt ON tgt.claim_id = r.target_claim_id
        WHERE r.source_claim_id = $1 OR r.target_claim_id = $1
        ORDER BY r.confidence DESC, r.detected_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id, limit)
            return [dict(r) for r in rows]

    async def get_relationship_count(self) -> int:
        sql = "SELECT COUNT(*) FROM claim_relationship"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def detect_same_claims(
        self, threshold: float = 0.88, batch_size: int = 100,
        model_name: str = "all-MiniLM-L6-v2",
        max_claims: int = 500,
    ) -> int:
        """Find same claims via per-claim HNSW neighbor search (uses index efficiently).
        Only processes up to max_claims per call to bound runtime.
        """
        sql_claims = """
        SELECT claim_id FROM claim_embedding
        WHERE model_name = $1
        ORDER BY claim_id
        LIMIT $2
        """
        sql_neighbors = """
        SELECT
            c.claim_id, c.canonical_text,
            1 - (ce.embedding <=> (SELECT embedding FROM claim_embedding WHERE claim_id = $1)) AS similarity
        FROM claim_embedding ce
        JOIN claim c ON c.claim_id = ce.claim_id
        WHERE ce.model_name = $3
          AND ce.claim_id > $1
          AND 1 - (ce.embedding <=> (SELECT embedding FROM claim_embedding WHERE claim_id = $1)) >= $2
        ORDER BY similarity DESC
        LIMIT $4
        """
        async with self._pool.acquire() as conn:
            claim_ids = await conn.fetch(sql_claims, model_name, max_claims)
        count = 0
        for row in claim_ids:
            cid = row["claim_id"]
            async with self._pool.acquire() as conn:
                neighbors = await conn.fetch(sql_neighbors, cid, threshold, model_name, batch_size)
            for n in neighbors:
                rel = ClaimRelationship(
                    source_claim_id=cid,
                    target_claim_id=n["claim_id"],
                    relationship_type="repeated_by",
                    confidence=float(n["similarity"]),
                    detected_by="embedding",
                    evidence=f"Embedding similarity: {n['similarity']:.4f}",
                )
                await self.insert_relationship(rel)
                count += 1
        return count

    # --- Claim Enrichment ---

    async def enrich_claim(self, claim_id: UUID, temporal_refs: list[dict],
                            uncertainty_score: float = 0.0,
                            uncertainty_signals: dict | None = None) -> bool:
        sql = """
        UPDATE claim SET
            temporal_references = $1::jsonb,
            uncertainty_score = $2,
            uncertainty_signals = $3::jsonb
        WHERE claim_id = $4
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                sql,
                json.dumps(temporal_refs),
                uncertainty_score,
                json.dumps(uncertainty_signals or {}),
                claim_id,
            )
            enriched = result != "UPDATE 0"

        # Seed confidence factors from enrichment data
        if enriched:
            # Uncertainty factor: inverse of uncertainty_score (lower uncertainty = higher confidence)
            unc_factor = max(0.0, 1.0 - uncertainty_score)
            await self.insert_confidence_factor("claim", claim_id,
                "uncertainty", unc_factor, 0.8,
                f"Uncertainty score {uncertainty_score:.2f} inverted")

            # Temporal precision factor: higher if specific dates exist
            precise = sum(1 for t in temporal_refs if t.get("normalized") and "-" in t["normalized"])
            total = len(temporal_refs)
            temp_factor = precise / max(total, 1)
            if total == 0:
                temp_factor = 0.3  # neutral when no temporal refs
                await self.insert_confidence_factor("claim", claim_id,
                    "temporal_precision", temp_factor, 0.5,
                    "No temporal references found")
            else:
                await self.insert_confidence_factor("claim", claim_id,
                    "temporal_precision", temp_factor, 0.5,
                    f"{precise}/{total} temporal refs are precise dates")

        return enriched

    # --- Canonical Claim Model ---

    async def assign_canonical_claim(self, claim_id: UUID, similarity_threshold: float = 0.92) -> UUID:
        """Find or create a canonical_claim for this claim based on embedding similarity."""
        # Check if claim has an embedding
        sql_emb = "SELECT embedding FROM claim_embedding WHERE claim_id = $1 LIMIT 1"
        sql_candidates = """
        SELECT c.canonical_id, 1 - (ce.embedding <=> $1) AS similarity
        FROM claim_embedding ce
        JOIN claim c ON c.claim_id = ce.claim_id
        WHERE c.canonical_id IS NOT NULL
          AND 1 - (ce.embedding <=> $1) >= $2
        ORDER BY similarity DESC
        LIMIT 1
        """
        sql_new = """
        INSERT INTO canonical_claim (canonical_text, first_seen, last_seen)
        VALUES ($1, now(), now())
        RETURNING canonical_id
        """
        sql_update = """
        UPDATE canonical_claim SET
            n_sources = n_sources + 1,
            n_observations = n_observations + 1,
            last_seen = now()
        WHERE canonical_id = $1
        """
        sql_claim_text = "SELECT canonical_text FROM claim WHERE claim_id = $1"

        async with self._pool.acquire() as conn:
            emb = await conn.fetchval(sql_emb, claim_id)
            if not emb:
                # No embedding yet — create a new canonical claim
                text = await conn.fetchval(sql_claim_text, claim_id)
                cid = await conn.fetchval(sql_new, text or "unknown")
                await conn.execute("UPDATE claim SET canonical_id = $1 WHERE claim_id = $2", cid, claim_id)
                return cid

            candidate = await conn.fetchrow(sql_candidates, emb, similarity_threshold)
            if candidate and candidate["canonical_id"]:
                cid = candidate["canonical_id"]
                await conn.execute(sql_update, cid)
                await conn.execute("UPDATE claim SET canonical_id = $1 WHERE claim_id = $2", cid, claim_id)
                return cid

            text = await conn.fetchval(sql_claim_text, claim_id)
            cid = await conn.fetchval(sql_new, text or "unknown")
            await conn.execute("UPDATE claim SET canonical_id = $1 WHERE claim_id = $2", cid, claim_id)
            return cid

    async def detect_semantic_drift(self, canonical_id: UUID, threshold: float = 0.15) -> float:
        sql = """
        WITH ranked AS (
            SELECT c.claim_id, ce.embedding, c.extracted_at,
                   ROW_NUMBER() OVER (ORDER BY c.extracted_at) AS rn,
                   COUNT(*) OVER () AS total
            FROM claim c
            JOIN claim_embedding ce ON ce.claim_id = c.claim_id
            WHERE c.canonical_id = $1
        ),
        windows AS (
            SELECT
                AVG(CASE WHEN rn <= total / 2 THEN embedding END) AS early_centroid,
                AVG(CASE WHEN rn > total / 2 THEN embedding END) AS recent_centroid
            FROM ranked
            WHERE embedding IS NOT NULL
        )
        SELECT
            CASE WHEN early_centroid IS NOT NULL AND recent_centroid IS NOT NULL
                 THEN 1 - (early_centroid <=> recent_centroid)
                 ELSE 0.0
            END AS drift
        FROM windows
        """
        async with self._pool.acquire() as conn:
            drift = await conn.fetchval(sql, canonical_id)
            drift = float(drift or 0.0)
            await conn.execute(
                "UPDATE canonical_claim SET drift_score = $1 WHERE canonical_id = $2",
                drift, canonical_id,
            )
            return drift

    async def detect_all_drift(self, threshold: float = 0.15) -> list[dict]:
        sql = "SELECT canonical_id FROM canonical_claim WHERE n_sources > 1"
        results = []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)
        for r in rows:
            drift = await self.detect_semantic_drift(r["canonical_id"], threshold)
            results.append({"canonical_id": str(r["canonical_id"]), "drift": drift})
        return results

    # --- Boilerplate claim filtering ---

    _BOILERPLATE_PATTERNS: list[re.Pattern] = [
        re.compile(r"^(?:retrieved|accessed)\s+\w+", re.IGNORECASE),
        re.compile(r"^doi:\s*10\.", re.IGNORECASE),
        re.compile(r"^isbn:\s*\d", re.IGNORECASE),
        re.compile(r"^join the conversation", re.IGNORECASE),
        re.compile(r"^©|copyright", re.IGNORECASE),
        re.compile(r"^all\s+(?:rights\s+)?reserved", re.IGNORECASE),
        re.compile(r"^https?://", re.IGNORECASE),
        re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE),
        re.compile(r"^\s*$"),
        re.compile(r"^world economic forum\.?$", re.IGNORECASE),
        re.compile(r"^plays?\s+of\s+the", re.IGNORECASE),
        re.compile(r"^(?:sign\s+(?:in|up|out)|subscribe|click\s+here|read\s+more|learn\s+more|contact\s+us|privacy\s+policy|terms\s+of\s+service|cookie|share\s+this|follow\s+us|join\s+the\s+conversation)", re.IGNORECASE),
        re.compile(r"visit\s+(?:the|our)\s", re.IGNORECASE),
        re.compile(r"\[\d+\]|\[edit\]|footnote", re.IGNORECASE),
        re.compile(r"^\d+\s+words?$", re.IGNORECASE),
        re.compile(r"^\w+\s+\^\s+\w+"),
    ]

    @staticmethod
    def _is_boilerplate(text: str) -> bool:
        stripped = text.strip()
        for pattern in EventStore._BOILERPLATE_PATTERNS:
            if pattern.search(stripped):
                return True
        if len(stripped) < 60:
            return not bool(re.search(r"[A-Z][a-z]{3,}", stripped))
        if len(stripped) > 5000:
            return True
        return False

    # --- Layer 3c: Contradiction detection (uses embedding similarity as pre-filter) ---

    @staticmethod
    def _is_html_like(text: str) -> bool:
        return bool(re.match(r"^\s*(?:<!DOCTYPE|<html|<!\[CDATA|<script|<style)", text, re.IGNORECASE))

    @staticmethod
    def _is_year(n: float) -> bool:
        return 1900 <= n <= 2100

    @staticmethod
    def _is_footnote(n: float, text: str) -> bool:
        return n <= 20 and text.rstrip().endswith(str(int(n)))

    _STOPWORDS: frozenset[str] = frozenset({
        'the', 'a', 'an', 'of', 'to', 'in', 'for', 'on', 'and', 'or', 'is', 'are',
        'was', 'were', 'be', 'been', 'by', 'at', 'from', 'as', 'with', 'that', 'this',
        'it', 'its', 'their', 'our', 'has', 'have', 'had', 'do', 'does', 'did',
        'will', 'would', 'could', 'should', 'may', 'might', 'shall', 'can',
        'not', 'no', 'nor', 'but', 'if', 'so', 'than', 'about', 'also', 'more',
        'very', 'just', 'each', 'all', 'any', 'both', 'few', 'most',
        'other', 'some', 'such', 'own', 'same', 'into', 'over', 'after', 'before',
        'between', 'under', 'above', 'below', 'up', 'down', 'out', 'off',
        'per', 'than', 'then', 'now', 'here', 'there', 'when', 'where', 'how',
        'what', 'who', 'whom', 'which', 'why', 'because', 'while', 'although',
        'since', 'until', 'during', 'through', 'against', 'without', 'within',
        'along', 'among', 'about', 'across', 'around', 'behind', 'beyond',
        'inside', 'outside', 'upon', 'via', 'ago', 'yet', 'already',
    })

    @staticmethod
    def _clean_text_for_numbers(text: str) -> str:
        text = re.sub(r"\[\d+(?:[,\s]+\d*)*\]", "", text)
        text = re.sub(r"https?://[^\s]+", "", text)
        text = re.sub(r"\b10\.\d{4,}/[^\s]+", "", text)
        text = re.sub(
            r"\b(?:Executive Order|Page|Section|Chapter|Article|Part|Volume|"
            r"Bill|Act|Law|Regulation|Resolution)\.?\s+\d+\b",
            "", text, flags=re.IGNORECASE,
        )
        text = re.sub(r"\b\d+(?:th|st|nd|rd)\b", "", text)
        text = re.sub(r"([a-zA-Z])\.(\d{1,3})\b", r"\1", text)
        text = re.sub(
            r"^\d\s+(?=[A-Z])|(?:^|\n)\s*\d{1,2}[\.\)]\s+", "", text
        )
        text = re.sub(
            r"(?:January|February|March|April|May|June|"
            r"July|August|September|October|November|December)\s+\d{1,2}\b",
            "", text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b\d{1,2}\s+(?:January|February|March|April|May|June|"
            r"July|August|September|October|November|December)\b",
            "", text, flags=re.IGNORECASE,
        )
        return text

    @staticmethod
    def _extract_meaningful_numbers(text: str) -> list[float]:
        text = EventStore._clean_text_for_numbers(text)
        nums = re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", text)
        result = []
        for n_str in nums:
            try:
                n = float(n_str)
            except (ValueError, OverflowError):
                continue
            if not math.isfinite(n):
                continue
            if n == 0 or n == 1:
                continue
            if n < 0:
                continue
            if n <= 99 and text.rstrip().endswith(str(int(n))) and len(text) < 60:
                continue
            if n >= 10000 and int(n) == n:
                continue
            result.append(n)
        return result[:8]

    @staticmethod
    def _extract_numbers_with_context(text: str, window: int = 5) -> list[tuple[float, set[str]]]:
        text = EventStore._clean_text_for_numbers(text)
        result = []
        for match in re.finditer(r"(?<!\w)-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", text):
            n_str = match.group()
            n = float(n_str)
            if n == 0 or n == 1:
                continue
            if n < 0:
                continue
            if n <= 99 and text.rstrip().endswith(str(int(n))) and len(text) < 60:
                continue
            if n >= 10000 and int(n) == n:
                continue
            before = text[max(0, match.start()-80):match.start()].split()
            after = text[match.end():match.end()+80].split()
            ctx = set()
            for w in (before[-window:] + after[:window]):
                wc = w.lower().strip(".,;:!?()[]{}""''")
                if len(wc) > 2 and wc not in EventStore._STOPWORDS and not wc.replace(".", "").isdigit():
                    ctx.add(wc)
            result.append((n, ctx))
        return result[:8]

    @staticmethod
    def _extract_months(text: str) -> list[str]:
        return re.findall(
            r"(?:January|February|March|April|May|June|"
            r"July|August|September|October|November|December)", text
        )

    @staticmethod
    def _extract_dated_months(text: str) -> list[str]:
        return re.findall(
            r"(?:January|February|March|April|May|June|"
            r"July|August|September|October|November|December)\s+\d{4}", text
        )

    @staticmethod
    def _extract_roles(text: str) -> list[str]:
        return re.findall(
            r"(?:serves\s+as\s+|appointed\s+(?:as\s+)?|named\s+|"
            r"is\s+the\s+)([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2})",
            text
        )

    @staticmethod
    def _extract_stance(text: str) -> list[str]:
        stances = []
        if re.search(r"\b(supports?|supported|backed|advocates?\s+for|advocated\s+for|in\s+favor\s+of|pro-)\b", text, re.IGNORECASE):
            stances.append("support")
        if re.search(r"\b(opposes?|opposed|against|rejects?|rejected|anti-|fights?|fought|blocks?|blocked)\b", text, re.IGNORECASE):
            stances.append("oppose")
        if re.search(r"\b(banned?|banning|prohibits?|prohibited|outlaws?|outlawed|restricts?|restricted)\b", text, re.IGNORECASE):
            stances.append("restrict")
        if re.search(r"\b(allows?|allowed|permits?|permitted|authorizes?|authorized|legalizes?|legalized|approves?|approved)\b", text, re.IGNORECASE):
            stances.append("permit")
        if re.search(r"\b(incr(?:ease|eases|eased|easing)|raises?|raised|raising|expands?|expanded|expanding|grows?|grew|growing|rises?|rose|rising)\b", text, re.IGNORECASE):
            stances.append("increase")
        if re.search(r"\b(decr(?:ease|eases|eased|easing)|reduces?|reduced|reducing|cuts?|cutting|shrinks?|shrank|shrinking|declines?|declined|declining|falls?|fell|falling)\b", text, re.IGNORECASE):
            stances.append("decrease")
        return stances

    @staticmethod
    def _is_historical_year_ctx(ctx: set[str]) -> bool:
        """Check if context suggests a historical year (BC/AD/BCE/CE) rather than a quantity."""
        if ctx & {"bc", "ad", "bce", "ce", "century", "centuries"}:
            return True
        # Also check for composite tokens like "bc–ad" or "bce–ce"
        for token in ctx:
            if any(marker in token for marker in ("bc", "ad", "bce", "ce")):
                return True
        return False

    @staticmethod
    def _extract_number_units(text: str, window: int = 3) -> list[tuple[float, str, set[str]]]:
        """Extract numbers with their units and surrounding context words."""
        text_clean = EventStore._clean_text_for_numbers(text)
        result = []
        for match in re.finditer(r"(?<!\w)-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", text_clean):
            n_str = match.group()
            n = float(n_str)
            if n == 0 or n == 1:
                continue
            if n < 0:
                continue
            if n <= 99 and text_clean.rstrip().endswith(str(int(n))) and len(text_clean) < 60:
                continue
            if n >= 10000 and int(n) == n:
                continue

            pos = match.start()
            full_text_lower = text.lower()
            before_words = full_text_lower[max(0, pos - 60):pos].split()
            after_words = full_text_lower[match.end():match.end() + 60].split()

            ctx = set()
            for w in (before_words[-window:] + after_words[:window]):
                wc = w.strip(".,;:!?()[]{}""''-$¢£¥€%")
                if len(wc) > 1 and wc not in EventStore._STOPWORDS:
                    ctx.add(wc)

            # Skip historical years (BC/AD context)
            if EventStore._is_historical_year_ctx(ctx):
                continue

            # Skip page/footnote numbers (p. 109, pp. 22-25, note 5, etc.)
            if "pp" in ctx or "page" in ctx or "pages" in ctx:
                continue

            # Extract unit: look for $, €, £, %, million, billion, trillion, percent
            unit = ""
            if "$" in text_clean[max(0, pos - 2):pos] or "dollar" in ctx:
                unit = "currency"
            elif "%" in text_clean[match.end():match.end() + 2] or "percent" in ctx:
                unit = "percent"
            for uword in ["million", "billion", "trillion", "thousand"]:
                if uword in ctx:
                    unit = uword
                    break

            result.append((n, unit, ctx))
        return result[:8]

    @staticmethod
    def _check_numeric_contradiction(text_a: str, text_b: str, numeric_diff: float) -> str | None:
        nums_a = EventStore._extract_number_units(text_a)
        nums_b = EventStore._extract_number_units(text_b)
        if not nums_a or not nums_b:
            return None
        if len(nums_a) > 3 or len(nums_b) > 3:
            return None
        # Different number counts likely means numbers refer to different things
        if len(nums_a) != len(nums_b):
            return None

        for na, unit_a, ctx_a in nums_a:
            for nb, unit_b, ctx_b in nums_b:
                if na == nb:
                    continue
                if EventStore._is_year(na) and EventStore._is_year(nb):
                    continue
                if EventStore._is_year(na) or EventStore._is_year(nb):
                    continue
                # Units must match for meaningful comparison
                if unit_a and unit_b and unit_a != unit_b:
                    continue
                # Need at least 2 shared context words for meaningful comparison
                if ctx_a and ctx_b and len(ctx_a & ctx_b) < 2:
                    continue
                # If either number lacks context (< 2 words), require at least one to have a unit
                if (len(ctx_a) < 2 and not unit_a) or (len(ctx_b) < 2 and not unit_b):
                    continue
                # Small unlabeled numbers (< 200, no unit) are likely page/footnote refs
                if not unit_a and not unit_b and na < 200 and nb < 200:
                    continue
                if abs(na - nb) / (max(abs(na), abs(nb)) + 1) >= numeric_diff:
                    return f"numeric: {na} vs {nb} (unit={unit_a or unit_b or 'none'})"
        return None

    @staticmethod
    def _strip_metadata_lines(text: str) -> str:
        """Remove lines that contain archive/citation metadata, not content."""
        lines = text.split("\n")
        clean = []
        for line in lines:
            stripped = line.strip()
            # Citation footnotes: "- ^ Author (Date)" or "1. ^ Author (Date)"
            if re.match(r"^\s*[-–—]\s*\^", stripped):
                continue
            if re.match(r"^\s*\d+\.?\s*\^", stripped):
                continue
            # "Archived ... at the Wayback Machine"
            if re.search(r"archived?.*wayback\s+machine", stripped, re.IGNORECASE):
                continue
            # "Archived from the original on/at..."
            if re.search(r"archived?\s+(?:from|on|at)\s+(?:the\s+)?original", stripped, re.IGNORECASE):
                continue
            # Any line that is purely an archive date: "Retrieved/Archived/Accessed <date>"
            if re.match(r"^\s*(?:retrieved|archived|accessed|last\s+updated)\s+", stripped, re.IGNORECASE):
                continue
            clean.append(stripped)
        return "\n".join(clean)

    @staticmethod
    def _is_boilerplate_pair(text_a: str, text_b: str) -> bool:
        """Detect boilerplate text pairs that differ only by date/number tokens.

        Boilerplate texts like "Stock Advisor returns as of February 19, 2026" are
        the same disclaimer updated for different periods, not genuine contradictions.
        """
        boilerplate_markers = [
            "stock advisor returns as of",
            "if you invested $1,000 at the time of our recommendation",
            "returns as of",
            "this list on december",
        ]
        a_clean = text_a.lower().strip()
        b_clean = text_b.lower().strip()
        for marker in boilerplate_markers:
            if marker in a_clean and marker in b_clean:
                return True
        # If after stripping all dates and numbers the texts are identical, it's boilerplate
        strip_date_num = re.compile(
            r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b"  # 2026-02-19, 02/19/2026
            r"|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+\d{4}\b"
            r"|\b\d{1,2}:\d{2}\s*(?:am|pm)\b"
            r"|\$\s*[\d,]+(?:\.\d+)?"
            r"|\b\d[\d,]*\b"
        )
        a_stripped = strip_date_num.sub("", a_clean).strip()
        b_stripped = strip_date_num.sub("", b_clean).strip()
        # Also collapse all whitespace
        a_stripped = re.sub(r"\s+", " ", a_stripped).strip()
        b_stripped = re.sub(r"\s+", " ", b_stripped).strip()
        if a_stripped and a_stripped == b_stripped:
            return True
        # Very short boilerplate: just a date-stamped line (e.g. "*Stock Advisor returns as of February 19, 2026.")
        # After stripping dates/numbers, if remaining text is < 40 chars and identical → boilerplate
        if len(a_stripped) < 40 and len(b_stripped) < 40 and a_stripped == b_stripped:
            return True
        return False

    @staticmethod
    def _check_temporal_contradiction(text_a: str, text_b: str) -> str | None:
        # Strip metadata before checking — archive dates and citation footnotes are not content
        clean_a = EventStore._strip_metadata_lines(text_a)
        clean_b = EventStore._strip_metadata_lines(text_b)

        dm_a = set(EventStore._extract_dated_months(clean_a))
        dm_b = set(EventStore._extract_dated_months(clean_b))

        if dm_a and dm_b:
            for da in dm_a:
                year_a = da.split()[-1]
                for db in dm_b:
                    if db.split()[-1] == year_a and da != db:
                        return f"date conflict: '{da}' vs '{db}'"

        # Month-only conflict: require at least one text to have a dated month
        # (month + year) to avoid flagging bare "January" vs "February" references
        # that could refer to different years
        if dm_a or dm_b:
            months_a = set(EventStore._extract_months(clean_a))
            months_b = set(EventStore._extract_months(clean_b))
            if len(months_a) == 1 and len(months_b) == 1 and months_a != months_b:
                diff = months_a ^ months_b
                if diff:
                    return f"month conflict: {diff.pop()}"
        return None

    @staticmethod
    def _check_role_contradiction(text_a: str, text_b: str) -> str | None:
        roles_a = EventStore._extract_roles(text_a)
        roles_b = EventStore._extract_roles(text_b)
        if roles_a and roles_b:
            for ra in roles_a:
                for rb in roles_b:
                    if ra.lower() != rb.lower() and len(ra) > 2 and len(rb) > 2:
                        return f"role conflict: '{ra}' vs '{rb}'"
        return None

    @staticmethod
    def _stance_near_entity(text: str, entity_names: list[str]) -> bool:
        """Check if any stance keyword appears within 5 words of a shared entity name."""
        if not entity_names:
            return True  # no entity info — don't block, assume stance pertains to topic
        text_lower = text.lower()
        for ename in entity_names:
            ename_lower = ename.lower()
            idx = text_lower.find(ename_lower)
            while idx != -1:
                window = text_lower[max(0, idx - 40):idx + len(ename_lower) + 40]
                if re.search(
                    r"\b(supports?|supported|backed|opposes?|opposed|against|"
                    r"rejects?|rejected|banned?|banning|prohibits?|allows?|"
                    r"permitted|incr(?:ease|eases?|eased)|raises?|raised|"
                    r"decr(?:ease|eases?|eased)|reduces?|reduced|cuts?|cutting|"
                    r"rises?|rose|rising|falls?|fell|falling)\b",
                    window, re.IGNORECASE,
                ):
                    return True
                idx = text_lower.find(ename_lower, idx + 1)
        return False

    @staticmethod
    def _check_stance_contradiction(text_a: str, text_b: str,
                                    entity_names: list[str] | None = None) -> str | None:
        stances_a = set(EventStore._extract_stance(text_a))
        stances_b = set(EventStore._extract_stance(text_b))
        if not stances_a or not stances_b:
            return None
        # Require stance keywords near a shared entity in both texts
        entities = entity_names or []
        if not EventStore._stance_near_entity(text_a, entities):
            return None
        if not EventStore._stance_near_entity(text_b, entities):
            return None
        conflicts = {
            ("support", "oppose"), ("oppose", "support"),
            ("restrict", "permit"), ("permit", "restrict"),
            ("increase", "decrease"), ("decrease", "increase"),
        }
        for sa in stances_a:
            for sb in stances_b:
                if (sa, sb) in conflicts:
                    return f"stance conflict: '{sa}' vs '{sb}'"
        return None

    async def detect_contradictions(
        self, sim_threshold: float = 0.60, sim_max: float = 0.80,
        numeric_diff: float = 0.15,
        model_name: str = "all-MiniLM-L6-v2",
        batch_size: int = 5000,
    ) -> int:
        """Contradiction detection using embedding similarity as pre-filter.
        Compares claims that share entities, using batched SQL to avoid per-pair DB round trips.
        """
        sql_pairs_with_sim = """
        WITH entity_pairs AS (
            SELECT DISTINCT ce1.claim_id AS cid_a, ce2.claim_id AS cid_b
            FROM claim_entity ce1
            JOIN claim_entity ce2 ON ce2.entity_id = ce1.entity_id AND ce2.claim_id > ce1.claim_id
        )
        SELECT ep.cid_a, ep.cid_b,
               1 - (cea.embedding <=> ceb.embedding) AS similarity
        FROM entity_pairs ep
        JOIN claim_embedding cea ON cea.claim_id = ep.cid_a AND cea.model_name = $1
        JOIN claim_embedding ceb ON ceb.claim_id = ep.cid_b AND ceb.model_name = $1
        WHERE 1 - (cea.embedding <=> ceb.embedding) BETWEEN $2 AND $3
        ORDER BY similarity DESC
        LIMIT $4
        """
        sql_claim_pair = """
        SELECT ca.claim_id AS cid_a, ca.canonical_text AS text_a, ca.source_id AS source_a,
               cb.claim_id AS cid_b, cb.canonical_text AS text_b, cb.source_id AS source_b
        FROM (SELECT * FROM claim WHERE claim_id = $1) ca
        CROSS JOIN (SELECT * FROM claim WHERE claim_id = $2) cb
        """
        sql_shared_entities = """
        SELECT DISTINCT e.name
        FROM claim_entity ce1
        JOIN claim_entity ce2 ON ce2.entity_id = ce1.entity_id AND ce2.claim_id = $2
        JOIN entity e ON e.entity_id = ce1.entity_id
        WHERE ce1.claim_id = $1
        """
        sql_check_exists = """
        SELECT 1 FROM claim_relationship
        WHERE ((source_claim_id=$1 AND target_claim_id=$2)
           OR (source_claim_id=$2 AND target_claim_id=$1))
          AND relationship_type='contradicts'
        LIMIT 1
        """

        async with self._pool.acquire() as conn:
            await conn.execute("SET work_mem = '256MB'")
            await conn.execute("SET maintenance_work_mem = '512MB'")
            candidates = await conn.fetch(sql_pairs_with_sim, model_name, sim_threshold, sim_max, batch_size)

        if not candidates:
            return 0

        count = 0
        for row in candidates:
            ca, cb = row["cid_a"], row["cid_b"]
            similarity = float(row["similarity"])

            async with self._pool.acquire() as conn:
                pair_row = await conn.fetchrow(sql_claim_pair, ca, cb)
            if not pair_row:
                continue
            a_text = pair_row["text_a"]
            b_text = pair_row["text_b"]
            a_source = pair_row["source_a"]
            b_source = pair_row["source_b"]
            if a_source == b_source:
                continue
            if a_text == b_text:
                continue
            if self._is_html_like(a_text) or self._is_html_like(b_text):
                continue
            if self._is_boilerplate_pair(a_text, b_text):
                continue

            async with self._pool.acquire() as conn:
                entity_rows = await conn.fetch(sql_shared_entities, ca, cb)
            entity_names = [r["name"] for r in entity_rows]

            evidence_parts = []
            num = self._check_numeric_contradiction(a_text, b_text, numeric_diff)
            if num:
                evidence_parts.append(num)
            temporal = self._check_temporal_contradiction(a_text, b_text)
            if temporal:
                evidence_parts.append(temporal)
            role = self._check_role_contradiction(a_text, b_text)
            if role:
                evidence_parts.append(role)
            stance = self._check_stance_contradiction(a_text, b_text, entity_names)
            if stance:
                evidence_parts.append(stance)

            if not evidence_parts:
                continue

            async with self._pool.acquire() as conn:
                exists = await conn.fetchval(sql_check_exists, ca, cb)
                if not exists:
                    rel = ClaimRelationship(
                        source_claim_id=ca,
                        target_claim_id=cb,
                        relationship_type="contradicts",
                        confidence=round(similarity, 3),
                        detected_by="rule",
                        evidence="; ".join(evidence_parts) + f" (sim={similarity:.2f})",
                    )
                    await self.insert_relationship(rel)
                    count += 1
        return count

    # --- Layer 3d: supports ---

    async def detect_supports(
        self, sim_min: float = 0.40, sim_max: float = 0.88,
        batch_size: int = 5000,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> int:
        """Find supports relationships: claims sharing entities + similar stance + compatible framing.

        detour: claims about the same entity that agree in direction (not contradictory).
        """
        sql = """
        WITH entity_pairs AS (
            SELECT DISTINCT ce1.claim_id AS cid_a, ce2.claim_id AS cid_b
            FROM claim_entity ce1
            JOIN claim_entity ce2 ON ce2.entity_id = ce1.entity_id AND ce2.claim_id > ce1.claim_id
        )
        SELECT ep.cid_a, ep.cid_b,
               1 - (cea.embedding <=> ceb.embedding) AS similarity
        FROM entity_pairs ep
        JOIN claim_embedding cea ON cea.claim_id = ep.cid_a AND cea.model_name = $1
        JOIN claim_embedding ceb ON ceb.claim_id = ep.cid_b AND ceb.model_name = $1
        WHERE 1 - (cea.embedding <=> ceb.embedding) BETWEEN $2 AND $3
          AND NOT EXISTS (
              SELECT 1 FROM claim_relationship cr
              WHERE ((cr.source_claim_id=ep.cid_a AND cr.target_claim_id=ep.cid_b)
                  OR (cr.source_claim_id=ep.cid_b AND cr.target_claim_id=ep.cid_a))
                AND cr.relationship_type = 'contradicts'
          )
        ORDER BY similarity DESC
        LIMIT $4
        """
        sql_pair = """
        SELECT ca.claim_id AS cid_a, ca.canonical_text AS text_a,
               cb.claim_id AS cid_b, cb.canonical_text AS text_b
        FROM (SELECT * FROM claim WHERE claim_id = $1) ca
        CROSS JOIN (SELECT * FROM claim WHERE claim_id = $2) cb
        """
        sql_skip = """
        SELECT 1 FROM claim_relationship
        WHERE ((source_claim_id=$1 AND target_claim_id=$2)
           OR (source_claim_id=$2 AND target_claim_id=$1))
          AND relationship_type IN ('supports', 'contradicts')
        LIMIT 1
        """
        async with self._pool.acquire() as conn:
            candidates = await conn.fetch(sql, model_name, sim_min, sim_max, batch_size)
        if not candidates:
            return 0
        count = 0
        for row in candidates:
            ca, cb = row["cid_a"], row["cid_b"]
            similarity = float(row["similarity"])
            async with self._pool.acquire() as conn:
                pair_row = await conn.fetchrow(sql_pair, ca, cb)
            if not pair_row:
                continue
            a_text, b_text = pair_row["text_a"], pair_row["text_b"]
            if a_text == b_text:
                continue
            if self._is_html_like(a_text) or self._is_html_like(b_text):
                continue
            stance_a = set(self._extract_stance(a_text))
            stance_b = set(self._extract_stance(b_text))
            if not stance_a or not stance_b:
                continue
            conflict_pairs = {("support", "oppose"), ("oppose", "support"),
                             ("restrict", "permit"), ("permit", "restrict"),
                             ("increase", "decrease"), ("decrease", "increase")}
            if any((sa, sb) in conflict_pairs for sa in stance_a for sb in stance_b):
                continue
            async with self._pool.acquire() as conn:
                skip = await conn.fetchval(sql_skip, ca, cb)
                if skip:
                    continue
                rel = ClaimRelationship(
                    source_claim_id=ca, target_claim_id=cb,
                    relationship_type="supports",
                    confidence=round(similarity, 3),
                    detected_by="rule",
                    evidence=f"Shared entity + compatible stance (sim={similarity:.2f})",
                )
                await self.insert_relationship(rel)
                count += 1
        return count

    # --- Layer 3d: evolves_into ---

    async def detect_evolves_into(
        self, sim_min: float = 0.50, sim_max: float = 0.88,
        batch_size: int = 5000,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> int:
        """Find evolves_into relationships: same entities, similar topic, later claim updates earlier.

        detour: a claim from a later source or later observation that supersedes an earlier one
        about the same entity and numeric context.
        """
        sql = """
        WITH entity_pairs AS (
            SELECT DISTINCT ce1.claim_id AS cid_a, ce2.claim_id AS cid_b
            FROM claim_entity ce1
            JOIN claim_entity ce2 ON ce2.entity_id = ce1.entity_id AND ce2.claim_id > ce1.claim_id
        )
        SELECT ep.cid_a, ep.cid_b,
               1 - (cea.embedding <=> ceb.embedding) AS similarity,
               ca.extracted_at AS time_a, cb.extracted_at AS time_b
        FROM entity_pairs ep
        JOIN claim_embedding cea ON cea.claim_id = ep.cid_a AND cea.model_name = $1
        JOIN claim_embedding ceb ON ceb.claim_id = ep.cid_b AND ceb.model_name = $1
        JOIN claim ca ON ca.claim_id = ep.cid_a
        JOIN claim cb ON cb.claim_id = ep.cid_b
        WHERE 1 - (cea.embedding <=> ceb.embedding) BETWEEN $2 AND $3
          AND ca.extracted_at != cb.extracted_at
          AND NOT EXISTS (
              SELECT 1 FROM claim_relationship cr
              WHERE ((cr.source_claim_id=ep.cid_a AND cr.target_claim_id=ep.cid_b)
                  OR (cr.source_claim_id=ep.cid_b AND cr.target_claim_id=ep.cid_a))
                AND cr.relationship_type IN ('contradicts', 'repeated_by')
          )
        ORDER BY similarity DESC
        LIMIT $4
        """
        sql_pair = """
        SELECT ca.claim_id AS cid_a, ca.canonical_text AS text_a, ca.extracted_at AS time_a,
               cb.claim_id AS cid_b, cb.canonical_text AS text_b, cb.extracted_at AS time_b
        FROM (SELECT * FROM claim WHERE claim_id = $1) ca
        CROSS JOIN (SELECT * FROM claim WHERE claim_id = $2) cb
        """
        sql_skip = """
        SELECT 1 FROM claim_relationship
        WHERE ((source_claim_id=$1 AND target_claim_id=$2)
           OR (source_claim_id=$2 AND target_claim_id=$1))
          AND relationship_type IN ('evolves_into', 'contradicts', 'repeated_by')
        LIMIT 1
        """
        async with self._pool.acquire() as conn:
            candidates = await conn.fetch(sql, model_name, sim_min, sim_max, batch_size)
        if not candidates:
            return 0
        count = 0
        for row in candidates:
            ca, cb = row["cid_a"], row["cid_b"]
            similarity = float(row["similarity"])
            time_a, time_b = row["time_a"], row["time_b"]
            async with self._pool.acquire() as conn:
                pair_row = await conn.fetchrow(sql_pair, ca, cb)
            if not pair_row:
                continue
            a_text, b_text = pair_row["text_a"], pair_row["text_b"]
            if a_text == b_text:
                continue
            if self._is_html_like(a_text) or self._is_html_like(b_text):
                continue
            num_a = self._extract_meaningful_numbers(a_text)
            num_b = self._extract_meaningful_numbers(b_text)
            if not num_a and not num_b:
                continue
            has_overlap = bool(set(num_a) & set(num_b))
            has_diff = bool(set(num_a) ^ set(num_b))
            if not has_overlap or not has_diff:
                continue
            older = ca if time_a < time_b else cb
            newer = cb if time_a < time_b else ca
            async with self._pool.acquire() as conn:
                skip = await conn.fetchval(sql_skip, older, newer)
                if skip:
                    continue
                evidence = f"Numeric evolution (sim={similarity:.2f})"
                rel = ClaimRelationship(
                    source_claim_id=older, target_claim_id=newer,
                    relationship_type="evolves_into",
                    confidence=round(similarity, 3),
                    detected_by="rule",
                    evidence=evidence,
                )
                await self.insert_relationship(rel)
                count += 1
        return count

    # --- Layer 3d: derived_from (textual reuse) ---

    async def detect_derived_from(
        self, ngram_threshold: float = 0.30, batch_size: int = 5000,
    ) -> int:
        """Find derived_from: significant n-gram overlap suggesting one source quoted another."""
        sql_claims = """
        SELECT claim_id, canonical_text, source_id, extracted_at
        FROM claim
        ORDER BY extracted_at
        """
        async with self._pool.acquire() as conn:
            all_claims = await conn.fetch(sql_claims)
        if len(all_claims) < 2:
            return 0
        count = 0
        checked: set[tuple[int, int]] = set()
        for i in range(len(all_claims)):
            if count >= batch_size:
                break
            ca = all_claims[i]
            words_a = set(ca["canonical_text"].lower().split())
            if len(words_a) < 10:
                continue
            for j in range(i + 1, len(all_claims)):
                if count >= batch_size:
                    break
                cb = all_claims[j]
                if ca["source_id"] == cb["source_id"]:
                    continue
                pair_key = (ca["claim_id"], cb["claim_id"])
                if pair_key in checked:
                    continue
                checked.add(pair_key)
                words_b = set(cb["canonical_text"].lower().split())
                if len(words_b) < 10:
                    continue
                intersection = words_a & words_b
                overlap = len(intersection) / min(len(words_a), len(words_b))
                if overlap < ngram_threshold:
                    continue
                if len(intersection) < 5:
                    continue
                time_a, time_b = ca["extracted_at"], cb["extracted_at"]
                older_id = ca["claim_id"] if time_a < time_b else cb["claim_id"]
                newer_id = cb["claim_id"] if time_a < time_b else ca["claim_id"]
                async with self._pool.acquire() as conn:
                    skip = await conn.fetchval(
                        "SELECT 1 FROM claim_relationship "
                        "WHERE ((source_claim_id=$1 AND target_claim_id=$2) "
                        "   OR (source_claim_id=$2 AND target_claim_id=$1)) "
                        "AND relationship_type IN ('derived_from', 'repeated_by') LIMIT 1",
                        older_id, newer_id,
                    )
                    if skip:
                        continue
                    rel = ClaimRelationship(
                        source_claim_id=older_id, target_claim_id=newer_id,
                        relationship_type="derived_from",
                        confidence=round(overlap, 3),
                        detected_by="rule",
                        evidence=f"Word overlap: {overlap:.0%} ({len(intersection)} shared words)",
                    )
                    await self.insert_relationship(rel)
                    count += 1
        return count

    # --- Layer 3d: references ---

    async def detect_references(
        self, batch_size: int = 5000,
    ) -> int:
        """Find references: claims that explicitly cite another entity or source.

        detour: when a claim contains phrases like 'according to [Entity]' or '[Entity] said',
        the claim references that entity.
        """
        cite_pattern = re.compile(
            r"(?:according\s+to|said\s+by|reported\s+by|published\s+by|"
            r"as\s+(?:per|reported|stated|noted)\s+by)\s+"
            r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,3})",
            re.I,
        )
        sql_claims = """
        SELECT claim_id, canonical_text, source_id
        FROM claim
        ORDER BY extracted_at
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql_claims)
        count = 0
        for row in rows:
            if count >= batch_size:
                break
            text = row["canonical_text"]
            matches = cite_pattern.findall(text)
            if not matches:
                continue
            for cited_name in matches:
                cited_name = cited_name.strip().removeprefix("The ").removeprefix("A ")
                if len(cited_name.split()) < 2:
                    continue
                async with self._pool.acquire() as conn:
                    eid = await conn.fetchval(
                        "SELECT entity_id FROM entity WHERE LOWER(name) = LOWER($1) LIMIT 1",
                        cited_name,
                    )
                    if not eid:
                        continue
                    crows = await conn.fetch(
                        "SELECT claim_id FROM claim_entity WHERE entity_id=$1 LIMIT 1",
                        eid,
                    )
                    if not crows:
                        continue
                    target = crows[0]["claim_id"]
                    if target == row["claim_id"]:
                        continue
                    skip = await conn.fetchval(
                        "SELECT 1 FROM claim_relationship "
                        "WHERE source_claim_id=$1 AND target_claim_id=$2 "
                        "AND relationship_type='references' LIMIT 1",
                        row["claim_id"], target,
                    )
                    if skip:
                        continue
                    rel = ClaimRelationship(
                        source_claim_id=row["claim_id"],
                        target_claim_id=target,
                        relationship_type="references",
                        confidence=0.8,
                        detected_by="rule",
                        evidence=f"Explicit citation: '{cited_name}'",
                    )
                    await self.insert_relationship(rel)
                    count += 1
        return count

    # --- Layer 3d: run all relationship detection ---

    async def detect_all_relationships(
        self,
        detect_same: bool = True,
        detect_contra: bool = True,
        detect_support: bool = True,
        detect_evolve: bool = True,
        detect_derived: bool = True,
        detect_refs: bool = True,
    ) -> dict[str, int]:
        """Run all configured relationship detectors and return counts per type."""
        results: dict[str, int] = {}
        if detect_same:
            results["repeated_by"] = await self.detect_same_claims(threshold=0.88)
        if detect_contra:
            results["contradicts"] = await self.detect_contradictions(
                sim_threshold=0.40, sim_max=0.98
            )
        if detect_support:
            results["supports"] = await self.detect_supports()
        if detect_evolve:
            results["evolves_into"] = await self.detect_evolves_into()
        if detect_derived:
            results["derived_from"] = await self.detect_derived_from()
        if detect_refs:
            results["references"] = await self.detect_references()
        return results

    # --- Layer 5: Narratives ---

    async def get_all_claim_texts_with_ids(self) -> list[dict]:
        sql = "SELECT claim_id, canonical_text FROM claim ORDER BY extracted_at"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]

    async def detect_narratives(self, k: int = 10, min_claims: int = 5) -> int:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import LatentDirichletAllocation

        claims = await self.get_all_claim_texts_with_ids()
        if len(claims) < k:
            return 0
        texts = [c["canonical_text"] for c in claims]
        vec = TfidfVectorizer(max_features=1000, stop_words="english", max_df=0.85)
        dtm = vec.fit_transform(texts)
        lda = LatentDirichletAllocation(n_components=k, random_state=42, n_jobs=-1)
        topic_dist = lda.fit_transform(dtm)
        feature_names = vec.get_feature_names_out()

        count = 0
        for topic_idx in range(k):
            top_indices = topic_dist[:, topic_idx].argsort()[::-1]
            top_terms_idx = lda.components_[topic_idx].argsort()[::-1][:8]
            top_terms = [feature_names[i] for i in top_terms_idx]
            name = ", ".join(top_terms[:4])
            claim_ids = []
            for ci in top_indices:
                weight = float(topic_dist[ci, topic_idx])
                if weight >= 0.1:
                    claim_ids.append((claims[ci]["claim_id"], weight))
            if len(claim_ids) < min_claims:
                continue
            sql_n = """
            INSERT INTO narrative (name, description, top_terms, claim_count)
            VALUES ($1, $2, $3, $4)
            RETURNING narrative_id
            """
            async with self._pool.acquire() as conn:
                    nid = await conn.fetchval(
                        sql_n, name,
                        f"Auto-detected topic: {', '.join(top_terms)}",
                        top_terms, len(claim_ids)
                    )
            try:
                await self.append_ledger("narrative_assignment", {
                    "narrative_name": name,
                    "top_terms": top_terms,
                    "claim_count": len(claim_ids),
                }, metadata={"narrative_id": str(nid)})
            except Exception:
                pass
            for cid, w in claim_ids:
                sql_c = """
                INSERT INTO narrative_claim (narrative_id, claim_id, weight)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """
                async with self._pool.acquire() as conn:
                    await conn.execute(sql_c, nid, cid, w)
            count += 1
        return count

    async def assign_new_claims_to_narratives(self, threshold: float = 0.50,
                                               batch_size: int = 5000) -> int:
        """Assign claims without narratives to existing narratives using
        embedding centroid similarity. Returns number of claims assigned."""
        async with self._pool.acquire() as conn:
            nar_rows = await conn.fetch("""
                SELECT n.narrative_id, n.name,
                       (SELECT AVG(ce.embedding) FROM claim_embedding ce
                        JOIN narrative_claim nc2 ON nc2.claim_id = ce.claim_id
                        WHERE nc2.narrative_id = n.narrative_id
                          AND ce.model_name = 'all-MiniLM-L6-v2') AS centroid
                FROM narrative n
                WHERE n.narrative_id IN (
                    SELECT nc.narrative_id FROM narrative_claim nc
                    JOIN claim_embedding ce ON ce.claim_id = nc.claim_id
                    WHERE ce.model_name = 'all-MiniLM-L6-v2'
                    LIMIT 1
                )
            """)
            if not nar_rows:
                return 0

            unassigned = await conn.fetch("""
                SELECT c.claim_id FROM claim c
                LEFT JOIN narrative_claim nc ON nc.claim_id = c.claim_id
                WHERE nc.claim_id IS NULL
                LIMIT $1
            """, batch_size)

        assigned = 0
        for row in unassigned:
            cid = row["claim_id"]
            best_nid = None
            best_score = 0.0
            for nar in nar_rows:
                centroid = nar["centroid"]
                if centroid is None:
                    continue
                async with self._pool.acquire() as conn:
                    score = await conn.fetchval("""
                        SELECT 1 - (ce.embedding <=> $1::vector)
                        FROM claim_embedding ce
                        WHERE ce.claim_id = $2 AND ce.model_name = 'all-MiniLM-L6-v2'
                        LIMIT 1
                    """, centroid, cid)
                if score is not None and score > best_score:
                    best_score = score
                    best_nid = nar["narrative_id"]
            if best_nid is not None and best_score >= threshold:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO narrative_claim (narrative_id, claim_id, weight) "
                        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                        best_nid, cid, best_score,
                    )
                assigned += 1
        if assigned:
            try:
                await self.append_ledger("narrative_assignment", {
                    "mode": "incremental_centroid",
                    "assigned_count": assigned,
                    "narratives": [{"id": str(r["narrative_id"]), "name": r["name"]} for r in nar_rows],
                    "threshold": threshold,
                })
            except Exception:
                pass
        return assigned

    async def list_narratives(self, limit: int = 20, topic: str | None = None, offset: int = 0) -> list[dict]:
        topic_clause = "AND (SELECT COUNT(*)::float FROM narrative_claim nc2 JOIN claim c2 ON c2.claim_id = nc2.claim_id WHERE nc2.narrative_id = n.narrative_id AND c2.topic = $3) / NULLIF(n.claim_count, 0) >= 0" if topic else ""
        topic_filter = "AND c.topic = $3" if topic else ""
        topic_filter2 = "AND c2.topic = $3" if topic else ""
        params = [limit, offset]
        if topic:
            params.append(topic)
        sql = f"""
        SELECT n.*,
          (SELECT COUNT(DISTINCT c.source_id)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id {topic_filter}) AS source_count,
          (SELECT MIN(c.extracted_at)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id {topic_filter}) AS first_claim_date,
          (SELECT MAX(c.extracted_at)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id {topic_filter}) AS last_claim_date,
          (SELECT c2.canonical_text
           FROM narrative_claim nc2
           JOIN claim c2 ON c2.claim_id = nc2.claim_id
           WHERE nc2.narrative_id = n.narrative_id {topic_filter2}
           ORDER BY nc2.weight DESC LIMIT 1) AS top_claim_text
        FROM narrative n
        WHERE n.is_active = TRUE {topic_clause}
        ORDER BY n.claim_count DESC
        LIMIT $1 OFFSET $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            result = []
            for r in rows:
                d = dict(r)
                d["narrative_id"] = str(d["narrative_id"])
                result.append(d)
            return result

    async def get_narrative(self, narrative_id: UUID) -> dict | None:
        sql = """
        SELECT n.*,
          (SELECT COUNT(DISTINCT c.source_id)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id) AS source_count,
          (SELECT MIN(c.extracted_at)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id) AS first_claim_date,
          (SELECT MAX(c.extracted_at)
           FROM narrative_claim nc
           JOIN claim c ON c.claim_id = nc.claim_id
           WHERE nc.narrative_id = n.narrative_id) AS last_claim_date,
          (SELECT c2.canonical_text
           FROM narrative_claim nc2
           JOIN claim c2 ON c2.claim_id = nc2.claim_id
           WHERE nc2.narrative_id = n.narrative_id
           ORDER BY nc2.weight DESC LIMIT 1) AS top_claim_text
        FROM narrative n WHERE n.narrative_id = $1
        """
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(sql, narrative_id)
            return dict(r) if r else None

    async def get_narrative_claims(self, narrative_id: UUID, limit: int = 50,
                                    topic: str | None = None) -> list[dict]:
        topic_filter = "AND c.topic = $3" if topic else ""
        params = [narrative_id, limit]
        if topic:
            params.append(topic)
        sql = f"""
        SELECT c.claim_id, c.canonical_text, c.source_url, c.source_id,
               c.extracted_at, c.extraction_confidence, c.context_sentence,
               COALESCE(ss.title, c.source_url) AS source_title,
               nc.weight
        FROM narrative_claim nc
        JOIN claim c ON c.claim_id = nc.claim_id
        LEFT JOIN source_snapshot ss ON ss.source_id = c.source_id
        WHERE nc.narrative_id = $1 {topic_filter}
        ORDER BY nc.weight DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def get_narrative_count(self) -> int:
        sql = "SELECT COUNT(*) FROM narrative WHERE is_active = TRUE"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql)

    async def resolve_narrative_prefix(self, prefix: str) -> UUID | None:
        """Resolve a short UUID prefix to a full narrative UUID.
        Returns None if 0 matches, raises ValueError if ambiguous."""
        if len(prefix) < 3:
            raise ValueError("Prefix must be at least 3 characters")
        sql = "SELECT narrative_id::text FROM narrative WHERE narrative_id::text LIKE $1 || '%' AND is_active = TRUE"
        pattern = prefix.replace("-", "").lower()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, pattern)
        if len(rows) == 0:
            return None
        if len(rows) > 1:
            matches = ", ".join(str(r[0])[:8] for r in rows)
            raise ValueError(f"Ambiguous prefix '{prefix}' matches: {matches}")
        return UUID(rows[0][0])

    async def rename_narrative(self, narrative_id: UUID, name: str, description: str | None = None) -> bool:
        sql = "UPDATE narrative SET name = $1, description = COALESCE($2, description) WHERE narrative_id = $3 AND is_active = TRUE"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, name, description, narrative_id)
            return "UPDATE 1" in result

    async def delete_narrative(self, narrative_id: UUID) -> bool:
        sql = "DELETE FROM narrative_claim WHERE narrative_id = $1"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, narrative_id)
        sql = "DELETE FROM narrative WHERE narrative_id = $1"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, narrative_id)
            return "DELETE 1" in result

    # --- Entity noise cleanup ---

    _NOISE_ENTITIES: set[str] = {
        "this", "that", "these", "those", "here", "there",
        "app", "apps", "error", "errors", "on", "in", "at", "by", "to",
        "for", "with", "from", "into", "onto", "upon",
        "shaping", "action", "plan", "plans", "policy", "policies",
        "strategy", "strategies", "report", "reports", "study", "studies",
        "research", "development", "technology", "digital",
        "global", "international", "national", "federal",
        "public", "private", "social", "economic",
        "new", "old", "first", "last", "next", "previous",
        "may", "june", "july", "august", "january", "february",
        "march", "april", "september", "october", "november", "december",
    }

    async def clean_noise_entities(self, dry_run: bool = True) -> int:
        """Remove entities that match noise patterns (common English words, single generic terms).
        Returns count of entities removed.
        """
        sql_all = """
        SELECT e.entity_id, e.name, COUNT(ce.claim_id) AS claim_count
        FROM entity e
        LEFT JOIN claim_entity ce ON ce.entity_id = e.entity_id
        GROUP BY e.entity_id, e.name
        ORDER BY claim_count DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql_all)

        to_remove = []
        for r in rows:
            name = (r["name"] or "").strip().lower()
            parts = name.split()
            is_noise = False
            if len(parts) == 1 and parts[0] in self._NOISE_ENTITIES:
                is_noise = True
            elif len(parts) <= 2 and all(p in self._NOISE_ENTITIES for p in parts):
                is_noise = True
            if is_noise:
                to_remove.append((r["entity_id"], r["name"]))

        if dry_run:
            return len(to_remove)

        async with self._pool.acquire() as conn:
            for eid, _ in to_remove:
                await conn.execute("DELETE FROM claim_entity WHERE entity_id = $1", eid)
                await conn.execute("DELETE FROM entity WHERE entity_id = $1", eid)
        return len(to_remove)

    # --- Phase 2 Area 3: Confidence Explainability ---

    async def insert_confidence_factor(self, target_type: str, target_id: UUID,
                                        factor_type: str, value: float,
                                        weight: float = 1.0, explanation: str = "") -> None:
        sql = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (target_type, target_id, factor_type) DO UPDATE SET
            value = EXCLUDED.value,
            weight = EXCLUDED.weight,
            explanation = EXCLUDED.explanation,
            computed_at = now()
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, target_type, target_id, factor_type, value, weight, explanation)
        try:
            await self.append_ledger("factor", {
                "target_type": target_type,
                "factor_type": factor_type,
                "value": value,
                "weight": weight,
            }, claim_id=target_id if target_type == "claim" else None,
               source_id=target_id if target_type == "source" else None)
        except Exception:
            pass

    async def get_confidence_factors(self, target_type: str, target_id: UUID) -> list[dict]:
        sql = """
        SELECT factor_id, factor_type, value, weight, explanation, computed_at
        FROM confidence_factor
        WHERE target_type = $1 AND target_id = $2
        ORDER BY factor_type
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, target_type, target_id)
            return [dict(r) for r in rows]

    # --- Phase 2 Area 4: Source Behavior Intelligence ---

    async def update_source_behavior(self, source_id: UUID, delta: dict) -> None:
        sql = """
        INSERT INTO source_behavior (source_id, n_claims, n_contradictions, n_corrections,
            n_original_claims, n_repeated_claims)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (source_id) DO UPDATE SET
            n_claims = source_behavior.n_claims + EXCLUDED.n_claims,
            n_contradictions = source_behavior.n_contradictions + EXCLUDED.n_contradictions,
            n_corrections = source_behavior.n_corrections + EXCLUDED.n_corrections,
            n_original_claims = source_behavior.n_original_claims + EXCLUDED.n_original_claims,
            n_repeated_claims = source_behavior.n_repeated_claims + EXCLUDED.n_repeated_claims,
            last_seen = now(),
            updated_at = now()
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql, source_id,
                delta.get("n_claims", 0),
                delta.get("n_contradictions", 0),
                delta.get("n_corrections", 0),
                delta.get("n_original_claims", 0),
                delta.get("n_repeated_claims", 0),
            )

    async def get_source_raw_text(self, source_id: UUID | str) -> str | None:
        sql = "SELECT raw_text FROM source_snapshot WHERE source_id = $1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, source_id)

    async def get_source_behavior(self, source_id: UUID) -> dict | None:
        sql = "SELECT * FROM source_behavior WHERE source_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, source_id)
            return dict(row) if row else None

    async def get_source_contradiction_rate(self, source_id: UUID) -> float:
        sql = """
        SELECT COUNT(*)::REAL / NULLIF(
            (SELECT COUNT(*) FROM claim WHERE source_id = $1), 0)
        FROM claim_relationship r
        JOIN claim c ON c.claim_id IN (r.source_claim_id, r.target_claim_id)
        WHERE c.source_id = $1 AND r.relationship_type = 'contradicts'
        """
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(sql, source_id)
            return float(val) if val is not None else 0.0

    async def get_source_originality_ratio(self, source_id: UUID) -> float:
        sql = """
        SELECT CASE WHEN COUNT(*) = 0 THEN 0.5
            ELSE SUM(CASE WHEN r.relationship_id IS NULL THEN 1 ELSE 0 END)::REAL / COUNT(*)
            END
        FROM claim c
        LEFT JOIN claim_relationship r ON r.target_claim_id = c.claim_id
            AND r.relationship_type = 'repeated_by'
        WHERE c.source_id = $1
        """
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(sql, source_id)
            return float(val) if val is not None else 0.5

    async def log_source_behavior_event(self, source_id: UUID, event_type: str,
                                         claim_id: UUID | None = None, detail: str | None = None) -> None:
        sql = """
        INSERT INTO source_behavior_event (source_id, event_type, claim_id, detail)
        VALUES ($1, $2, $3, $4)
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, source_id, event_type, claim_id, detail)

    async def get_source_behavior_events(self, source_id: UUID, limit: int = 50) -> list[dict]:
        sql = """
        SELECT event_id, event_type, claim_id, detail, observed_at
        FROM source_behavior_event
        WHERE source_id = $1
        ORDER BY observed_at DESC
        LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id, limit)
            return [dict(r) for r in rows]

    # --- Phase 1b: Source Reliability Scoring ---

    async def _get_source_age_days(self, source_id: UUID) -> int:
        sql = """
        SELECT COALESCE(EXTRACT(DAY FROM now() - MIN(observed_at))::INT, 0)
        FROM claim_observation
        WHERE source_id = $1
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, source_id) or 0

    async def compute_source_reliability(self, source_id: UUID) -> float:
        cr = await self.get_source_contradiction_rate(source_id)
        contradiction_score = max(0.0, 1.0 - (cr * 2.0))

        or_ = await self.get_source_originality_ratio(source_id)

        age_days = await self._get_source_age_days(source_id)
        age_score = min(1.0, 0.5 + (age_days / 60.0))

        composite = 0.4 * contradiction_score + 0.4 * or_ + 0.2 * age_score
        composite = max(0.0, min(1.0, composite))

        await self.insert_confidence_factor("source", source_id,
            "source_reliability", composite, 1.0,
            f"Composite: contra={contradiction_score:.2f}, orig={or_:.2f}, age={age_score:.2f}")

        sql = "UPDATE source_behavior SET reliability_score = $1, updated_at = now() WHERE source_id = $2"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, composite, source_id)

        return composite

    async def get_source_reliability(self, source_id: UUID) -> float:
        row = await self.get_source_behavior(source_id)
        if row and row.get("reliability_score") is not None:
            return row["reliability_score"]
        return await self.compute_source_reliability(source_id)

    # --- Phase 1e: Source Self-Contradiction Detection ---

    async def detect_source_contradictions(self, source_id: UUID) -> int:
        sql = """
        SELECT r.source_claim_id, r.target_claim_id, r.confidence
        FROM claim_relationship r
        JOIN claim c1 ON c1.claim_id = r.source_claim_id AND c1.source_id = $1
        JOIN claim c2 ON c2.claim_id = r.target_claim_id AND c2.source_id = $1
        WHERE r.relationship_type = 'contradicts'
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id)

        if not rows:
            return 0

        n_contradictions = len(rows)
        await self.update_source_behavior(source_id, {"n_contradictions": n_contradictions})

        for row in rows:
            detail = f"Self-contradiction: claim {str(row['source_claim_id'])[:8]} ↔ {str(row['target_claim_id'])[:8]}"
            await self.log_source_behavior_event(source_id, "contradiction", row["source_claim_id"], detail)

        return n_contradictions

    # --- Phase 1d: Claim Originality Classification ---

    async def classify_claim_originality(self, claim_id: UUID, source_id: UUID,
                                          threshold: float = 0.85) -> dict:
        result = {"is_original": True, "propagation_lag_h": None, "match_claim_id": None}

        emb_sql = "SELECT embedding FROM claim_embedding WHERE claim_id = $1 AND model_name = 'all-MiniLM-L6-v2'"
        match_sql = """
        SELECT c.claim_id, c.source_id,
               1 - (ce.embedding <=> $1) AS similarity,
               co.observed_at
        FROM claim_embedding ce
        JOIN claim c ON c.claim_id = ce.claim_id
        JOIN claim_observation co ON co.claim_id = ce.claim_id
        WHERE c.source_id != $2
          AND ce.model_name = 'all-MiniLM-L6-v2'
          AND 1 - (ce.embedding <=> $1) >= $3
        ORDER BY similarity DESC
        LIMIT 1
        """
        earliest_sql = """
        SELECT MIN(co.observed_at) AS earliest_seen
        FROM claim_observation co
        WHERE co.claim_id IN (
            SELECT ce2.claim_id FROM claim_embedding ce2
            JOIN claim c2 ON c2.claim_id = ce2.claim_id
            WHERE c2.source_id != $2
              AND ce2.model_name = 'all-MiniLM-L6-v2'
              AND 1 - (ce2.embedding <=> $1) >= $3
        )
        """
        current_obs_sql = """
        SELECT MIN(observed_at) FROM claim_observation WHERE claim_id = $1
        """

        async with self._pool.acquire() as conn:
            emb = await conn.fetchval(emb_sql, claim_id)
            if not emb:
                return result  # no embedding yet, can't classify

            match = await conn.fetchrow(match_sql, emb, source_id, threshold)
            if not match:
                return result  # truly original

            result["is_original"] = False
            result["match_claim_id"] = match["claim_id"]

            earliest = await conn.fetchval(earliest_sql, emb, source_id, threshold)
            current_obs = await conn.fetchval(current_obs_sql, claim_id)

            if earliest and current_obs:
                lag = (current_obs - earliest).total_seconds() / 3600
                result["propagation_lag_h"] = round(lag, 1)

        # Log behavior event and update counters
        event_type = "original" if result["is_original"] else "repeated"
        detail = f"Original claim (no similar claims found above {threshold:.2f})" if result["is_original"] else (
            f"Similar to claim {result['match_claim_id']} at sim {threshold:.2f}"
        )
        await self.log_source_behavior_event(source_id, event_type, claim_id, detail)

        delta_key = "n_original_claims" if result["is_original"] else "n_repeated_claims"
        await self.update_source_behavior(source_id, {delta_key: 1})

        if not result["is_original"] and result.get("propagation_lag_h") is not None:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    UPDATE source_behavior SET
                        avg_propagation_lag_h = (
                            COALESCE(avg_propagation_lag_h, 0) * (n_repeated_claims - 1) + $1
                        ) / GREATEST(n_repeated_claims, 1),
                        updated_at = now()
                    WHERE source_id = $2
                """, result["propagation_lag_h"], source_id)

        return result

    async def _get_all_source_ids_with_claims(self) -> list[dict]:
        sql = """
        SELECT DISTINCT ss.source_id
        FROM source_snapshot ss
        WHERE EXISTS (SELECT 1 FROM claim c WHERE c.source_id = ss.source_id)
        ORDER BY ss.source_id
        """
        async with self._pool.acquire() as conn:
            return await conn.fetch(sql)

    async def _get_all_claim_ids(self) -> list[dict]:
        sql = "SELECT claim_id FROM claim ORDER BY claim_id"
        async with self._pool.acquire() as conn:
            return await conn.fetch(sql)

    # --- Phase 3a: Source Correction Detection ---

    async def detect_source_corrections(self, source_id: UUID) -> int:
        """Find claims from the same source that share an entity
        and where a later claim contradicts an earlier one."""
        sql = """
        WITH claim_entities AS (
            SELECT ce.claim_id, e.name AS entity_name,
                   MIN(co.observed_at) AS first_obs
            FROM claim_entity ce
            JOIN entity e ON e.entity_id = ce.entity_id
            JOIN claim_observation co ON co.claim_id = ce.claim_id
            WHERE co.source_id = $1
            GROUP BY ce.claim_id, e.name
        ),
        ordered AS (
            SELECT claim_id, entity_name, first_obs,
                   ROW_NUMBER() OVER (
                       PARTITION BY entity_name ORDER BY first_obs
                   ) AS rn
            FROM claim_entities
        )
        SELECT a.claim_id AS earlier_claim,
               b.claim_id AS later_claim,
               a.entity_name
        FROM ordered a
        JOIN ordered b ON b.entity_name = a.entity_name AND b.rn = a.rn + 1
        WHERE EXISTS (
            SELECT 1 FROM claim_relationship r
            WHERE ((r.source_claim_id = a.claim_id AND r.target_claim_id = b.claim_id)
                OR (r.source_claim_id = b.claim_id AND r.target_claim_id = a.claim_id))
              AND r.relationship_type = 'contradicts'
        )
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, source_id)

        for row in rows:
            detail = (
                f"Correction: entity '{row['entity_name']}' "
                f"claim {str(row['earlier_claim'])[:8]} → {str(row['later_claim'])[:8]}"
            )
            await self.log_source_behavior_event(source_id, "correction",
                row["later_claim"], detail)

        n = len(rows)
        if n:
            await self.update_source_behavior(source_id, {"n_corrections": n})

        return n

    # --- Phase 2: Evidence Diversity & Temporal Stability ---

    async def _count_sources_for_claim(self, claim_id: UUID) -> int:
        sql = "SELECT COUNT(DISTINCT source_id) FROM claim_observation WHERE claim_id = $1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id) or 0

    async def _get_source_tlds_for_claim(self, claim_id: UUID) -> set[str]:
        sql = """
        SELECT DISTINCT SUBSTRING(ss.source_url FROM 'https?://([^/]+)') AS domain
        FROM claim_observation co
        JOIN source_snapshot ss ON ss.source_id = co.source_id
        WHERE co.claim_id = $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id)
        tlds = set()
        for r in rows:
            domain = r["domain"] or ""
            parts = domain.split(".")
            if len(parts) >= 2:
                tlds.add(parts[-1])
        return tlds

    async def _get_observation_span_hours(self, claim_id: UUID) -> float:
        sql = """
        SELECT COALESCE(
            EXTRACT(EPOCH FROM MAX(observed_at) - MIN(observed_at)) / 3600.0, 0
        ) FROM claim_observation WHERE claim_id = $1
        """
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(sql, claim_id)
            return float(val) if val is not None else 0.0

    async def _get_observation_count(self, claim_id: UUID) -> int:
        sql = "SELECT COUNT(*) FROM claim_observation WHERE claim_id = $1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id) or 0

    async def _get_claim_age_days(self, claim_id: UUID) -> int:
        sql = """
        SELECT COALESCE(
            EXTRACT(DAY FROM now() - MIN(observed_at))::INT, 0
        ) FROM claim_observation WHERE claim_id = $1
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id) or 0

    async def _get_canonical_versions(self, claim_id: UUID) -> int:
        sql = """
        SELECT COUNT(*) FROM claim_version WHERE claim_id = $1
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id) or 0

    async def _count_contradictions(self, claim_id: UUID) -> int:
        sql = """
        SELECT COUNT(*) FROM claim_relationship
        WHERE (source_claim_id = $1 OR target_claim_id = $1)
          AND relationship_type = 'contradicts'
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id) or 0

    async def compute_evidence_diversity(self, claim_id: UUID) -> float:
        n_sources = await self._count_sources_for_claim(claim_id)
        source_score = min(1.0, n_sources / 5.0)

        tlds = await self._get_source_tlds_for_claim(claim_id)
        geo_score = min(1.0, len(tlds) / 3.0)

        span_hours = await self._get_observation_span_hours(claim_id)
        temporal_span_score = min(1.0, span_hours / 720.0)

        composite = 0.4 * source_score + 0.3 * geo_score + 0.3 * temporal_span_score
        composite = max(0.0, min(1.0, composite))

        await self.insert_confidence_factor("claim", claim_id,
            "evidence_diversity", composite, 0.25,
            f"Sources={n_sources}, TLDs={len(tlds)}, span={span_hours:.0f}h")

        return composite

    async def compute_temporal_stability(self, claim_id: UUID) -> float:
        age_days = await self._get_claim_age_days(claim_id)
        age_score = min(1.0, 0.3 + (age_days / 60.0 * 0.7))

        obs_count = await self._get_observation_count(claim_id)
        obs_score = min(1.0, obs_count / 10.0)

        versions = await self._get_canonical_versions(claim_id)
        version_score = 1.0 / max(1, versions)

        n_contradictions = await self._count_contradictions(claim_id)
        contra_score = 1.0 / (1.0 + n_contradictions)

        composite = 0.3 * age_score + 0.3 * obs_score + 0.2 * version_score + 0.2 * contra_score
        composite = max(0.0, min(1.0, composite))

        await self.insert_confidence_factor("claim", claim_id,
            "temporal_stability", composite, 0.2,
            f"Age={age_days}d, obs={obs_count}, versions={versions}, contra={n_contradictions}")

        return composite

    async def _get_contradictions(self, claim_id: UUID) -> list[dict]:
        sql = """
        SELECT
            CASE WHEN source_claim_id = $1 THEN target_claim_id ELSE source_claim_id END AS other_claim_id
        FROM claim_relationship
        WHERE (source_claim_id = $1 OR target_claim_id = $1)
          AND relationship_type = 'contradicts'
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, claim_id)
            return [dict(r) for r in rows]

    async def _get_source_id(self, claim_id: UUID) -> UUID | None:
        sql = "SELECT source_id FROM claim WHERE claim_id = $1"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, claim_id)

    async def compute_contradiction_impact(self, claim_id: UUID) -> float:
        contrads = await self._get_contradictions(claim_id)
        if not contrads:
            return 1.0

        multipliers = []
        for c in contrads:
            other_source_id = await self._get_source_id(c["other_claim_id"])
            if other_source_id:
                other_reliability = await self.get_source_reliability(other_source_id)
                multiplier = 1.0 - (0.5 * other_reliability)
                multipliers.append(multiplier)

        impact = min(multipliers) if multipliers else 1.0
        impact = max(0.0, min(1.0, impact))

        await self.insert_confidence_factor("claim", claim_id,
            "contradiction_impact", impact, 0.15,
            f"{len(contrads)} contradictions, min multiplier={impact:.2f}")

        return impact

    def _extraction_method_confidence(self, method: str) -> float:
        return {
            "sentence_split": 0.7,
            "spacy_sentencizer": 0.8,
            "llm_extraction": 0.95,
            "manual": 1.0,
        }.get(method, 0.7)

    async def compute_claim_confidence(self, claim_id: UUID) -> dict:
        # Clean up any lingering old scaffolding factors for this claim
        async with self._pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM confidence_factor
                WHERE target_type = 'claim' AND target_id = $1
                  AND factor_type IN ('temporal_precision', 'uncertainty')
            """, claim_id)

        claim = await self.get_claim(claim_id)
        if not claim:
            return {"composite": 0.5, "factors": []}

        src_rel = await self.compute_source_reliability(claim["source_id"])
        await self.insert_confidence_factor("claim", claim_id,
            "source_reliability", src_rel, 0.3,
            f"Source reliability score {src_rel:.2f}")

        ev_div = await self.compute_evidence_diversity(claim_id)

        temp_stab = await self.compute_temporal_stability(claim_id)

        contra_impact = await self.compute_contradiction_impact(claim_id)

        ext_conf = self._extraction_method_confidence(
            claim.get("extraction_method", "sentence_split")
        )
        await self.insert_confidence_factor("claim", claim_id,
            "extraction_method", ext_conf, 0.1,
            f"Extraction method: {claim.get('extraction_method', 'sentence_split')}")

        composite = await self._compute_claim_confidence_weighted(claim_id)

        return {
            "composite": composite,
            "factors": await self.get_confidence_factors("claim", claim_id),
        }

    async def _compute_claim_confidence_weighted(self, claim_id: UUID) -> float:
        factors = await self.get_confidence_factors("claim", claim_id)
        if not factors:
            return 0.5
        total_weight = sum(f["weight"] for f in factors)
        if total_weight == 0:
            return 0.5
        return sum(f["value"] * f["weight"] for f in factors) / total_weight

    async def clear_old_confidence_factors(self) -> int:
        """Remove legacy scaffolding factors (temporal_precision, uncertainty)
        that were replaced by the 5-factor confidence system.
        Returns number of deleted rows."""
        async with self._pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM confidence_factor
                WHERE target_type = 'claim'
                  AND factor_type IN ('temporal_precision', 'uncertainty')
            """)
            count = int(result.split()[-1]) if result else 0
        return count

    async def bulk_compute_confidence(self, batch_size: int = 5000,
                                      skip_source_reliability: bool = False) -> dict:
        """Compute confidence factors for ALL claims using bulk SQL.
        Returns summary stats."""
        # Clean up legacy scaffolding factors before recomputing
        cleared = await self.clear_old_confidence_factors()
        if cleared:
            print(f"  Cleared {cleared} old scaffolding factors")
        total_claims = 0
        total_inserted = 0

        # 1. Source reliability factors — recompute for every source first
        if not skip_source_reliability:
            src_rows = await self._get_all_source_ids_with_claims()
            for row in src_rows:
                await self.compute_source_reliability(row["source_id"])
                total_claims += 1

        # 2. Evidence diversity — bulk INSERT
        sql_evidence = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation, computed_at)
        SELECT
            'claim',
            claim_id,
            'evidence_diversity',
            LEAST(1.0, GREATEST(0.0,
                COALESCE(src_cnt, 0)::REAL / 5.0 * 0.4 +
                COALESCE(tld_cnt, 0)::REAL / 3.0 * 0.3 +
                LEAST(1.0, COALESCE(span_h, 0) / 720.0) * 0.3
            )),
            0.25,
            'Sources=' || COALESCE(src_cnt, 0) || ', TLDs=' || COALESCE(tld_cnt, 0) || ', span=' || COALESCE(ROUND(span_h::numeric), 0) || 'h',
            now()
        FROM (
            SELECT
                c.claim_id,
                COUNT(DISTINCT co.source_id) AS src_cnt,
                COUNT(DISTINCT SPLIT_PART(COALESCE(SUBSTRING(ss.source_url FROM 'https?://([^/]+)'), ''), '.', -1)) AS tld_cnt,
                EXTRACT(EPOCH FROM MAX(co.observed_at) - MIN(co.observed_at)) / 3600.0 AS span_h
            FROM claim c
            LEFT JOIN claim_observation co ON co.claim_id = c.claim_id
            LEFT JOIN source_snapshot ss ON ss.source_id = co.source_id
            GROUP BY c.claim_id
        ) sub
        ON CONFLICT (target_type, target_id, factor_type)
        DO UPDATE SET value = EXCLUDED.value, explanation = EXCLUDED.explanation, computed_at = now()
        """
        await self._bulk_insert_confidence(sql_evidence)

        # 3. Temporal stability — bulk INSERT
        sql_temporal = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation, computed_at)
        SELECT
            'claim',
            claim_id,
            'temporal_stability',
            LEAST(1.0, GREATEST(0.0,
                LEAST(1.0, 0.3 + COALESCE(c_age, 0)::REAL / 60.0 * 0.7) * 0.3 +
                LEAST(1.0, COALESCE(obs_cnt, 0)::REAL / 10.0) * 0.3 +
                (1.0 / GREATEST(1, COALESCE(ver_cnt, 0))) * 0.2 +
                (1.0 / (1.0 + COALESCE(contra_cnt, 0))) * 0.2
            )),
            0.2,
            'Age=' || COALESCE(c_age, 0) || 'd, obs=' || COALESCE(obs_cnt, 0) || ', versions=' || COALESCE(ver_cnt, 0) || ', contra=' || COALESCE(contra_cnt, 0),
            now()
        FROM (
            SELECT
                c.claim_id,
                EXTRACT(DAY FROM now() - MIN(co.observed_at))::INT AS c_age,
                COUNT(co.observed_at) AS obs_cnt,
                (SELECT COUNT(*) FROM claim_version cv WHERE cv.claim_id = c.claim_id) AS ver_cnt,
                (SELECT COUNT(*) FROM claim_relationship cr
                 WHERE (cr.source_claim_id = c.claim_id OR cr.target_claim_id = c.claim_id)
                   AND cr.relationship_type = 'contradicts') AS contra_cnt
            FROM claim c
            LEFT JOIN claim_observation co ON co.claim_id = c.claim_id
            GROUP BY c.claim_id
        ) sub
        ON CONFLICT (target_type, target_id, factor_type)
        DO UPDATE SET value = EXCLUDED.value, explanation = EXCLUDED.explanation, computed_at = now()
        """
        await self._bulk_insert_confidence(sql_temporal)

        # 4. Contradiction impact — bulk INSERT
        sql_contra = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation, computed_at)
        SELECT
            'claim',
            claim_id,
            'contradiction_impact',
            CASE WHEN n_contra > 0 THEN
                (SELECT LEAST(1.0, GREATEST(0.0, 1.0 - 0.5 * AVG(COALESCE(sb.reliability_score, 0.5))))
                 FROM claim_relationship cr2
                 JOIN claim c2 ON c2.claim_id = CASE WHEN cr2.source_claim_id = base.claim_id THEN cr2.target_claim_id ELSE cr2.source_claim_id END
                 LEFT JOIN source_behavior sb ON sb.source_id = c2.source_id
                 WHERE (cr2.source_claim_id = base.claim_id OR cr2.target_claim_id = base.claim_id)
                   AND cr2.relationship_type = 'contradicts')
            ELSE 1.0 END,
            0.15,
            CASE WHEN n_contra > 0 THEN n_contra || ' contradictions' ELSE 'No contradictions' END,
            now()
        FROM (
            SELECT c.claim_id,
                (SELECT COUNT(*) FROM claim_relationship cr
                 WHERE (cr.source_claim_id = c.claim_id OR cr.target_claim_id = c.claim_id)
                   AND cr.relationship_type = 'contradicts') AS n_contra
            FROM claim c
        ) base
        ON CONFLICT (target_type, target_id, factor_type)
        DO UPDATE SET value = EXCLUDED.value, explanation = EXCLUDED.explanation, computed_at = now()
        """
        await self._bulk_insert_confidence(sql_contra)

        # 5. Extraction method — bulk INSERT
        sql_extract = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation, computed_at)
        SELECT
            'claim',
            claim_id,
            'extraction_method',
            CASE COALESCE(extraction_method, 'sentence_split')
                WHEN 'sentence_split' THEN 0.7
                WHEN 'spacy_sentencizer' THEN 0.8
                WHEN 'llm_extraction' THEN 0.95
                WHEN 'manual' THEN 1.0
                ELSE 0.7
            END,
            0.1,
            'Extraction method: ' || COALESCE(extraction_method, 'sentence_split'),
            now()
        FROM claim
        ON CONFLICT (target_type, target_id, factor_type)
        DO UPDATE SET value = EXCLUDED.value, explanation = EXCLUDED.explanation, computed_at = now()
        """
        await self._bulk_insert_confidence(sql_extract)

        # 6. Source reliability per-claim — propagate from source-level reliability
        sql_src_rel = """
        INSERT INTO confidence_factor (target_type, target_id, factor_type, value, weight, explanation, computed_at)
        SELECT
            'claim',
            c.claim_id,
            'source_reliability',
            COALESCE(sb.reliability_score, 0.5),
            0.3,
            'Source reliability score ' || ROUND(COALESCE(sb.reliability_score, 0.5)::numeric, 2),
            now()
        FROM claim c
        LEFT JOIN source_behavior sb ON sb.source_id = c.source_id
        ON CONFLICT (target_type, target_id, factor_type)
        DO UPDATE SET value = EXCLUDED.value, explanation = EXCLUDED.explanation, computed_at = now()
        """
        await self._bulk_insert_confidence(sql_src_rel)

        return {
            "total_claims": total_claims,
            "factors_inserted": 5,
        }

    async def _bulk_insert_confidence(self, sql: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql)
            return int(result.split()[-1]) if result else 0

    async def get_stats(self) -> dict:
        sql = """
        SELECT
            (SELECT COUNT(*) FROM source_snapshot) AS total_sources,
            (SELECT COUNT(*) FROM source_ingested) AS total_events,
            (SELECT COUNT(*) FROM source_snapshot
             WHERE first_seen_at != last_updated_at) AS updated_sources,
            (SELECT MIN(first_seen_at) FROM source_snapshot) AS oldest_source,
            (SELECT MAX(last_updated_at) FROM source_snapshot) AS newest_source,
            (SELECT COUNT(*) FROM claim) AS total_claims,
            (SELECT COUNT(*) FROM claim_observation) AS total_observations,
            (SELECT COUNT(*) FROM entity) AS total_entities,
            (SELECT COUNT(*) FROM claim_embedding) AS total_embeddings,
            (SELECT COUNT(*) FROM claim_relationship) AS total_relationships,
            (SELECT COUNT(*) FROM claim_relationship WHERE relationship_type = 'repeated_by') AS total_repeated_by,
            (SELECT COUNT(*) FROM claim_relationship WHERE relationship_type = 'contradicts') AS total_contradictions,
            (SELECT COUNT(*) FROM narrative WHERE is_active = TRUE) AS total_narratives,
            (SELECT COUNT(*) FROM source_ingested
             WHERE ingested_at > now() - interval '1 hour') AS rate_1h,
            (SELECT COUNT(*) FROM source_ingested
             WHERE ingested_at > now() - interval '24 hours') AS rate_24h,
            (SELECT COUNT(*) FROM source_snapshot WHERE publish_date IS NOT NULL) AS sources_with_date,
            (SELECT COUNT(*) FROM source_snapshot WHERE author IS NOT NULL) AS sources_with_author,
            (SELECT COUNT(*) FROM source_snapshot WHERE metadata->>'language' IS NOT NULL OR metadata->'head_meta'->>'language' IS NOT NULL) AS sources_with_language
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql)
            return dict(row)

    async def get_domain_stats(self, limit: int = 10) -> list[tuple[str, int]]:
        sql = """
        SELECT SUBSTRING(source_url FROM '://([^/]+)') AS domain, COUNT(*) AS cnt
        FROM source_snapshot
        GROUP BY domain
        ORDER BY cnt DESC
        LIMIT $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [(r["domain"], r["cnt"]) for r in rows]

    async def get_language_stats(self, limit: int = 10) -> list[tuple[str, int]]:
        sql = """
        SELECT COALESCE(
            NULLIF(ss.metadata->>'language', ''),
            NULLIF(ss.metadata->'head_meta'->>'language', '')
        ) AS lang, COUNT(*) AS cnt
        FROM source_snapshot ss
        WHERE ss.metadata IS NOT NULL
          AND (ss.metadata->>'language' IS NOT NULL OR ss.metadata->'head_meta'->>'language' IS NOT NULL)
        GROUP BY lang
        ORDER BY cnt DESC
        LIMIT $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit)
            return [(r["lang"], r["cnt"]) for r in rows]

    async def backfill_claim_topics(self, topic: str = "ai") -> int:
        sql = "UPDATE claim SET topic = $1 WHERE topic IS NULL"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, topic)
            count = int(result.split()[-1]) if result else 0
        # Also update the version table
        sql_v = "UPDATE claim_version SET topic = $1 WHERE topic IS NULL"
        async with self._pool.acquire() as conn:
            await conn.execute(sql_v, topic)
        return count

    async def get_topic_stats(self, topic: str) -> dict:
        sql = """
        SELECT
            (SELECT COUNT(*) FROM claim WHERE topic = $1) AS total_claims,
            (SELECT COUNT(DISTINCT c.source_id) FROM claim c WHERE c.topic = $1) AS total_sources,
            (SELECT COUNT(DISTINCT nc.narrative_id)
             FROM narrative_claim nc
             JOIN claim c ON c.claim_id = nc.claim_id
             WHERE c.topic = $1) AS total_narratives,
            (SELECT COUNT(*) FROM claim_relationship cr
             WHERE cr.relationship_type = 'contradicts'
             AND (EXISTS (SELECT 1 FROM claim c WHERE c.claim_id = cr.source_claim_id AND c.topic = $1)
                  OR EXISTS (SELECT 1 FROM claim c WHERE c.claim_id = cr.target_claim_id AND c.topic = $1))
            ) AS total_contradictions
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, topic)
            return dict(row)

    async def search_global(self, q: str, limit: int = 10, topic: str | None = None) -> list[dict]:
        """Full-text search across claims, sources, entities, and narratives."""
        results = []
        pattern = f"%{q}%"
        topic_filter = " AND topic = $3" if topic else ""
        async with self._pool.acquire() as conn:
            sources = await conn.fetch(
                "SELECT source_id, title, source_url, 'source' AS result_type, "
                "  ts_rank(to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(raw_text,'')), plainto_tsquery('english', $1)) AS rank "
                "FROM source_snapshot "
                "WHERE to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(raw_text,'')) @@ plainto_tsquery('english', $1) "
                "ORDER BY rank DESC LIMIT $2",
                q, limit,
            )
            for r in sources:
                results.append({"type": "source", "id": str(r["source_id"]),
                                "title": r["title"] or r["source_url"], "rank": float(r["rank"])})

            if topic:
                claims = await conn.fetch(
                    "SELECT claim_id, canonical_text FROM claim "
                    "WHERE canonical_text ILIKE $1 AND topic = $2 LIMIT $3",
                    pattern, topic, limit,
                )
            else:
                claims = await conn.fetch(
                    "SELECT claim_id, canonical_text FROM claim "
                    "WHERE canonical_text ILIKE $1 LIMIT $2",
                    pattern, limit,
                )
            for r in claims:
                results.append({"type": "claim", "id": str(r["claim_id"]),
                                "title": r["canonical_text"][:150], "rank": 0.5})

            entities = await conn.fetch(
                "SELECT entity_id, name FROM entity WHERE name ILIKE $1 LIMIT $2",
                pattern, limit,
            )
            for r in entities:
                results.append({"type": "entity", "id": str(r["entity_id"]),
                                "title": r["name"], "rank": 0.3})

            narratives = await conn.fetch(
                "SELECT narrative_id, name FROM narrative WHERE name ILIKE $1 OR description ILIKE $1 LIMIT $2",
                pattern, limit,
            )
            for r in narratives:
                results.append({"type": "narrative", "id": str(r["narrative_id"]),
                                "title": r["name"], "rank": 0.4})

        results.sort(key=lambda x: -x["rank"])
        return results[:limit]

    async def detect_claim_mutations(self, claim_id: UUID) -> list[dict]:
        """Detect mutations between successive claim versions. Returns list of mutation events."""
        versions_sql = """
        SELECT version_id, version, canonical_text, original_text, extracted_at
        FROM claim_version WHERE claim_id = $1 ORDER BY version
        """
        async with self._pool.acquire() as conn:
            versions = await conn.fetch(versions_sql, claim_id)

        if len(versions) < 2:
            return []

        mutations = []
        for i in range(1, len(versions)):
            prev = versions[i - 1]
            curr = versions[i]
            ptext = (prev["original_text"] or prev["canonical_text"] or "")
            ctext = (curr["original_text"] or curr["canonical_text"] or "")

            if not ptext or not ctext:
                continue

            max_len = max(len(ptext), len(ctext))
            if max_len == 0:
                continue

            n_chars = sum(1 for a, b in zip(ptext, ctext) if a != b) + abs(len(ptext) - len(ctext))
            edit_ratio = n_chars / max_len

            if edit_ratio < 0.1:
                mtype = "refinement"
            elif edit_ratio < 0.4:
                mtype = "revision"
            elif edit_ratio >= 0.7:
                mtype = "contradiction"
            else:
                mtype = "expansion"

            mutations.append({
                "from_version": prev["version"],
                "to_version": curr["version"],
                "type": mtype,
                "edit_ratio": round(edit_ratio, 3),
                "from_extracted_at": prev["extracted_at"],
                "to_extracted_at": curr["extracted_at"],
            })

        return mutations

    async def get_claim_timeline(self, claim_id: UUID) -> dict:
        """Return timeline of observations, behavior events, and confidence for a claim."""
        async with self._pool.acquire() as conn:
            obs = await conn.fetch(
                "SELECT co.observed_at, co.observer, co.context, "
                "  ss.source_url, ss.title AS source_title "
                "FROM claim_observation co "
                "LEFT JOIN source_snapshot ss ON ss.source_id = co.source_id "
                "WHERE co.claim_id = $1 "
                "ORDER BY co.observed_at",
                claim_id,
            )

            events = await conn.fetch(
                "SELECT sbe.event_type, sbe.detail, sbe.observed_at "
                "FROM source_behavior_event sbe "
                "JOIN claim c ON c.source_id = sbe.source_id "
                "WHERE c.claim_id = $1 "
                "ORDER BY sbe.observed_at",
                claim_id,
            )

            factors = await conn.fetch(
                "SELECT factor_type, value, weight, explanation, computed_at "
                "FROM confidence_factor "
                "WHERE target_type = 'claim' AND target_id = $1 "
                "ORDER BY factor_type",
                claim_id,
            )

        return {
            "claim_id": claim_id,
            "observations": [dict(r) for r in obs],
            "behavior_events": [dict(r) for r in events],
            "confidence_factors": [dict(r) for r in factors],
        }

    # ── Ledger: Immutable hash chain ──────────────────────────────────────────

    _GENESIS_HASH = hashlib.sha256(b"helioryn-genesis-block-v1").hexdigest()

    async def _compute_previous_hash(self, conn) -> str:
        """Get the hash of the last ledger entry, or genesis hash if empty."""
        row = await conn.fetchval("SELECT data_hash FROM ledger ORDER BY id DESC LIMIT 1")
        return row if row else self._GENESIS_HASH

    async def append_ledger(self, entry_type: str, data: dict,
                            claim_id: UUID | None = None,
                            source_id: UUID | None = None,
                            metadata: dict | None = None) -> dict | None:
        """Append an entry to the immutable hash chain. Returns the entry dict."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        data_hash = hashlib.sha256(serialized.encode()).hexdigest()

        async with self._pool.acquire() as conn:
            previous_hash = await self._compute_previous_hash(conn)
            row = await conn.fetchrow(
                "INSERT INTO ledger (entry_type, claim_id, source_id, data_hash, previous_hash, metadata) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, created_at",
                entry_type, claim_id, source_id, data_hash, previous_hash,
                json.dumps(metadata or {}),
            )
            if row:
                return {
                    "id": row["id"],
                    "entry_type": entry_type,
                    "claim_id": str(claim_id) if claim_id else None,
                    "source_id": str(source_id) if source_id else None,
                    "data_hash": data_hash,
                    "previous_hash": previous_hash,
                    "metadata": metadata or {},
                    "created_at": row["created_at"],
                }
        return None

    async def get_chain(self, claim_id: UUID | None = None,
                        source_id: UUID | None = None,
                        limit: int = 1000) -> list[dict]:
        """Retrieve ledger entries in ascending order, optionally filtered."""
        if claim_id:
            sql = "SELECT * FROM ledger WHERE claim_id = $1 ORDER BY id ASC LIMIT $2"
            params = (claim_id, limit)
        elif source_id:
            sql = "SELECT * FROM ledger WHERE source_id = $1 ORDER BY id ASC LIMIT $2"
            params = (source_id, limit)
        else:
            sql = "SELECT * FROM ledger ORDER BY id ASC LIMIT $1"
            params = (limit,)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def verify_chain(self, claim_id: UUID | None = None,
                           source_id: UUID | None = None) -> list[dict]:
        """Walk the hash chain and verify integrity.
        Checks that each entry's previous_hash matches the preceding entry's data_hash.
        Returns list of (id, entry_type, valid, errors)."""
        entries = await self.get_chain(claim_id=claim_id, source_id=source_id, limit=10000)
        if not entries:
            return []

        results = []
        expected_previous = self._GENESIS_HASH

        for entry in entries:
            errors = []
            if entry["previous_hash"] != expected_previous:
                errors.append(
                    f"previous_hash mismatch: expected {expected_previous[:20]}..., "
                    f"got {entry['previous_hash'][:20]}..."
                )
            expected_previous = entry["data_hash"]

            results.append({
                "id": entry["id"],
                "entry_type": entry["entry_type"],
                "valid": len(errors) == 0,
                "errors": errors,
            })

        return results

    async def export_chain(self, claim_id: UUID) -> str:
        """Export a human-readable audit trail for a claim."""
        entries = await self.get_chain(claim_id=claim_id, limit=10000)
        lines = [
            "═" * 72,
            f"  Helioryn Evidence Audit Trail — Claim {claim_id}",
            "═" * 72,
            f"  Generated: {datetime.utcnow().isoformat()}Z",
            f"  Chain length: {len(entries)} entries",
            "═" * 72,
        ]

        for entry in entries:
            lines.append("")
            lines.append(f"  Entry #{entry['id']}")
            lines.append(f"  Type:     {entry['entry_type']}")
            lines.append(f"  Created:  {entry['created_at'].isoformat() if hasattr(entry['created_at'], 'isoformat') else entry['created_at']}")
            lines.append(f"  DataHash: {entry['data_hash'][:20]}...")
            lines.append(f"  PrevHash: {entry['previous_hash'][:20]}...")
            if entry["metadata"]:
                meta = json.dumps(entry["metadata"], indent=2, default=str)
                lines.append(f"  Meta:     {meta}")
            lines.append("  ─" * 36)

        return "\n".join(lines)

    async def ledger_status(self) -> dict:
        """Return ledger health statistics."""
        async with self._pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM ledger")
            by_type = await conn.fetch(
                "SELECT entry_type, COUNT(*) AS cnt FROM ledger GROUP BY entry_type ORDER BY cnt DESC"
            )
            latest = await conn.fetchval("SELECT created_at FROM ledger ORDER BY id DESC LIMIT 1")
            broken = await self.verify_chain()
            return {
                "total_entries": total,
                "by_type": {r["entry_type"]: r["cnt"] for r in by_type},
                "latest_entry": latest.isoformat() if latest else None,
                "broken_links": sum(1 for r in broken if not r["valid"]),
            }

    async def detect_cross_claim_mutations(self, limit: int = 50) -> int:
        """Detect mutations between claims in the same canonical group.
        Processes top-K groups by source count. Returns number of new mutations found."""
        import editdistance

        async with self._pool.acquire() as conn:
            canon_rows = await conn.fetch(
                "SELECT canonical_id FROM canonical_claim ORDER BY n_sources DESC LIMIT $1",
                limit,
            )

        total_mutations = 0
        for cr in canon_rows:
            cid = cr["canonical_id"]
            async with self._pool.acquire() as conn:
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
                            exists = await conn.fetchval(
                                "SELECT 1 FROM claim_mutation WHERE source_claim_id = $1 AND target_claim_id = $2",
                                a["claim_id"], b["claim_id"],
                            )
                            if not exists:
                                await conn.execute(
                                    "INSERT INTO claim_mutation (source_claim_id, target_claim_id, canonical_id, "
                                    "mutation_type, edit_distance, embedding_similarity, detected_by) "
                                    "VALUES ($1, $2, $3, $4, $5, "
                                    "  COALESCE((SELECT 1 - (e1.embedding <=> e2.embedding) "
                                    "            FROM claim_embedding e1, claim_embedding e2 "
                                    "            WHERE e1.claim_id = $1 AND e2.claim_id = $2 "
                                    "              AND e1.model_name = e2.model_name LIMIT 1), 0.0), "
                                    "'rule')",
                                    a["claim_id"], b["claim_id"], cid, "paraphrase", norm_dist,
                                )
                                total_mutations += 1
        return total_mutations

    # ── User Authentication ──────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = hashlib.sha256(password.encode()).hexdigest()[:16]
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex() + ":" + salt

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        if ":" not in stored:
            return False
        hash_part, salt = stored.rsplit(":", 1)
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex() == hash_part

    async def bootstrap_admin(self, admin_password: str):
        """Create default admin user if no users exist yet."""
        async with self._pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM app_user")
            if count == 0:
                pw_hash = self._hash_password(admin_password)
                await conn.execute(
                    "INSERT INTO app_user (username, password_hash, role) VALUES ($1, $2, $3) "
                    "ON CONFLICT (username) DO NOTHING",
                    "admin", pw_hash, "admin",
                )

    async def create_user(self, username: str, password: str, role: str = "viewer") -> dict | None:
        pw_hash = self._hash_password(password)
        sql = """
        INSERT INTO app_user (username, password_hash, role)
        VALUES ($1, $2, $3)
        ON CONFLICT (username) DO NOTHING
        RETURNING user_id, username, role, created_at
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, username, pw_hash, role)
            return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> dict | None:
        sql = "SELECT user_id, username, password_hash, role, created_at FROM app_user WHERE username = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, username)
            return dict(row) if row else None

    async def list_users(self) -> list[dict]:
        sql = "SELECT user_id, username, role, created_at, updated_at FROM app_user ORDER BY username"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]

    async def change_password(self, username: str, new_password: str) -> bool:
        pw_hash = self._hash_password(new_password)
        sql = "UPDATE app_user SET password_hash = $1, updated_at = now() WHERE username = $2"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, pw_hash, username)
            return "UPDATE 1" in result

    async def change_user_role(self, username: str, new_role: str) -> bool:
        sql = "UPDATE app_user SET role = $1, updated_at = now() WHERE username = $2"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, new_role, username)
            return "UPDATE 1" in result

    async def delete_user(self, username: str) -> bool:
        sql = "DELETE FROM app_user WHERE username = $1"
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, username)
            return "DELETE 1" in result

    async def user_count(self) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM app_user")

    # ── Projects ─────────────────────────────────────────────────

    async def create_project(self, user_id: UUID, name: str, description: str = "") -> dict | None:
        sql = """
        INSERT INTO project (user_id, name, description)
        VALUES ($1, $2, $3)
        RETURNING project_id, user_id, name, description, created_at, updated_at
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, name, description)
            return dict(row) if row else None

    async def list_projects(self, user_id: UUID) -> list[dict]:
        sql = """
        SELECT p.project_id, p.name, p.description, p.created_at, p.updated_at,
               (SELECT COUNT(*) FROM chat_session cs WHERE cs.project_id = p.project_id)::int AS session_count
        FROM project p
        WHERE p.user_id = $1
        ORDER BY p.updated_at DESC
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, user_id)
            return [dict(r) for r in rows]

    async def get_project(self, project_id: UUID, user_id: UUID) -> dict | None:
        sql = "SELECT * FROM project WHERE project_id = $1 AND user_id = $2"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, project_id, user_id)
            return dict(row) if row else None

    async def update_project(self, project_id: UUID, user_id: UUID, name: str, description: str | None = None) -> bool:
        if description is not None:
            sql = "UPDATE project SET name = $1, description = $2, updated_at = now() WHERE project_id = $3 AND user_id = $4"
            async with self._pool.acquire() as conn:
                r = await conn.execute(sql, name, description, project_id, user_id)
                return "UPDATE 1" in r
        else:
            sql = "UPDATE project SET name = $1, updated_at = now() WHERE project_id = $2 AND user_id = $3"
            async with self._pool.acquire() as conn:
                r = await conn.execute(sql, name, project_id, user_id)
                return "UPDATE 1" in r

    async def delete_project(self, project_id: UUID, user_id: UUID) -> bool:
        sql = "DELETE FROM project WHERE project_id = $1 AND user_id = $2"
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, project_id, user_id)
            return "DELETE 1" in r

    # ── Chat Sessions ────────────────────────────────────────────

    async def create_chat_session(self, user_id: UUID, project_id: UUID | None = None,
                                   mode: str = "public", title: str = "New Chat") -> dict | None:
        sql = """
        INSERT INTO chat_session (user_id, project_id, mode, title)
        VALUES ($1, $2, $3, $4)
        RETURNING session_id, user_id, project_id, title, mode, messages, created_at, updated_at
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, project_id, mode, title)
            return dict(row) if row else None

    async def list_chat_sessions(self, user_id: UUID, project_id: UUID | None = None,
                                  limit: int = 50) -> list[dict]:
        if project_id:
            sql = """
            SELECT session_id, user_id, project_id, title, mode, created_at, updated_at
            FROM chat_session
            WHERE user_id = $1 AND project_id = $2
            ORDER BY updated_at DESC
            LIMIT $3
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, user_id, project_id, limit)
                return [dict(r) for r in rows]
        else:
            sql = """
            SELECT session_id, user_id, project_id, title, mode, created_at, updated_at
            FROM chat_session
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, user_id, limit)
                return [dict(r) for r in rows]

    async def get_chat_session(self, session_id: UUID, user_id: UUID) -> dict | None:
        sql = "SELECT * FROM chat_session WHERE session_id = $1 AND user_id = $2"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, session_id, user_id)
            return dict(row) if row else None

    async def update_chat_session(self, session_id: UUID, user_id: UUID,
                                   messages: list | None = None,
                                   title: str | None = None) -> bool:
        sets = ["updated_at = now()"]
        params: list = []
        idx = 1
        if messages is not None:
            sets.append(f"messages = ${idx}::jsonb")
            params.append(json.dumps(messages))
            idx += 1
        if title is not None:
            sets.append(f"title = ${idx}")
            params.append(title)
            idx += 1
        params.extend([session_id, user_id])
        sql = f"UPDATE chat_session SET {', '.join(sets)} WHERE session_id = ${idx} AND user_id = ${idx + 1}"
        idx += 2
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, *params)
            return "UPDATE 1" in r

    async def delete_chat_session(self, session_id: UUID, user_id: UUID) -> bool:
        sql = "DELETE FROM chat_session WHERE session_id = $1 AND user_id = $2"
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, session_id, user_id)
            return "DELETE 1" in r

    # ── Admin Audit Log ──────────────────────────────────────────

    async def log_admin_action(self, action: str, details: dict | None = None, ip_address: str = ""):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_audit_log (action, details, ip_address) VALUES ($1, $2, $3)",
                action, details or {}, ip_address,
            )

    async def get_admin_audit_log(self, limit: int = 50) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, action, details, ip_address, created_at "
                "FROM admin_audit_log ORDER BY created_at DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

    # ── Settings ─────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
            return val or default

    async def set_setting(self, key: str, value: str):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ($1, $2, now())
                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()
            """, key, value)

    async def get_all_settings(self) -> dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM app_settings")
            return {r["key"]: r["value"] for r in rows}

    # ── Credentials ──────────────────────────────────────────────

    async def list_credentials(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT credential_id, service_name, api_key, base_url, description, is_active, created_at, updated_at "
                "FROM api_credential ORDER BY service_name"
            )
            return [dict(r) for r in rows]

    async def get_credential(self, credential_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT credential_id, service_name, api_key, base_url, description, is_active, created_at, updated_at "
                "FROM api_credential WHERE credential_id = $1", credential_id
            )
            return dict(r) if r else None

    async def get_credential_by_service(self, service_name: str) -> dict | None:
        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT credential_id, service_name, api_key, base_url, description, is_active, created_at, updated_at "
                "FROM api_credential WHERE service_name = $1 AND is_active = true", service_name
            )
            return dict(r) if r else None

    async def create_credential(self, service_name: str, api_key: str,
                                 base_url: str = "", description: str = "") -> str:
        async with self._pool.acquire() as conn:
            return str(await conn.fetchval(
                "INSERT INTO api_credential (service_name, api_key, base_url, description) "
                "VALUES ($1, $2, $3, $4) RETURNING credential_id",
                service_name, api_key, base_url, description
            ))

    async def update_credential(self, credential_id: str, api_key: str | None = None,
                                 base_url: str | None = None, description: str | None = None,
                                 is_active: bool | None = None) -> bool:
        sets = []
        params = []
        idx = 1
        if api_key is not None:
            sets.append(f"api_key = ${idx}"); params.append(api_key); idx += 1
        if base_url is not None:
            sets.append(f"base_url = ${idx}"); params.append(base_url); idx += 1
        if description is not None:
            sets.append(f"description = ${idx}"); params.append(description); idx += 1
        if is_active is not None:
            sets.append(f"is_active = ${idx}"); params.append(is_active); idx += 1
        if not sets:
            return False
        sets.append(f"updated_at = now()")
        params.append(credential_id)
        sql = f"UPDATE api_credential SET {', '.join(sets)} WHERE credential_id = ${idx}::uuid"
        async with self._pool.acquire() as conn:
            r = await conn.execute(sql, *params)
            return r != "UPDATE 0"

    async def delete_credential(self, credential_id: str) -> bool:
        async with self._pool.acquire() as conn:
            r = await conn.execute("DELETE FROM api_credential WHERE credential_id = $1::uuid", credential_id)
            return r != "DELETE 0"

    # ── Interpret Engine ────────────────────────────────────────

    async def store_interpretation(self, product_type: str, title: str,
                                    payload: dict, topic: str | None = None,
                                    narrative_id: UUID | None = None,
                                    claim_ids: list[UUID] | None = None,
                                    source_ids: list[UUID] | None = None,
                                    narrative_ids: list[UUID] | None = None,
                                    severity: str = "info") -> UUID:
        def _json_default(o):
            if isinstance(o, UUID):
                return str(o)
            if isinstance(o, datetime):
                return o.isoformat()
            raise TypeError
        async with self._pool.acquire() as conn:
            _json_str = json.dumps(payload, default=_json_default)
            payload_pg = json.loads(_json_str)
            row = await conn.fetchrow("""
                INSERT INTO interpretation (product_type, topic, narrative_id, title,
                    payload, claim_ids, source_ids, narrative_ids, severity)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::uuid[], $7::uuid[], $8::uuid[], $9)
                RETURNING interpretation_id
            """, product_type, topic, narrative_id, title,
                payload_pg, claim_ids or [], source_ids or [],
                narrative_ids or [], severity)
            await conn.execute("""
                INSERT INTO interpretation_history (interpretation_id, product_type,
                    topic, narrative_id, payload)
                VALUES ($1, $2, $3, $4, $5::jsonb)
            """, row[0], product_type, topic, narrative_id, payload_pg)
            return row[0]

    async def get_interpretations(self, product_type: str | None = None,
                                   topic: str | None = None,
                                   narrative_id: UUID | None = None,
                                   limit: int = 20) -> list[dict]:
        clauses = []
        params: list = []
        if product_type:
            clauses.append(f"product_type = ${len(params) + 1}")
            params.append(product_type)
        if topic:
            clauses.append(f"topic = ${len(params) + 1}")
            params.append(topic)
        if narrative_id:
            clauses.append(f"narrative_id = ${len(params) + 1}")
            params.append(narrative_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            SELECT interpretation_id, product_type, topic, narrative_id, title,
                   payload, claim_ids, source_ids, narrative_ids, severity, produced_at
            FROM interpretation {where}
            ORDER BY produced_at DESC LIMIT ${len(params) + 1}
        """
        params.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            result = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("payload"), str):
                    import json
                    d["payload"] = json.loads(d["payload"])
                result.append(d)
            return result

    async def produce_topic_brief(self, topic: str) -> dict:
        async with self._pool.acquire() as conn:
            stats = await conn.fetchrow("""
                SELECT COUNT(DISTINCT c.claim_id) AS total_claims,
                       COUNT(DISTINCT c.source_id) AS total_sources,
                       COUNT(DISTINCT nc.narrative_id) AS total_narratives
                FROM claim c
                LEFT JOIN narrative_claim nc ON nc.claim_id = c.claim_id
                WHERE c.topic = $1
            """, topic)
            total_claims = stats["total_claims"] or 0
            if total_claims == 0:
                return {"topic": topic, "total_claims": 0, "message": "No claims for this topic"}

            last_cycle = await conn.fetchval("""
                SELECT produced_at FROM interpretation
                WHERE topic = $1 AND product_type = 'topic_brief'
                ORDER BY produced_at DESC LIMIT 1
            """, topic)

            new_since = total_claims
            if last_cycle:
                new_since = await conn.fetchval("""
                    SELECT COUNT(*) FROM claim
                    WHERE topic = $1 AND extracted_at > $2
                """, topic, last_cycle)

            contradictions = await conn.fetch("""
                SELECT cr.relationship_type, COUNT(DISTINCT cr.relationship_id) AS cnt
                FROM claim_relationship cr
                JOIN claim c ON c.claim_id IN (cr.source_claim_id, cr.target_claim_id)
                WHERE c.topic = $1 AND cr.relationship_type IN ('contradicts', 'supports')
                GROUP BY cr.relationship_type
            """, topic)
            contra_map = {r["relationship_type"]: r["cnt"] for r in contradictions}

            narratives = await conn.fetch("""
                SELECT n.narrative_id, n.name, n.stability_label, n.claim_count,
                       n.momentum, n.velocity,
                       (SELECT c2.canonical_text
                        FROM narrative_claim nc2
                        JOIN claim c2 ON c2.claim_id = nc2.claim_id
                        WHERE nc2.narrative_id = n.narrative_id
                        ORDER BY nc2.weight DESC LIMIT 1) AS top_claim_text
                FROM narrative n
                JOIN narrative_claim nc ON nc.narrative_id = n.narrative_id
                JOIN claim c ON c.claim_id = nc.claim_id
                WHERE c.topic = $1
                GROUP BY n.narrative_id
                ORDER BY n.claim_count DESC LIMIT 10
            """, topic)

            top_sources = await conn.fetch("""
                SELECT ss.source_url, ss.title, sb.reliability_score,
                       COUNT(DISTINCT c.claim_id) AS claim_count
                FROM source_snapshot ss
                JOIN claim c ON c.source_id = ss.source_id
                LEFT JOIN source_behavior sb ON sb.source_id = ss.source_id
                WHERE c.topic = $1
                GROUP BY ss.source_id, sb.reliability_score
                ORDER BY claim_count DESC LIMIT 5
            """, topic)

            entities = await conn.fetch("""
                SELECT e.name, e.entity_type, COUNT(ce.claim_id) AS mention_count
                FROM entity e
                JOIN claim_entity ce ON ce.entity_id = e.entity_id
                JOIN claim c ON c.claim_id = ce.claim_id
                WHERE c.topic = $1
                GROUP BY e.entity_id
                ORDER BY mention_count DESC LIMIT 10
            """, topic)

            brief = {
                "topic": topic,
                "total_claims": total_claims,
                "total_sources": stats["total_sources"],
                "total_narratives": stats["total_narratives"],
                "new_claims_since_last": new_since,
                "contradictions": contra_map.get("contradicts", 0),
                "supports": contra_map.get("supports", 0),
                "narratives": [dict(r) for r in narratives],
                "top_sources": [dict(r) for r in top_sources],
                "top_entities": [dict(r) for r in entities],
            }
            from helioryn.constants import TOPICS
            topic_label = TOPICS.get(topic, {}).get("name", topic)
            await self.store_interpretation("topic_brief", f"{topic_label} Brief",
                brief, topic=topic, severity="info",
                source_ids=[r["source_id"] for r in top_sources if "source_id" in r])
            return brief

    async def produce_narrative_deep_dive(self, narrative_id: UUID) -> dict | None:
        async with self._pool.acquire() as conn:
            narrative = await conn.fetchrow("""
                SELECT n.*, ed.source_diversity, ed.echo_chamber_score
                FROM narrative n
                LEFT JOIN evidence_density ed ON ed.narrative_id = n.narrative_id
                WHERE n.narrative_id = $1
            """, narrative_id)
            if not narrative:
                return None
            n = dict(narrative)

            top_claims = await conn.fetch("""
                SELECT c.claim_id, c.canonical_text, c.topic,
                       cf.composite AS confidence
                FROM narrative_claim nc
                JOIN claim c ON c.claim_id = nc.claim_id
                LEFT JOIN (
                    SELECT target_id, AVG(value * weight) / NULLIF(AVG(weight), 0) AS composite
                    FROM confidence_factor WHERE target_type = 'claim'
                    GROUP BY target_id
                ) cf ON cf.target_id = c.claim_id
                WHERE nc.narrative_id = $1
                ORDER BY nc.weight DESC LIMIT 10
            """, narrative_id)

            contradictions = await conn.fetch("""
                SELECT cr.relationship_type, COUNT(*) AS cnt,
                       c1.canonical_text AS source_text,
                       c2.canonical_text AS target_text
                FROM claim_relationship cr
                JOIN claim c1 ON c1.claim_id = cr.source_claim_id
                JOIN claim c2 ON c2.claim_id = cr.target_claim_id
                JOIN narrative_claim nc ON nc.claim_id = c1.claim_id
                WHERE nc.narrative_id = $1
                  AND cr.relationship_type IN ('contradicts', 'supports')
                GROUP BY cr.relationship_type, c1.claim_id, c2.claim_id,
                         c1.canonical_text, c2.canonical_text
                ORDER BY cnt DESC LIMIT 5
            """, narrative_id)

            sources = await conn.fetch("""
                SELECT ss.source_id, ss.source_url, ss.title,
                       sb.reliability_score, COUNT(DISTINCT c.claim_id) AS claim_count
                FROM source_snapshot ss
                JOIN claim c ON c.source_id = ss.source_id
                JOIN narrative_claim nc ON nc.claim_id = c.claim_id
                LEFT JOIN source_behavior sb ON sb.source_id = ss.source_id
                WHERE nc.narrative_id = $1
                GROUP BY ss.source_id, sb.reliability_score
                ORDER BY claim_count DESC LIMIT 5
            """, narrative_id)

            deep_dive = {
                "narrative_id": str(narrative_id),
                "name": n["name"],
                "description": n.get("description"),
                "top_terms": n.get("top_terms", []),
                "claim_count": n.get("claim_count", 0),
                "stability_label": n.get("stability_label", "unknown"),
                "stability_score": n.get("stability_score"),
                "momentum": n.get("momentum"),
                "velocity": n.get("velocity"),
                "divergence": n.get("divergence"),
                "contradiction_density": n.get("contradiction_density"),
                "source_diversity": n.get("source_diversity"),
                "echo_chamber_score": n.get("echo_chamber_score"),
                "top_claims": [dict(r) for r in top_claims],
                "contradictions": [dict(r) for r in contradictions],
                "key_sources": [dict(r) for r in sources],
            }
            await self.store_interpretation("narrative_deep_dive",
                f"Deep Dive: {n['name']}", deep_dive,
                narrative_id=narrative_id,
                claim_ids=[r["claim_id"] for r in top_claims if "claim_id" in r],
                source_ids=[r["source_id"] for r in sources if "source_id" in r],
                narrative_ids=[narrative_id])
            return deep_dive

    async def produce_contradiction_report(self, topic: str, limit: int = 20) -> dict:
        async with self._pool.acquire() as conn:
            pairs = await conn.fetch("""
                SELECT cr.relationship_id, cr.relationship_type, cr.confidence,
                       cr.evidence, cr.detected_at,
                       c1.claim_id AS source_id, c1.canonical_text AS source_text,
                       c1.source_url AS source_url_a,
                       c2.claim_id AS target_id, c2.canonical_text AS target_text,
                       c2.source_url AS source_url_b
                FROM claim_relationship cr
                JOIN claim c1 ON c1.claim_id = cr.source_claim_id
                JOIN claim c2 ON c2.claim_id = cr.target_claim_id
                WHERE c1.topic = $1 AND c2.topic = $1
                  AND cr.relationship_type = 'contradicts'
                ORDER BY cr.confidence DESC LIMIT $2
            """, topic, limit)

            return {
                "topic": topic,
                "total": len(pairs),
                "contradictions": [dict(r) for r in pairs],
            }

    async def produce_velocity_alerts(self, topic: str | None = None) -> list[dict]:
        async with self._pool.acquire() as conn:
            where = "WHERE 1=1"
            params: list = []
            if topic:
                where = "WHERE c.topic = $1"
                params.append(topic)
            sql = f"""
                SELECT n.narrative_id, n.name, n.momentum, n.velocity, n.divergence,
                       n.claim_count, n.stability_label, c.topic
                FROM narrative n
                JOIN narrative_claim nc ON nc.narrative_id = n.narrative_id
                JOIN claim c ON c.claim_id = nc.claim_id
                {where}
                GROUP BY n.narrative_id, c.topic
                HAVING n.velocity > 0.5 OR n.momentum > 2.0 OR n.divergence > 0.3
                ORDER BY GREATEST(n.velocity, n.momentum, n.divergence) DESC LIMIT 10
            """
            rows = await conn.fetch(sql, *params)
            alerts = []
            for r in rows:
                parts = []
                if r["velocity"] and r["velocity"] > 0.5:
                    parts.append(f"velocity {r['velocity']:.2f}x")
                if r["momentum"] and r["momentum"] > 2.0:
                    parts.append(f"momentum {r['momentum']:.1f}")
                if r["divergence"] and r["divergence"] > 0.3:
                    parts.append(f"divergence {r['divergence']:.2f}")
                reason_text = "Rising: " + ", ".join(parts) if parts else "Monitoring narrative shift."
                severity = "warning" if (r["velocity"] or 0) > 2.0 or (r["momentum"] or 0) > 5.0 else "info"
                alerts.append({
                    "narrative_id": str(r["narrative_id"]),
                    "narrative_name": r["name"],
                    "topic": r["topic"],
                    "velocity": r["velocity"],
                    "momentum": r["momentum"],
                    "divergence": r["divergence"],
                    "claim_count": r["claim_count"],
                    "stability_label": r["stability_label"],
                    "reason": reason_text,
                    "severity": severity,
                })
            return alerts

    async def produce_source_intelligence_report(self, topic: str, limit: int = 10) -> dict:
        async with self._pool.acquire() as conn:
            sources = await conn.fetch("""
                SELECT ss.source_id, ss.source_url, ss.title,
                       sb.reliability_score, sb.contradiction_rate,
                       sb.originality_ratio, sb.n_claims,
                       sb.n_contradictions, sb.n_corrections,
                       COUNT(DISTINCT c.claim_id) AS claim_count,
                       COUNT(DISTINCT cr.relationship_id) AS total_relations
                FROM source_snapshot ss
                JOIN claim c ON c.source_id = ss.source_id
                LEFT JOIN source_behavior sb ON sb.source_id = ss.source_id
                LEFT JOIN claim_relationship cr
                    ON cr.source_claim_id = c.claim_id OR cr.target_claim_id = c.claim_id
                WHERE c.topic = $1
                GROUP BY ss.source_id, sb.reliability_score, sb.contradiction_rate,
                         sb.originality_ratio, sb.n_claims, sb.n_contradictions,
                         sb.n_corrections
                ORDER BY sb.reliability_score ASC NULLS LAST
                LIMIT $2
            """, topic, limit)

            top_by_reliability = sorted([dict(r) for r in sources],
                key=lambda x: x.get("reliability_score") or 0, reverse=True)[:3]
            bottom_by_reliability = sorted([dict(r) for r in sources],
                key=lambda x: x.get("reliability_score") or 1)[:3]

            return {
                "topic": topic,
                "total_sources": len(sources),
                "most_reliable": top_by_reliability,
                "least_reliable": bottom_by_reliability,
                "all_sources": [dict(r) for r in sources],
            }

    async def produce_narrative_correlations(self, topic: str | None = None,
                                               limit: int = 20) -> list[dict]:
        async with self._pool.acquire() as conn:
            where = "WHERE 1=1"
            params: list = []
            if topic:
                where = """
                    WHERE n1.narrative_id IN (
                        SELECT nc.narrative_id FROM narrative_claim nc
                        JOIN claim c ON c.claim_id = nc.claim_id WHERE c.topic = $1
                    ) AND n2.narrative_id IN (
                        SELECT nc.narrative_id FROM narrative_claim nc
                        JOIN claim c ON c.claim_id = nc.claim_id WHERE c.topic = $1
                    )
                """
                params.extend([topic, topic])
            sql = f"""
                SELECT no.*, n1.name AS name_a, n2.name AS name_b
                FROM narrative_overlap no
                JOIN narrative n1 ON n1.narrative_id = no.narrative_a_id
                JOIN narrative n2 ON n2.narrative_id = no.narrative_b_id
                {where}
                ORDER BY no.anomaly_score DESC NULLS LAST, no.overlap_score DESC
                LIMIT ${len(params) + 1}
            """
            params.append(limit)
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    # ── Chat / RAG ──────────────────────────────────────────────────

    async def execute(self, sql: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchrow(self, sql: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def vector_search(
        self, embedding: list[float], mode: str = "public", top_k: int = 15
    ) -> list[dict]:
        emb_str = str(embedding)
        if mode == "org":
            method_filter = "AND ss.retrieval_method IN ('upload', 'folder_watch')"
        elif mode == "all":
            method_filter = ""
        else:
            method_filter = "AND ss.retrieval_method NOT IN ('upload', 'folder_watch')"

        sql = f"""
            SELECT
                c.claim_id, c.canonical_text, ss.title, ss.source_url,
                ss.retrieval_method, ss.source_id,
                1 - (ce.embedding <=> $1::vector) AS similarity
            FROM claim_embedding ce
            JOIN claim c ON c.claim_id = ce.claim_id
            JOIN source_snapshot ss ON ss.source_id = c.source_id
            WHERE ce.model_name = 'all-MiniLM-L6-v2'
              {method_filter}
            ORDER BY similarity DESC
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, emb_str, top_k)
        return [
            {
                "claim_id": str(r["claim_id"]),
                "source_id": str(r["source_id"]),
                "title": r["title"] or "",
                "text": r["canonical_text"],
                "url": r["source_url"],
                "retrieval_method": r["retrieval_method"],
                "similarity": float(r["similarity"]),
            }
            for r in rows
        ]

    async def list_documents(self, limit: int = 50, offset: int = 0) -> list[dict]:
        sql = """
            SELECT source_id, source_url, title, retrieval_method,
                   raw_text, metadata, first_seen_at, last_updated_at
            FROM source_snapshot
            WHERE retrieval_method IN ('upload', 'folder_watch')
            ORDER BY last_updated_at DESC
            LIMIT $1 OFFSET $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit, offset)
        return [dict(r) for r in rows]

    # ── Guardrail: Hybrid Search ──────────────────────────────────

    @staticmethod
    def _extract_search_terms(question: str) -> list[str]:
        words = question.split()
        stop_words = {
            "what", "which", "where", "when", "why", "how", "who", "whom",
            "is", "are", "was", "were", "do", "does", "did", "have", "has",
            "had", "can", "could", "will", "would", "shall", "should", "may",
            "might", "must", "the", "a", "an", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "at", "be", "been", "being",
            "it", "its", "we", "our", "they", "their", "you", "your",
            "that", "this", "these", "those", "and", "or", "not", "no",
            "about", "any", "all", "each", "every", "some", "more", "most",
            "tell", "list", "find", "show", "give", "need", "like", "able",
            "regarding", "please", "also", "very", "just", "only",
        }
        terms = []
        seen = set()
        # Multi-word proper nouns (consecutive capitalized words)
        i = 0
        while i < len(words):
            w = words[i]
            clean = w.strip("?,.;:!\"'()[]{}").lower()
            is_cap = w[0].isupper() if w else False
            if is_cap:
                j = i
                while j < len(words) and (words[j][0].isupper() if words[j] else False):
                    j += 1
                phrase = " ".join(words[i:j]).strip("?,.;:!\"'()[]{}")
                if len(phrase) > 1:
                    lower = phrase.lower()
                    if lower not in seen and lower not in stop_words:
                        terms.append(phrase)
                        seen.add(lower)
                    i = j
                    continue
            if clean not in stop_words and clean not in seen and len(clean) > 2:
                terms.append(clean)
                seen.add(clean)
            i += 1
        return terms[:6]

    async def hybrid_search(
        self, question: str, embedding: list[float],
        mode: str = "public", top_k: int = 20,
    ) -> list[dict]:
        emb_str = str(embedding)
        if mode == "org":
            method_filter = "AND ss.retrieval_method IN ('upload', 'folder_watch')"
        elif mode == "all":
            method_filter = ""
        else:
            method_filter = "AND ss.retrieval_method NOT IN ('upload', 'folder_watch')"

        GOV_SEED_BOOST = 2.0
        raw_limit = top_k * 8

        # Vector search (fetch extra candidates for gov_seed boost)
        vec_sql = f"""
            SELECT c.claim_id, c.canonical_text, ss.title, ss.source_url,
                   ss.retrieval_method, ss.source_id,
                   1 - (ce.embedding <=> $1::vector) AS sim,
                   'vector' AS match_type
            FROM claim_embedding ce
            JOIN claim c ON c.claim_id = ce.claim_id
            JOIN source_snapshot ss ON ss.source_id = c.source_id
            WHERE ce.model_name = 'all-MiniLM-L6-v2'
              {method_filter}
            ORDER BY sim DESC
            LIMIT {raw_limit}
        """
        async with self._pool.acquire() as conn:
            vec_rows = await conn.fetch(vec_sql, emb_str)

        # Keyword search — extract meaningful terms from the question
        kw_terms = self._extract_search_terms(question)
        kw_rows = []
        if kw_terms:
            kw_sim = 0.3
            kw_conditions = " OR ".join(
                f"(c.canonical_text ILIKE '%' || ${i}::text || '%' OR ss.title ILIKE '%' || ${i}::text || '%')"
                for i, _ in enumerate(kw_terms, start=1)
            )
            kw_params = list(kw_terms)
            limit_idx = len(kw_terms) + 1
            kw_sql = f"""
                SELECT c.claim_id, c.canonical_text, ss.title, ss.source_url,
                       ss.retrieval_method, ss.source_id,
                       {kw_sim} AS sim,
                       'keyword' AS match_type
                FROM claim c
                JOIN source_snapshot ss ON ss.source_id = c.source_id
                WHERE ({kw_conditions})
                  {method_filter}
                LIMIT ${limit_idx}
            """
            async with self._pool.acquire() as conn:
                kw_rows = await conn.fetch(kw_sql, *kw_params, top_k // 2)

        seen = set()
        merged = []
        for r in list(vec_rows) + list(kw_rows):
            cid = str(r["claim_id"])
            if cid in seen:
                continue
            seen.add(cid)
            sim = float(r["sim"])
            if r["retrieval_method"] == "gov_seed":
                sim *= GOV_SEED_BOOST
            merged.append({
                "claim_id": cid,
                "source_id": str(r["source_id"]),
                "title": r["title"] or "",
                "text": r["canonical_text"],
                "url": r["source_url"],
                "retrieval_method": r["retrieval_method"],
                "similarity": sim,
                "match_type": r["match_type"],
            })

        merged.sort(key=lambda x: x["similarity"], reverse=True)

        # Per-source cap: at most top_k/4 results from a single source (min 2)
        cap = max(2, top_k // 4)
        source_count: dict[str, int] = {}
        capped = []
        # Ensure at least one keyword match per matching source for diversity
        kw_sources_seen: set[str] = set()
        for item in merged:
            sid = item["source_id"]
            if source_count.get(sid, 0) >= cap:
                continue
            source_count[sid] = source_count.get(sid, 0) + 1
            capped.append(item)
            if len(capped) >= top_k:
                break

        return capped

    # ── Guardrail: Embedding Lookup ───────────────────────────────

    async def get_embeddings_by_ids(self, claim_ids: list[str]) -> dict[str, list[float]]:
        if not claim_ids:
            return {}
        uuids = [UUID(cid) for cid in claim_ids]
        sql = """
            SELECT claim_id, embedding::text AS emb_str
            FROM claim_embedding
            WHERE claim_id = ANY($1::uuid[])
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, uuids)
        import json
        return {str(r["claim_id"]): json.loads(r["emb_str"]) for r in rows}

    # ── Guardrail: Structured Data Extraction ─────────────────────

    async def extract_structured_data(self, question: str, mode: str = "org") -> dict | None:
        q_lower = question.lower()
        if mode == "org":
            method_filter = "AND ss.retrieval_method IN ('upload', 'folder_watch')"
        else:
            method_filter = "AND ss.retrieval_method NOT IN ('upload', 'folder_watch')"

        # Detect structured query types
        is_employee = any(w in q_lower for w in ("employee", "staff", "advocate", "who", "training record"))
        is_requirement = any(w in q_lower for w in ("requirement", "standard", "policy", "condition", "rule", "regulation"))
        is_gap = any(w in q_lower for w in ("gap", "missing", "deficit", "out of compliance", "deficient", "shortfall"))
        is_count = any(w in q_lower for w in ("how many", "count", "number of", "list", "total"))

        if is_employee:
            sql = f"""
                SELECT c.claim_id, c.canonical_text, ss.title, ss.source_url,
                       ss.metadata
                FROM claim c
                JOIN source_snapshot ss ON ss.source_id = c.source_id
                WHERE (c.canonical_text ILIKE '%employee%' OR c.canonical_text ILIKE '%training record%')
                  AND c.topic = 'org-compliance'
                  {method_filter}
                ORDER BY ss.last_updated_at DESC
                LIMIT 20
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql)
            records = [dict(r) for r in rows]
            if records:
                return {"type": "employee_records", "data": records}

        if is_requirement:
            sql = f"""
                SELECT c.claim_id, c.canonical_text, ss.title, ss.source_url
                FROM claim c
                JOIN source_snapshot ss ON ss.source_id = c.source_id
                WHERE (c.canonical_text ILIKE '%requirement%' OR c.canonical_text ILIKE '%must%'
                       OR c.canonical_text ILIKE '%shall%' OR c.canonical_text ILIKE '%standard%'
                       OR ss.title ILIKE '%policy%' OR ss.title ILIKE '%requirement%')
                  AND c.topic = 'org-compliance'
                  {method_filter}
                ORDER BY ss.last_updated_at DESC
                LIMIT 20
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql)
            records = [dict(r) for r in rows]
            if records:
                return {"type": "requirements", "data": records}

        if is_gap:
            sql = f"""
                SELECT c.claim_id, c.canonical_text, ss.title, ss.source_url,
                       ss.metadata
                FROM claim c
                JOIN source_snapshot ss ON ss.source_id = c.source_id
                WHERE (c.canonical_text ILIKE '%gap%' OR c.canonical_text ILIKE '%deficit%'
                       OR c.canonical_text ILIKE '%missing%' OR c.canonical_text ILIKE '%pending%'
                       OR c.canonical_text ILIKE '%not complete%' OR c.canonical_text ILIKE '%deficit%'
                       OR c.canonical_text ILIKE '%hours required%' OR c.canonical_text ILIKE '%hours deficit%')
                  AND c.topic = 'org-compliance'
                  {method_filter}
                ORDER BY ss.last_updated_at DESC
                LIMIT 20
            """
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql)
            records = [dict(r) for r in rows]
            if records:
                return {"type": "gaps", "data": records}

        return None
