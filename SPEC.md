# SPEC.md — ProofLine v2 (source of truth)

**ProofLine: evidence-receipt label verification for TTB-style review.**

One sentence for humans: upload a label and the application fields, get PASS / NEEDS_REVIEW / FAIL / UNREADABLE in under 5 seconds, with a cryptographically signed evidence receipt explaining every finding.

One sentence for the interview: a governed-AI compliance engine, built by a governed-AI engineering swarm, with signed receipts at both layers.

**Project type:** take-home prototype (Treasury/TTB-style assessment) with a documented venture-grade scaling path. Deadline-driven: 1–3 day build, submit well inside the one-week window.

---

## 0. Non-negotiable design laws

Every agent memorizes these five. Everything else in this spec serves them.

1. **5 seconds or it doesn't exist.** Single-label verdict p95 ≤ 5s, p50 ≤ 3s. A CI eval fails the build if fixtures exceed budget. (Sarah: the last vendor died at 30–40s.)
2. **Nothing leaves the box.** Default pipeline (OCR included) runs entirely inside the container. Zero outbound calls required for the deployed demo to work. Cloud OCR/VLM adapters exist but are env-gated OFF. (Marcus: their firewall killed the last vendor's ML endpoints.)
3. **Deterministic rules own the verdict.** AI may propose, adjudicate, and explain. AI never silently overrides a deterministic finding, and the warning-statement check is never delegated to a model. (Jenny: exactness. Dave: trust.)
4. **Every verdict ships a signed receipt.** Ed25519-signed evidence JSON: artifact hash, rule-pack version, provider versions, per-finding evidence, timestamps, latency. This is the differentiator. No receipt, no verdict.
5. **Simple face, sophisticated spine.** One screen, one big upload area, one Verify button, verdicts a 73-year-old can read. All sophistication lives underneath and in the receipt. (Sarah's mother test.)

---

## 1. Requirements traceability — the personas are the rubric

Each stakeholder quote encodes a grading criterion. README must reproduce this table.

| Persona | Quote (paraphrased) | Feature | Evidence we ship |
|---|---|---|---|
| Sarah | "5 seconds or nobody uses it" | Local OCR fast path, latency budget, timing eval in CI | `eval:latency` output in README + receipt latencyMs |
| Sarah | "Batch of 200–300 from importers" | Async batch with per-label isolation, SSE progress, CSV export | 50-label demo batch button |
| Sarah | "My 73-year-old mother could use it" | Single-screen flow, giant verdict cards, plain-English findings | Screenshots + axe a11y smoke |
| Marcus | "Firewall blocked their ML endpoints" | In-container OCR default, zero required outbound | Network section of README; demo works with egress blocked |
| Marcus | "Standalone, no COLA integration" | No COLAs Online coupling; standalone deploy | Architecture doc |
| Dave | "STONE'S THROW vs Stone's Throw — judgment" | Bounded fuzzy match: normalize case/punct/whitespace, report normalization transparently, PASS with note | `brand_case_equivalent` fixture |
| Dave | "Don't make my life harder" | Verdict-first UI, evidence one tap away, no config | Demo script |
| Jenny | "Warning must be EXACT, caps, bold" | Verbatim CFR text check + caps check + bold signal heuristic with honest confidence | `warning_title_case` fixture FAILS |
| Jenny | "Weird angles, glare, bad lighting" | Preprocess (EXIF rotate, deskew, contrast), readability score, UNREADABLE verdict instead of false pass | `glare_low_confidence` fixture |
| Assessment | "Attention to requirements" | This table | This table |

---

## 2. Architecture decision

### Chosen: one container, deterministic fast path, gated agentic escalation, receipts always

```text
Browser (React SPA, served as static files)
   │ multipart upload + application fields
   ▼
FastAPI (single service, single container)
   │ sha256 → cache check → run created (FSM: RECEIVED)
   ▼
Preprocess (Pillow/OpenCV): EXIF orient, deskew, contrast, readability score   [≤400ms]
   ▼
OCR (in-container: Tesseract primary; PaddleOCR env-gated upgrade adapter when cp314 wheels exist) → words+boxes+conf   [≤2.5s]
   ▼
Field extraction (deterministic parsers + layout heuristics)                   [≤150ms]
   ▼
Rule engine (versioned YAML rule packs per commodity)                          [≤150ms]
   ▼
Verdict + Ed25519 receipt  ──────────────► SSE event stream ─► UI timeline
   │
   └─ low confidence / conflict? → FSM: ESCALATED → VLM adjudicator adapter
      (env-gated, 10s timeout, circuit breaker, result is advisory → NEEDS_REVIEW)
```

Storage: SQLite (WAL mode) for runs/findings/receipts + local artifact volume. S3 adapter optional. Batch: in-process asyncio queue + ProcessPoolExecutor for OCR (CPU-bound). **No Redis. No Postgres. No external queue.**

### Why this and not the alternatives

**Rejected: swarm-per-label runtime.** Cost: 10–40s and dollars per label, non-deterministic verdicts on a compliance task, fails laws 1–3 simultaneously. Agents enter only on escalation, where ambiguity makes them worth their latency. The state machine decides when agents are allowed in the room. (This is the v1 pushback, kept and sharpened: it is also the interview's strongest sentence.)

**Rejected: single grounded-schema LLM.** Plausible JSON, unauditable reasoning, irreproducible verdicts, hopeless at verbatim regulatory text, and requires outbound calls (law 2). Used only as the gated adjudicator.

**Rejected: Next.js + FastAPI + Postgres + Redis + queue (v1 spec).** Five pieces of infrastructure for image-in → checks → verdict-out is the over-engineered-MVP trap. The assessment grades "appropriate technical choices for the scope." Venture-backable means clean seams and a written scaling path, never demo-Kafka. The hyperscale story lives in §11 and in the interfaces, both of which survive contact with investors; deployed Redis does not survive contact with a grader's curiosity.

**Rejected: cloud OCR as priority 1 (v1 spec).** Marcus's story is the lesson: the last vendor's outbound ML calls got firewalled. Azure Document Intelligence remains an env-gated adapter and the documented Azure-container production path, but the deployed demo depends on nothing external. Bonus: no third-party key can expire and break the URL three days later when a grader finally clicks it.

### Runtime FSM (mirror of the AIdenID clearance philosophy)

`RECEIVED → PREPROCESSED → EXTRACTED → RULED → {PASS | FAIL | NEEDS_REVIEW | UNREADABLE}`
with `RULED → ESCALATED → ADJUDICATED → NEEDS_REVIEW(annotated)` and `* → ERROR`.

Every transition emits an SSE event. Same 6-outcome philosophy as AIdenID clearance (allow/deny/queue/sandbox/throttle/priced ↔ pass/fail/review/unreadable/escalated/error). Say that sentence in the interview.

---

## 3. Latency budget (enforced, not vibes)

| Stage | p50 | p95 ceiling |
|---|---|---|
| Upload + hash + cache check | 80ms | 200ms |
| Preprocess | 250ms | 400ms |
| OCR (local, ~1500px image) | 1.2s | 2.5s |
| Extraction + rules + receipt | 200ms | 400ms |
| API → first verdict paint | 150ms | 300ms |
| **Total** | **~2s** | **≤4s** (1s headroom under the 5s law) |

`pytest -m latency` runs the full pipeline on 10 fixtures and **fails CI** if p95 > 4.5s. Re-verification of a seen artifact hash + same normalized application-data hash + same rule version returns cached verdict in <300ms.

Batch math, stated honestly in README: 300 labels on a 2-vCPU Fargate task with 2 OCR workers ≈ 5–6 min with live per-label progress; 4 vCPU halves it; production path is horizontal workers (§11).

---

## 4. Rule packs (versioned YAML per commodity)

`rules/spirits-v1.yaml` (complete), `rules/wine-v1.yaml`, `rules/malt-v1.yaml` (core checks; demonstrate the pack mechanism handles commodity variation). Every receipt records `rulePackId@version`.

### Mandatory checks (spirits v1)

| ruleId | Type | Logic |
|---|---|---|
| BRAND_NAME_MATCH | bounded-fuzzy | Normalize case, punctuation, apostrophe style, whitespace; rapidfuzz ratio ≥ 0.93 on normalized = PASS with a visible "normalized match" note (Dave's case); below = FAIL with both raw strings |
| CLASS_TYPE_MATCH | bounded-fuzzy | Same normalization; class/type vocabulary aware |
| ALCOHOL_CONTENT_MATCH | numeric-equivalence | Parse `45% Alc./Vol.`, `45% ABV`, `90 Proof`. **US spirits: proof = 2 × ABV.** Application 45% vs label "90 Proof" = PASS with conversion note. Mismatch beyond ±0.05% = FAIL |
| NET_CONTENTS_MATCH | unit-normalization | 750 mL ≡ 75 cL ≡ 0.75 L; compare in mL |
| NAME_ADDRESS_PRESENT | presence | Bottler/producer name + city/state detected |
| COUNTRY_OF_ORIGIN_IF_IMPORT | conditional-presence | Required iff application says imported |
| GOVERNMENT_WARNING_PRESENT | anchor-detect | Locate "GOVERNMENT WARNING" anchor |
| GOVERNMENT_WARNING_EXACT_TEXT | verbatim | See canonicalization below |
| GOVERNMENT_WARNING_FORMAT_SIGNAL | heuristic | Caps + bold signal, honest confidence (below) |
| IMAGE_READABILITY | preprocessing | Readability score from OCR mean confidence + blur/glare metrics; below threshold → UNREADABLE, never a confident pass |

### Warning-statement canonicalization (the trap they will test)

Required text per **27 CFR 16.21** — RULES-01 must fetch the live eCFR text at build time, pin it as a constant character-for-character, and put the citation URL + retrieval date in a comment. Do not trust any model's memory of it, including this spec's. Expected constant:

> GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

Comparison algorithm:
1. Collapse whitespace/line breaks (labels wrap the statement) — this is the ONLY normalization applied.
2. Statement body compared **verbatim, case-sensitive**.
3. "GOVERNMENT WARNING" prefix: case deviation with OCR confidence ≥ 0.9 on those tokens = **FAIL** (Jenny rejected title case; so do we). Confidence < 0.9 = NEEDS_REVIEW with the cropped evidence shown.
4. Bold: photos can't prove font weight. Stroke-width ratio vs surrounding body text → `boldSignal: likely | unlikely | indeterminate` with confidence. `unlikely` = NEEDS_REVIEW with crop; never a hard FAIL on bold alone, and never a silent pass. 27 CFR 16.22 type-size rules are reported as a relative-size ratio signal (can't measure millimeters from an unscaled photo — say so in the finding). Honesty here is a feature; the README documents it as a deliberate trade-off.

### Verdict aggregation

FAIL if any HIGH/CRITICAL deterministic failure. UNREADABLE if readability below floor. NEEDS_REVIEW if any low-confidence finding, format signal indeterminate, or adjudicator conflict. PASS only when all mandatory checks pass with sufficient confidence. Findings carry: ruleId, severity, status, expected, observed (raw + normalized), confidence, evidence (text, bbox, cropUri, provider), explanation, remediation.

---

## 5. Signed evidence receipts (the feature nobody else ships)

Per run, generate and store:

```json
{
  "receiptVersion": "1",
  "runId": "...", "requestId": "...",
  "artifactSha256": "...",
  "rulePack": "spirits-v1@1.0.0",
  "providers": {"ocr": "tesseract@x.y (local)", "adjudicator": null},
  "verdict": "FAIL",
  "findings": [ ...full findings... ],
  "timings": {"totalMs": 2140, "stages": {...}},
  "createdAt": "...",
  "signature": "ed25519:...", "publicKeyId": "proofline-2026-06"
}
```

Sign canonical JSON with Ed25519 (PyNaCl). Result-cache key is `(artifactSha256, normalizedApplicationDataHash, rulePackVersion)`; the application-data hash is computed from normalized application fields with canonical JSON ordering. Endpoints: `GET /api/receipts/:runId`, `GET /api/receipts/pubkey`, `POST /api/receipts/verify`. README includes a 5-line verification snippet. Interview line: "any verdict can be independently re-verified years later — same pattern as AIdenID's clearance receipts." ~1 hour of work, infinite differentiation.

---

## 6. API surface

```
POST /api/runs                  multipart: image + fields           → runId
GET  /api/runs/:id              verdict + findings + receipt ref
GET  /api/runs/:id/events       SSE
POST /api/batches               multipart: zip/multi + fields CSV   → batchId
GET  /api/batches/:id           summary + per-label statuses
GET  /api/batches/:id/events    SSE
GET  /api/batches/:id/export.csv
GET  /api/fixtures              seeded demo labels
GET  /api/receipts/:runId | /pubkey ; POST /api/receipts/verify
GET  /healthz                   build sha, rule pack versions, OCR provider
```

Error schema everywhere: `{error: {code, message, details, requestId}}`. Uploads: size ≤ 15MB, types jpeg/png/webp/heic/pdf (pillow-heif; graders WILL photograph a bottle with an iPhone), magic-byte validation, EXIF-orientation honored.

### SSE taxonomy

`run.created, preprocess.completed, ocr.completed{provider,confidence,latencyMs}, field.extracted, rule.evaluated{ruleId,status}, run.escalated{reason}, agent.spawned{role,reason}, agent.opinion{decision,rationale}, run.completed{status,latencyMs,receiptId}` + batch.* equivalents. UI renders these as the Orchestrator Timeline — the governance is visible, not narrated.

---

## 7. UI spec (the 73-year-old test)

Single page, three zones top-to-bottom: (1) drop zone + "Try these" gallery, (2) application fields with "Use sample" autofill, (3) giant Verify button. Result: full-width verdict banner using color + icon + word (PASS ✓ green / FAIL ✕ red / NEEDS REVIEW ⚠ amber / UNREADABLE 📷 gray — never color alone), time-to-result, findings as cards with expected vs observed and tap-to-zoom evidence crops, collapsible Orchestrator Timeline, Download Receipt button. Batch tab: progress bar, filterable table, CSV export. 360px-wide mobile works. Keyboard accessible, visible focus, ≥16px body text, large targets. Playwright smoke + axe pass.

**Reviewer-experience requirement:** a cold grader must hit a trap and see it caught within 30 seconds of landing. The "Try these" gallery front-loads: passing bourbon, title-case warning (FAIL with crop), 90-Proof-only label (PASS with conversion note), glare photo (UNREADABLE), plus a "Run 50-label demo batch" button. Demo theater is a deliverable.

---

## 8. Build-swarm governance (the part that wins the interview)

The build itself runs under the same philosophy as the product:

- **Identity:** each agent gets an ephemeral AIdenID identity at session start (`sl ai identity provision --execute`); identities listed in the room and revoked at session end (`sl ai identity revoke`).
- **Credential scoping (hard rule):** only INFRA-01 holds deploy credentials, via a dedicated `proofline-deployer` IAM role/profile restricted to `proofline-*`-tagged resources (ECR repo, ECS service, target group, log group) and a single Cloudflare token scoped to the one DNS record. No agent ever touches the default profile, the aidenid.com hosted zone, or org-wide tokens. GitHub access via a fine-grained PAT scoped to this repo only.
- **Destructive-op stop list (extends AGENTS.md stop conditions):** `aws s3 rb`, `aws ecs delete-*`, `aws ecr delete-repository`, any route53/cloudfront mutation outside the proofline record, `terraform destroy`, `gh repo delete`, force-push to main. These require an explicit human message in the Senti room.
- **Evidence:** every privileged action (deploy, DNS, secret injection) is posted to the room with command + outcome, no secret values. The session transcript becomes the build's own evidence receipt.
- **Gates:** Omar Gate (`sl /omargate deep`) on every PR, P0/P1 block; `sl audit --path . --json` full 15-agent swarm before the final PR.

README gets a section: **"How we governed our own swarm"** — roster, identity receipts, scoped credentials, gate results. The interview line: "we didn't just build a governed verifier; we built it under governance, and here are the receipts for both layers."

---

## 9. Security (product)

No secrets in repo/logs/receipts. Server-side upload validation (size/type/magic bytes). Artifacts private, TTL cleanup job (24h default, documented). Rate limit upload endpoints. SSRF: no URL-fetch of labels in v0. Request IDs everywhere. `pip-audit` + secret scan in CI. cosign-sign the container image (existing pipeline; one README line of flex). Production path documented: Azure container OCR / GovCloud / private networking — matching Marcus's world without building it.

---

## 10. Evals and fixtures

Generated fixture set (AI image gen per the assessment's own suggestion), each with expected verdict + expected finding IDs, snapshot-tested:

1. `pass_bourbon` — clean PASS
2. `brand_case_equivalent` — STONE'S THROW vs Stone's Throw → PASS + normalization note
3. `brand_material_mismatch` — FAIL
4. `abv_mismatch` — FAIL
5. `proof_only_equivalent` — label "90 Proof", application 45% → PASS + conversion note
6. `net_contents_unit_equiv` — 75 cL vs 750 mL → PASS
7. `warning_missing` — FAIL
8. `warning_title_case` — FAIL (high-conf caps deviation)
9. `warning_small_font_signal` — NEEDS_REVIEW with size-ratio finding
10. `import_missing_origin` — FAIL
11. `glare_low_confidence` — UNREADABLE/NEEDS_REVIEW
12. `wine_basic` + `malt_basic` — rule-pack mechanism proof
13. `batch_mixed_50.zip` — synthetic 50-label throughput + isolation demo

Cache-key regression: same image + same normalized application data may cache; same image + different normalized application data must recompute and can produce a different verdict/receipt.

Gates: pytest unit+eval, `pytest -m latency` (§3), Playwright smoke (desktop + Mobile Chrome), axe, lint/typecheck/build, pip-audit, Omar Gate. All green before "done" exists.

---

## 11. The venture-grade scaling path (written, not deployed)

README architecture section carries the math: 150K applications/year ≈ 600/working day ≈ trivially one box; the real production load is the importer spike (300 in an hour) and the audit trail. Path: same container image → horizontal OCR workers behind SQS (the asyncio queue interface swaps for an SQS adapter — interface already exists), Postgres swap via the storage adapter, receipts to immutable storage (S3 object-lock), rule packs as signed artifacts, Azure-container OCR variant for inside-the-firewall deployment. Every seam in the prototype is the production seam. That paragraph IS the hyperscale story; nothing about it requires deploying it this week.

---

## 12. Competitive position (June 2026, for README + interview)

- **COLAClear** — public beta May 2026; producer-side TTB pre-screen (CV + LLM for ambiguity + CFR-grounded rules). Validates the architecture and the timing. Their lane: producers pre-submission. Our lane: the reviewer side — batch triage of importer dumps, queue prioritization, and signed receipts an agency can audit. Same engine, opposite side of the counter.
- **Sovos ShipCompliant** — beverage-compliance workflow incumbent with COLA submission integration; workflow suite, not evidence infrastructure.
- **GlobalVision et al.** — packaging artwork QA; pixel-proofing, not regulatory field matching.
- **Hyperscaler Document AI** — generic extraction; no rules, no receipts, no governance.

Wedge sentence: "Everyone can OCR a label. We issue the signed receipt that proves what was checked, by which rules, at which version — and we govern the AI that helped." Five Forces summary lives in README appendix; supplier power neutralized by local OCR + adapters, substitute (manual review) attacked at the queue, not at the judgment.

If Treasury never calls back: ProofLine is not a label company. It is the first vertical demo of **AIdenID Receipts** — evidence-receipt infrastructure for regulated AI decisions (labels today; KYC docs, claims, safety filings next). Harvest the pattern, keep the demo, feed the J2 dual-use thesis.

---

## 13. Acceptance criteria (Definition of Done)

Deployed URL live behind CloudFront with healthz green · cold-grader 30-second trap demo works · single-label p95 ≤ 4.5s proven by CI eval output pasted in README · 50-label batch completes with progress + CSV export · all §10 fixtures produce expected verdicts · receipts download and verify against pubkey · works with all outbound egress blocked (tested) · README has setup, env, demo script, traceability table, trade-offs, scaling path, swarm-governance section · lint/typecheck/tests/build/audit green · Omar Gate no P0/P1 · `sl audit` clean · TODO evidence table filled · LESSONS updated with every correction.
