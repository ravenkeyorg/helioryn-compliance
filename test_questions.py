#!/usr/bin/env python3
"""Run all 11 hard questions against the dev server, save results to /tmp/helioryn_tests/"""
import json, os, requests, sys, time

URL = "http://localhost:8765/api/chat"
OUT = "/tmp/helioryn_tests"
os.makedirs(OUT, exist_ok=True)

QUESTIONS = [
    {
        "id": "q01",
        "label": "Subrecipient audit consequences",
        "question": "What are the consequences of a subrecipient failing a Single Audit under 2 CFR 200.514? Cite specific paragraphs.",
        "mode": "public"
    },
    {
        "id": "q02",
        "label": "Admin funds for subrecipient monitoring",
        "question": "Can a state VOCA administering agency use administrative funds to monitor subrecipients? Cite the specific authority in 28 CFR 94.106 or other regulations.",
        "mode": "public"
    },
    {
        "id": "q03",
        "label": "OIG findings on subrecipient monitoring",
        "question": "What specific OIG report findings relate to subrecipient monitoring failures under 2 CFR 200.332? For each, give the exact finding and which paragraph of 200.332 was violated.",
        "mode": "public"
    },
    {
        "id": "q04",
        "label": "Internal controls 200.303 + OIG deficiencies",
        "question": "List every type of internal control required by 2 CFR 200.303, enumerating each sub-requirement. For each, cite which OIG report (if any) found a deficiency in that area.",
        "mode": "public"
    },
    {
        "id": "q05",
        "label": "200.332(b) 14 data elements",
        "question": "Under 2 CFR 200.332(b), enumerate all required data elements that a pass-through entity must include in every subaward. Then identify which OIG reports flagged missing subaward data elements.",
        "mode": "public"
    },
    {
        "id": "q06",
        "label": "Subrecipient vs contractor 6-factor test",
        "question": "Under 2 CFR 200.330 and 200.331, how does a pass-through entity determine whether a recipient is a subrecipient vs a contractor? Give the complete 6-factor test from 200.331 with exact wording.",
        "mode": "public"
    },
    {
        "id": "q07",
        "label": "Below-threshold subrecipient obligations",
        "question": "What happens if a subrecipient does not meet the $1,000,000 audit threshold in 2 CFR 200.501? Does the pass-through entity still have monitoring obligations under 200.332? Cite specific paragraph numbers.",
        "mode": "public"
    },
    {
        "id": "q08",
        "label": "Full VOCA grant lifecycle",
        "question": "Walk through the complete grant lifecycle for a VOCA subaward: from application (28 CFR 94.103) through monitoring (2 CFR 200.332) to closeout. For each step, cite the exact regulation or section.",
        "mode": "public"
    },
    {
        "id": "q09",
        "label": "OIG reports citing 200.332",
        "question": "Find which OIG reports cite 2 CFR 200.332 directly. For each report, what was the specific finding and what corrective action was recommended?",
        "mode": "public"
    },
    {
        "id": "q10",
        "label": "VOCA-specific OIG common deficiencies",
        "question": "Which OIG reports specifically involve VOCA victim assistance funds (not OVW or other OJP programs)? What common compliance deficiencies did they find across states?",
        "mode": "public"
    },
    {
        "id": "q11",
        "label": "VOCA 28 CFR 94 vs Uniform Guidance comparison",
        "question": "Compare the subrecipient monitoring requirements for VOCA formula grants under 28 CFR 94 against the Uniform Guidance default in 2 CFR 200.332. Where does VOCA impose additional requirements beyond the Uniform Guidance?",
        "mode": "public"
    },
]

logf = open(os.path.join(OUT, "_run_log.txt"), "w")

for q in QUESTIONS:
    qid = q["id"]
    label = q["label"]
    print(f"\n=== {qid}: {label} ===", flush=True)
    logf.write(f"\n=== {qid}: {label} ===\n")
    logf.flush()

    payload = {
        "question": q["question"],
        "mode": q.get("mode", "public"),
    }

    start = time.time()
    try:
        r = requests.post(URL, json=payload, timeout=600)
        elapsed = time.time() - start
        data = r.json()
        data["_elapsed_seconds"] = round(elapsed, 1)

        # Save full response
        with open(os.path.join(OUT, f"{qid}.json"), "w") as f:
            json.dump(data, f, indent=2, default=str)

        ans = data.get("answer", "NO ANSWER")
        sources = data.get("sources", [])
        ver = data.get("verification", {})

        logf.write(f"Time: {elapsed:.0f}s\n")
        logf.write(f"Sources: {len(sources)}\n")
        logf.write(f"Verification: {json.dumps(ver, default=str)[:200]}\n")
        logf.write(f"Answer ({len(ans)} chars):\n{ans[:3500]}\n\n")
        logf.flush()

        print(f"  Time: {elapsed:.0f}s | Sources: {len(sources)} | Answer: {len(ans)} chars")

    except Exception as e:
        elapsed = time.time() - start
        logf.write(f"ERROR after {elapsed:.0f}s: {e}\n")
        logf.flush()
        print(f"  ERROR after {elapsed:.0f}s: {e}")

logf.close()
print(f"\nDone. Results in {OUT}/")
