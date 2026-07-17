# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
from __future__ import annotations

import json
from uuid import UUID as _UUID

from helioryn.config import AppConfig
from helioryn.embed import generate_embedding
from helioryn.gov_search import search_government
from helioryn.llm import create_llm
from helioryn.verify_rag import (
    verify_claims,
    should_abstain,
    compute_verification_summary,
    format_verification_notice,
)

BASE_SYSTEM_PROMPT = (
    "You are an OVC/VOCA grant compliance audit assistant. Your job is to answer questions "
    "based ONLY on the provided context documents and help grantees understand what needs "
    "to be fixed.\n\n"
    "Rules:\n"
    "- Answer ONLY from the context provided below. Do not use your own knowledge.\n"
    "- If the context doesn't contain enough information to answer, say so clearly.\n"
    "- CITE EXACT LOCATIONS: Always include the specific recommendation number, section "
    "heading, report ID, or exact quote.\n"
    "- EXACT QUOTES ONLY: When quoting a regulation or document, use the EXACT text from the "
    "source excerpt provided. Do not paraphrase, rephrase, or fabricate quotes. If you don't "
    "have the exact text, say what the source says in your own words without using quotation marks.\n"
    "- DOMAIN MAPPING: In OVC/VOCA audit context, the terms 'findings', 'issues', "
    "'problems', 'non-compliances', 'what went wrong', and 'recommendations' all refer "
    "to the same underlying audit findings. If the context contains recommendations, "
    "those ARE the audit findings — report them as findings.\n"
    "- REGULATION vs GUIDANCE: Distinguish between binding regulations (CFR, federal statutes) "
    "and non-binding guidance (Financial Guide sections, OVC guidelines, technical assistance "
    "materials). Cite the regulatory source when a requirement is mandatory.\n"
    "- ENTITY TYPE: Always distinguish between SAAs (State Administering Agencies) and "
    "subgrantees/subrecipients when discussing administrative cost caps. The 5% training and "
    "administration cap in 28 CFR § 94.107 applies to SAAs at the state level. Subrecipient "
    "administrative costs are governed by 28 CFR § 94.121 (allowable sub-recipient "
    "administrative costs) and 2 CFR 200 (Uniform Guidance).\n"
    "- VOCAPEDIA GUIDANCE: If the OVC VOCA Victim Assistance Vocapedia is available as a "
    "source, cite it as OVC guidance. Note that Vocapedia guidance is non-binding but "
    "represents OVC's official interpretation.\n"
    "- PASS-THROUGH ENTITY / SUBRECIPIENT QUESTIONS: When the user asks about monitoring "
    "a subrecipient, reviewing audit findings, or pass-through entity responsibilities, "
    "2 CFR § 200.332 (Requirements for pass-through entities) is the primary authority. "
    "When citing 200.332, enumerate ALL of its sub-requirements: (a) identifying and "
    "notifying subrecipients about award details, (b) evaluating each subrecipient's risk "
    "of noncompliance, (c)(1)-(2) considering prior audit results and other information "
    "in risk assessment, (d) monitoring subrecipient activities, (e)(1)-(3) ensuring "
    "corrective action on Single Audit findings, issuing management decisions, and "
    "verifying corrective action before closing findings, (f) terminating awards when "
    "necessary, (g) adjusting methods as needed, and (h)-(i) documenting all actions. "
    "Do not pick just the most relevant paragraphs — list all sub-requirements that apply. "
    "Also reference 28 CFR § 94.106 for VOCA-specific monitoring.\n"
    "- NAME RESOLUTION: The user may use shorthand names (e.g., 'Safe Horizon' for "
    "'Safe Horizon Inc.', 'DOJ' for 'Department of Justice', 'OVS' for 'Office of "
    "Victim Services'). Match them to the full names in the context.\n"
    "- Be specific about what needs to change and reference the exact text.\n"
    "- Include dollar amounts or specific number requirements when present.\n"
    "- Be precise and factual. Do not speculate.\n"
    "- Format your answer with clear sections when appropriate.\n"
    "- Be thorough — list ALL relevant items from the context, don't summarize away details.\n"
    "- COMPLETENESS: When you cite a regulation section (e.g., 2 CFR 200.332, 2 CFR 200.303, "
    "28 CFR 94.106), enumerate ALL of its sub-requirements and sub-paragraphs, not just the "
    "one most relevant to the question. Do not summarize a multi-part section into a single "
    "sentence. List each sub-requirement separately with its specific paragraph letter or number."
)

