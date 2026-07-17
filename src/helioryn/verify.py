# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path


async def verify_pipeline(store) -> dict:
    """Run pipeline health checks. Returns dict of check_name -> (pass: bool, detail: str)."""
    results: dict[str, dict] = {}

    async with store._pool.acquire() as conn:
        # 1. Sources
        src_count = await conn.fetchval("SELECT count(*) FROM source_snapshot")
        results["sources_present"] = {
            "pass": src_count > 0,
            "detail": f"{src_count} sources archived",
        }

        # 2. Claims
        claim_count = await conn.fetchval("SELECT count(*) FROM claim")
        results["claims_present"] = {
            "pass": claim_count > 0,
            "detail": f"{claim_count} claims extracted",
        }

        # 3. Embedding gap
        unembedded = await conn.fetchval(
            "SELECT count(*) FROM claim c LEFT JOIN claim_embedding e "
            "ON c.claim_id = e.claim_id WHERE e.claim_id IS NULL"
        )
        results["embedding_gap"] = {
            "pass": unembedded == 0,
            "detail": f"{unembedded} claims without embeddings",
        }

        # 4. Claim types (should not all be "fact")
        ct_rows = await conn.fetch(
            "SELECT claim_type, count(*) FROM claim GROUP BY claim_type ORDER BY count(*) DESC"
        )
        types_str = ", ".join(f"{r['claim_type']}={r['count']}" for r in ct_rows)
        has_multiple_types = len(ct_rows) > 1 or ct_rows[0]["claim_type"] != "fact" if ct_rows else True
        results["claim_types"] = {
            "pass": has_multiple_types,
            "detail": types_str,
        }

        # 5. Entity types (should have more than just "concept")
        et_rows = await conn.fetch(
            "SELECT entity_type, count(*) FROM entity GROUP BY entity_type ORDER BY count(*) DESC"
        )
        et_str = ", ".join(f"{r['entity_type']}={r['count']}" for r in et_rows)
        has_entity_types = (
            len([r for r in et_rows if r["entity_type"] != "concept"]) >= 2
            if et_rows else True
        )
        results["entity_types"] = {
            "pass": has_entity_types,
            "detail": et_str,
        }

        # 6. Relationship types (basic pipeline: repeated_by + contradicts must exist;
        # advanced types supports/evolves_into/derived_from/references are informational)
        rel_rows = await conn.fetch(
            "SELECT relationship_type, count(*) FROM claim_relationship "
            "GROUP BY relationship_type ORDER BY count(*) DESC"
        )
        rel_str = ", ".join(f"{r['relationship_type']}={r['count']}" for r in rel_rows)
        rel_types = {r["relationship_type"] for r in rel_rows}
        has_repeated = "repeated_by" in rel_types
        has_contradicts = "contradicts" in rel_types
        missing_advanced = [
            t for t in ("supports", "evolves_into", "derived_from", "references")
            if t not in rel_types
        ]
        results["relationship_types"] = {
            "pass": has_repeated and has_contradicts,
            "detail": rel_str + ("; advanced types missing: " + ", ".join(missing_advanced) if missing_advanced else ""),
        }

        # 7. Observations
        obs_count = await conn.fetchval("SELECT count(*) FROM claim_observation")
        results["observations_present"] = {
            "pass": obs_count > 0,
            "detail": f"{obs_count} observations",
        }

        # 8. Narratives
        nar_count = await conn.fetchval("SELECT count(*) FROM narrative")
        results["narratives_present"] = {
            "pass": nar_count > 0,
            "detail": f"{nar_count} narratives",
        }

        # 9. Observation context quality (check for HTML in recent observations)
        bad_ctx = await conn.fetchval(
            "SELECT count(*) FROM claim_observation WHERE "
            "context IS NOT NULL AND (context LIKE '<!%' OR context LIKE '<html%' "
            "OR context LIKE '<scri%' OR context LIKE '<!DOC%') "
            "AND observed_at > NOW() - interval '1 hour'"
        )
        results["observation_context"] = {
            "pass": bad_ctx == 0,
            "detail": f"{bad_ctx} recent observations with HTML context",
        }

        # 10. Daemon alive check
        recent_obs = await conn.fetchval(
            "SELECT count(*) FROM claim_observation WHERE observed_at > NOW() - interval '30 minutes'"
        )
        results["daemon_alive"] = {
            "pass": recent_obs > 0,
            "detail": f"{recent_obs} observations in last 30 min",
        }

        # 11. Confidence factor completeness
        essential = ["source_reliability", "evidence_diversity", "temporal_stability", "extraction_method"]
        n_essential = len(essential)
        missing_sql = """
        SELECT c.claim_id FROM claim c
        WHERE (
            SELECT COUNT(DISTINCT cf.factor_type) FROM confidence_factor cf
            WHERE cf.target_type = 'claim' AND cf.target_id = c.claim_id
              AND cf.factor_type = ANY($1::text[])
        ) < $2
        LIMIT 1
        """
        any_missing = await conn.fetchval(missing_sql, essential, n_essential)
        total_claims = await conn.fetchval("SELECT count(*) FROM claim")
        missing_count = 0
        if any_missing is not None:
            missing_count = await conn.fetchval("""
                SELECT count(*) FROM claim c
                WHERE (
                    SELECT COUNT(DISTINCT cf.factor_type) FROM confidence_factor cf
                    WHERE cf.target_type = 'claim' AND cf.target_id = c.claim_id
                      AND cf.factor_type = ANY($1::text[])
                ) < $2
            """, essential, n_essential)
        results["confidence_factors"] = {
            "pass": any_missing is None,
            "detail": f"All {total_claims} claims have essential confidence factors" if any_missing is None
                      else f"{missing_count}/{total_claims} claims missing essential confidence factors",
        }

        # 12. Source behavior populated
        missing_behavior = await conn.fetchval("""
        SELECT count(*) FROM source_snapshot ss
        WHERE EXISTS (SELECT 1 FROM claim c WHERE c.source_id = ss.source_id)
          AND NOT EXISTS (SELECT 1 FROM source_behavior sb WHERE sb.source_id = ss.source_id)
        """)
        results["source_behavior"] = {
            "pass": missing_behavior == 0,
            "detail": f"{missing_behavior} sources with claims missing behavior records",
        }

    return results


def format_verification(results: dict[str, dict]) -> str:
    lines = []
    lines.append("Pipeline Verification")
    lines.append("=" * 60)
    all_pass = True
    for name, check in sorted(results.items()):
        status = "PASS" if check["pass"] else "FAIL"
        if not check["pass"]:
            all_pass = False
        lines.append(f"  [{status}] {name}: {check['detail']}")
    lines.append("=" * 60)
    lines.append(f"  Overall: {'ALL CHECKS PASS' if all_pass else 'SOME CHECKS FAILED'}")
    return "\n".join(lines)
