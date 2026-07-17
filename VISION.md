Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.

# Helioryn — Grant Oversight Intelligence

Read this alongside AGENTS.md, CONTROL.md, SYSTEM.md, and design-doc.md at startup.

---

## What This Is

Helioryn is an evidence-based intelligence tool for **grant managers and grant-making agencies** (OVC, Navaa, DOJ, HHS). It helps oversight professionals understand audit findings, grant policies, compliance issues, and government regulations at a depth exceeding human capability. This is NOT a tool for grantees. It is a tool for the people who oversee and disburse grants.

The system is a **standalone project** (not part of Ravenkey MSP).

## Who Uses It

| Role | What They Do |
|------|-------------|
| Grant Manager | Reviews grantee compliance, understands audit findings, improves policy language |
| Program Officer | Oversees grant portfolios, identifies systemic issues across grantees |
| Policy Analyst | Drafts and refines grant guidelines, NOFOs, program policies |
| Auditor / Inspector | Cross-references findings across grantees, identifies patterns |

## What It Does

### Phase 1 — Read-Only Expert (current)

- Answers compliance questions with exact citations (report ID, recommendation number, section heading, quote)
- Cross-references multiple OIG reports to find patterns across grantees
- Maps audit findings to specific policy and regulatory requirements
- Identifies which recommendations apply to which grant programs
- Verifies answers against source evidence — flags unverifiable claims with abstention
- Distinguishes between public gov data (OIG reports, FAC, CFR) and uploaded org documents

### Phase 2 — Read-Write Agent (future)

- Draft and suggest edits to grant policies, NOFOs, and guidelines
- Generate compliance memos and policy briefs
- Propose corrective action plan templates based on audit findings
- Track policy changes across document versions
- Function calling for document editing tools

## Domain Coverage

**Primary:** OVC/VOCA grants — compliance, audit findings, program policies, grant regulations

**Expandable future domains (same architecture):**
- DOJ grant programs (VAWA, STOP Byrne JAG, etc.)
- HHS grant programs
- EPA, DOT, HUD grant compliance
- Any federal grant program with audit data

## Training Strategy: Fine-Tune + RAG

Helioryn uses both retrieval-augmented generation (RAG) and fine-tuning. RAG provides specific facts at inference time; fine-tuning teaches the model the deep patterns of grant compliance reasoning.

### RAG Layer (working now)

- Seeds knowledge base with government audit reports, FAC data, CFR excerpts
- Hybrid search (semantic + keyword, gov-seed boosted 2.0x)
- Gov seed prioritized in context (up to 15 chunks total)
- Abstention thresholds: verified ≥ 0.65, plausible ≥ 0.40
- Sources cited at recommendation / section level

### Fine-Tuning (next phase)

Goal: make the model inherently better at grant compliance reasoning than a generic 14B. Fine-tuning teaches the patterns — what audit findings look like, how recommendations relate to regulations, how compliance language is structured.

**Training data sources (public only):**
- **DOJ OIG reports** — oig.justice.gov (audit findings + recommendations)
- **Federal Audit Clearinghouse (FAC)** — fac.gov (single audit data)
- **SAM.gov** — assistance listings, grant terms and conditions
- **CFR Title 28** (DOJ) and **Title 45** (HHS) — grant regulations
- **grants.gov** — NOFOs, program guidelines
- **Federal Register** — policy notices, rule changes
- **CrimeSolutions.gov** — program effectiveness evidence
- **Navaa public materials** — training resources, policy interpretations
- **US Code 34** — Crime Control and Act (VOCA authorizing statute)

**Hardware constraint:** 16 GB unified memory on M4 MacBook Air limits inference to ~8–9 GB models in 4-bit.

**Fine-tuning options under evaluation:**
1. QLoRA on qwen2.5:14b — runs on 16 GB with gradient checkpointing, ~3–5 days
2. LoRA on qwen2.5:7b — proof of concept, then scale to 14B
3. Unsloth + Apple MLX — optimized for Apple Silicon, may get 14B training working on 16 GB
4. Cloud training (Lambda / Colab / RunPod) + local LoRA adapter inference

## Hardware

| Component | Spec |
|-----------|------|
| Machine | M4 MacBook Air |
| Memory | 16 GB unified |
| Peak model | ~8.5 GB (qwen2.5:14b Q4_K_M) |
| LLM backend | Ollama (default), OpenCode Go provider configured for future cloud use |

### Model Roadmap

1. ~~llama3.2:3b~~ — too small, weak instruction following
2. ✅ qwen2.5:7b — solid, better than 3B, current active
3. ⬅️ qwen2.5:14b — next step, best fit for 16 GB (pull and switch)
4. ⬜ Fine-tuned qwen2.5:14b variant — target state

## Key Design Principles

- Provenance is everything — every answer traces to an exact source location
- Citations are exact: report ID, recommendation number, section heading, verbatim quote
- Abstain rather than hallucinate — low-confidence answers are flagged, not fabricated
- Public government data and uploaded org documents are clearly separated via mode toggle
- Contradictions between sources are surfaced, not hidden
- All data sources are authoritative US government APIs — no web search engines
- The model serves grant **oversight** professionals, not grant recipients
- Built to be smarter than a human compliance analyst at pattern recognition across thousands of grant documents