STRUCTURED_PROMPT_SUFFIX = (
    "\n\nIMPORTANT: Where structured data is available below, use it directly. "
    "Do not recompute, re-read, or re-interpret table data. The structured data "
    "is algorithmically extracted and is more reliable than raw text. Report the "
    "numbers and facts exactly as they appear in the structured data section."
)


def _classify_question(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ("how many", "count", "number of", "list all", "total")):
        return "list"
    if any(w in q for w in ("gap", "missing", "deficit", "shortfall", "out of compliance",
                             "deficient", "not compliant")):
        return "gap"
    if any(w in q for w in ("compare", "difference", "versus", "vs", "which")):
        return "compare"
    if any(w in q for w in ("is there", "does", "do we", "are we", "have we", "did we")):
        return "boolean"
    return "explain"


async def answer_question(
    question: str,
    mode: str,
    store,
    *,
    config: AppConfig | None = None,
    gov_search: bool = False,
) -> dict:
    q_type = _classify_question(question)
    emb = generate_embedding(question)

    # 1. Hybrid search (vector + keyword)
    chunks = await store.hybrid_search(question, emb, mode=mode, top_k=20)

    # Prioritize gov_seed results
    gov = [c for c in chunks if c['retrieval_method'] == 'gov_seed']
    other = [c for c in chunks if c['retrieval_method'] != 'gov_seed']
    max_other = max(0, 15 - len(gov))
    chunks = (gov + other[:max_other])
    chunks.sort(key=lambda x: x['similarity'], reverse=True)

    # 2. Search government APIs if requested
    gov_results: list[dict[str, str]] = []
    if gov_search:
        gov_results = await search_government(question)

    if not chunks and not gov_results:
        return {
            "answer": (
                "I couldn't find any relevant documents in the current data scope. "
                "Try switching to the other mode or uploading documents first."
            ),
            "sources": [],
            "verification": None,
            "mode": mode,
        }

    # 3. Augment chunks with full source text where claim text is short
    keywords = question.lower().split()
    for c in chunks:
        txt = c.get("text", "") or ""
        if len(txt) < 300 and c.get("source_id"):
            full = await store.get_source_raw_text(c["source_id"])
            if full and len(full) > 300:
                c["text"] = full[:4000]
                # Try to find a more relevant section within the source
                for kw in keywords:
                    if len(kw) > 3 and kw in full.lower():
                        idx = full.lower().index(kw)
                        start = max(0, idx - 500)
                        end = min(len(full), idx + 1500)
                        c["text"] = full[start:end]
                        break

    # 4. Full regulation text augmentation — when any paragraph of a CFR section
    # is retrieved, fetch the COMPLETE section text for completeness
    cfr_sources: dict[str, dict] = {}
    for c in chunks:
        title = (c.get("title") or "").lower()
        if "cfr" in title and c.get("source_id"):
            sid = c["source_id"]
            if sid not in cfr_sources:
                full = await store.get_source_raw_text(sid)
                if full and len(full) > 500:
                    cfr_sources[sid] = {"title": c.get("title", ""), "text": full}
    if cfr_sources:
        extras = []
        for sid, src in cfr_sources.items():
            extras.append({
                "title": src["title"] + " (FULL TEXT)",
                "text": src["text"][:6000],
                "retrieval_method": "gov_seed",
                "match_type": "full_text_augmentation",
                "source_id": sid,
                "similarity": 1.0,
            })
        chunks.extend(extras)
        import logging
        logging.warning("Full text augmentation: added %d extra chunks for CFR sections: %s",
                        len(extras), [s["title"] for s in cfr_sources.values()])

    # 5. Collect source texts for lexical verification
    source_texts = [c["text"] for c in chunks]

    # 5. Try structured data extraction
    structured = await store.extract_structured_data(question, mode=mode)

    # 6. Build system prompt
    system_prompt = BASE_SYSTEM_PROMPT
    if structured:
        system_prompt += STRUCTURED_PROMPT_SUFFIX

    # 7. Build context
    context_parts = []
    for i, c in enumerate(chunks, 1):
        title_lower = (c.get("title") or "").lower()
        if "oig" in title_lower or "audit" in title_lower:
            prefix = "[Source: DOJ Office of Inspector General (OIG)]\n"
        elif "cfr" in title_lower:
            prefix = "[Source: Code of Federal Regulations]\n"
        elif "ovc" in title_lower or "voca" in title_lower:
            prefix = "[Source: OVC — Office for Victims of Crime]\n"
        else:
            prefix = ""
        ctx = (
            f"[Document {i}] Title: {c['title'] or 'Untitled'}\n"
            f"Source: {c['retrieval_method']}\n"
            f"Match: {c['match_type']}\n"
            f"{prefix}Excerpt: {c['text'][:2000]}"
        )
        context_parts.append(ctx)

    gov_offset = len(chunks) + 1
    for i, g in enumerate(gov_results, gov_offset):
        ctx = (
            f"[Government Source {i}] Title: {g['title']}\n"
            f"Source: {g['source_name']}\n"
            f"URL: {g['url']}\n"
            f"Excerpt: {g['text'][:1000]}"
        )
        context_parts.append(ctx)

    if structured:
        def _json_default(obj):
            if isinstance(obj, _UUID):
                return str(obj)
            raise TypeError

        context_parts.append(
            "\n--- Structured Data (algorithmically extracted) ---\n"
            + json.dumps(structured, indent=2, default=_json_default)
        )

    context = "\n\n".join(context_parts)

    # 7. Call LLM via provider
    cfg = config or AppConfig.load(None)
    llm = create_llm(cfg)
    answer_text = ""
    try:
        answer_text = await llm.generate(
            system_prompt=system_prompt,
            context=context,
            question=question,
            model=cfg.llm.model,
            max_tokens=cfg.llm.max_tokens,
            temperature=cfg.llm.temperature,
        )
    except Exception as e:
        import traceback, logging as _log
        _log.getLogger(__name__).error("LLM error: %s\n%s", e, traceback.format_exc())
        answer_text = (
            f"I encountered an error connecting to the AI model: {e}. "
            f"Please check the LLM configuration (provider: {cfg.llm.provider}, "
            f"model: {cfg.llm.model})."
        )
        return {
            "answer": answer_text,
            "sources": [{
                "title": c["title"] or "Untitled",
                "excerpt": c["text"][:300],
                "source_id": c["source_id"],
                "retrieval_method": c["retrieval_method"],
                "url": c.get("url", ""),
            } for c in chunks[:10]],
            "verification": None,
            "mode": mode,
        }

    # 8. Verify claims via lexical overlap
    claim_results = await verify_claims(answer_text, source_texts)
    summary = compute_verification_summary(claim_results)

    # 9. Abstention check
    abstain, abstain_msg = should_abstain(claim_results, min_avg_score=0.40, max_unverifiable_ratio=0.50)
    if abstain:
        answer_text = (abstain_msg or "") + "\n\n"
        for c in chunks[:5]:
            answer_text += f"• {c['title']}\n  {c['text'][:200]}...\n\n"
        answer_text = answer_text.strip()

    # 10. Build sources — include regular chunks and full text augmentations
    sources = []
    seen_titles = set()
    for c in chunks:
        title = c.get("title", "Untitled") or "Untitled"
        key = title[:60]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        src = {
            "title": title[:80],
            "excerpt": c["text"][:300],
            "source_id": c["source_id"],
            "retrieval_method": c["retrieval_method"],
            "match_type": c.get("match_type", ""),
            "url": c.get("url", ""),
            "source_type": "internal",
            "source_name": "Internal Document",
        }
        sources.append(src)
        if len(sources) >= 15:
            break

    for g in gov_results[:5]:
        sources.append({
            "title": g["title"],
            "excerpt": g["text"][:300],
            "source_id": "",
            "retrieval_method": g["source_type"],
            "url": g["url"],
            "source_type": "government",
            "source_name": g["source_name"],
        })

    verification_info = None
    if claim_results:
        notice = format_verification_notice(summary)
        verification_info = {
            "summary": summary,
            "claims": [
                {
                    "text": cr["text"],
                    "score": cr["score"],
                    "status": cr["status"],
                }
                for cr in claim_results
            ],
            "notice": notice,
        }

    return {
        "answer": answer_text,
        "sources": sources,
        "verification": verification_info,
        "mode": mode,
    }
