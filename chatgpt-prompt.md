# OpenCode / DeepSeek Master Prompt

You are helping build a domain-specific AI assistant for OVC, VOCA, DOJ grant compliance, audits, monitoring, policy changes, and federal financial management.

## Objectives
Build a citation-first RAG system using authoritative sources, not memorization.

## Priority data sources
1. DOJ Grants Financial Guide
2. 2 CFR Part 200 (Uniform Guidance)
3. 28 CFR Part 94 (VOCA)
4. OVC guidance and monitoring pages
5. DOJ OIG grant audit reports
6. State VOCA manuals (ND, OR, MO, OK, VA, etc.)
7. Current NOFOs, award conditions, FAQs, webinars, and policy updates.

Download and ingest HTML/PDF content with metadata:
- source
- title
- URL
- section
- effective_date
- document_version
- jurisdiction
- document_type
- citation
- text

## Recommendations
- Prefer RAG over fine-tuning.
- Chunk 800–1200 tokens with overlap.
- Use BGE-M3 or nomic-embed-text embeddings.
- Store in Qdrant.
- Always return citations.

## Questions for my existing ingestion pipeline
1. Review my current ingestion pipeline. What should change to support:
   - versioned documents
   - superseded policies
   - citation-ready chunks
   - effective dates
   - HTML + PDF normalization
   - duplicate detection
   - incremental updates
   - monitoring of changed federal guidance?

2. Recommend improvements for parsing CFR, DOJ HTML, PDFs, tables, appendices, and scanned audit reports.

3. How should I structure metadata for future policy changes?

## Questions about Qdrant schema
Review my current schema and recommend improvements.
Should collections separate:
- Regulations
- Financial Guide
- Audit Reports
- State Manuals
- Award Conditions
- FAQs

Recommend payload fields, filters, hybrid search, versioning, and citation support.

## Questions about prompt template
Review my current system prompt.
Recommend a citation-first prompt that:
- never invents policy
- distinguishes regulation vs guidance
- quotes only when necessary
- cites exact document, section, and effective date
- states when evidence is insufficient
- explains reasoning without exposing chain of thought.

## Desired output
Provide architecture recommendations, migration steps from my existing system, risks, performance improvements, and production best practices.

