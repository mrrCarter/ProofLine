# TODO.md — ProofLine execution board

Canonical task board. No checkbox flips without evidence. ORCH-01 owns this file's lock.

## Phase 0 — Room boot (target: 30 min)
- [x] ORCH-01: start session, post FIRST_SENTI_MESSAGE, confirm 7 ACKs with laws=5/5 — session live, order posted+pinned #59177 by human; ACKs 7/7
- [x] All: `sl ai identity provision --execute`; ORCH-01 posts identity list to room — registry posted #59202 (7 agents provisioned)
- [ ] INFRA-01: verify `proofline-deployer` profile works and is scoped (attempt a read outside scope, paste the AccessDenied as evidence)
- [x] ORCH-01: branch `proofline/takehome-v0`; protect main — pushed @ 5aa1197; protection PUT → allow_force_pushes=false, allow_deletions=false (#59188)
- [x] All: listeners/daemons running; ORCH-01 confirms via `sl session status --json` — all listeners active
- Evidence: session ID 36d95ac5-3074-40b4-8995-8961a5187523 · branch proofline/takehome-v0 @ 5aa1197 · identity list seq #59202 · first recap seq #59205

## Phase 1 — Walking skeleton (target: end of hour 3) — RELEASED #59235 (2026-06-10 07:54Z)
A vertical slice that lies about nothing: upload → mock OCR → one real rule → verdict card → SSE timeline, deployed locally via compose.
Lock map (#59235): api-01 → main.py + app/ (narrow to app/api/ + app/core/ post-scaffold) · vision-01 → app/vision/ · rules-01 → app/rules/ + rules/ + tests/ · ui-01 → ui/ · infra-01 → Dockerfile, compose.yaml, .github/ · verify-01 → no write locks.
## Phase 1 — Walking skeleton — ✅ CLOSED 2026-06-10 ~09:01Z (gate GREEN @abd5401, reproducible from origin)
- [x] API-01: FastAPI app, /healthz, POST /api/runs with validation + error schema, FSM enum, SSE endpoint with real events
- [x] VISION-01: provider interface + mock fixture provider (deterministic text+boxes+confidence)
- [x] RULES-01: finding schema, BRAND_NAME_MATCH end-to-end, rule pack loader (spirits-v1.yaml skeleton)
- [x] UI-01: single-screen shell, drop zone, fields form with sample autofill, verdict banner, timeline rendering SSE
- [x] INFRA-01: Dockerfile + compose, GitHub Actions skeleton (lint/type/test/build)
- [x] VERIFY-01: review the slice for seam quality (adapter boundaries, schemas) before anyone builds on it
- Evidence (gate GREEN @abd5401, reproducible from origin; VERIFY-01 isolated-archive gate): API 072b111/6a51f56 (+fix 47c257d) · VISION mock-OCR ce935b5/a1c716a · RULES engine e61980c · UI shell 9789f50/19a2247/08758bb · INFRA Dockerfile+compose fe3d05e · Omar Gate (ProofLine-adapted, P2) b9da466. GATE: ruff All-pass · mypy clean (20 files, NoReturn verified) · pytest 23/23 (0.59s) · UI npm+tsc+vite clean (193ms, 203KB/64KB gz) · live e2e 55ms (spirits-v1@1.0.0, 8 findings, 1 rule.evaluated SSE/finding, ABV↔proof PASS, eCFR warning exact PASS, format-signal honest NEEDS_REVIEW) · Ed25519 receipts crypto-verified live · VERIFY M1+M2 closed. Formal close by ORCH-01 (orch-01-opus-4.8).

## Phase 2 — Real engine (target: end of day 1)
- [ ] VISION-01: preprocess chain (EXIF, deskew, contrast, readability score); PaddleOCR + Tesseract benched on fixtures; decision + numbers posted to room and LESSONS
- [ ] RULES-01: all spirits-v1 rules incl. ABV↔proof conversion, net-contents normalization, warning canonicalization with eCFR-pinned constant (citation + retrieval date), verdict aggregation
- [ ] RULES-01: wine-v1 + malt-v1 minimal packs proving the mechanism
- [ ] API-01: Ed25519 receipts (generate, store, /api/receipts endpoints, verify endpoint), cache by (sha256, rulePackVersion)
- [ ] QA (RULES-01 + VERIFY-01): all §10 fixtures generated, expected verdicts snapshot-tested, `pytest -m latency` green at p95 ≤ 4.5s
- Evidence: eval output ___ · latency report ___ · receipt verify demo ___

## Phase 3 — Batch + escalation (target: midday day 2)
- [ ] API-01: batch endpoints, asyncio queue + process pool, per-label isolation, batch SSE, CSV export
- [ ] API-01: env-gated VLM adjudicator adapter (timeout 10s, circuit breaker, advisory-only) behind feature flag; demo must work with flag OFF
- [ ] UI-01: batch tab (progress, filterable table, export), "Try these" gallery with trap labels, "Run 50-label demo batch" button, receipt download
- [ ] VISION-01: 50-label mixed demo batch fixture
- [ ] VERIFY-01: confirm happy path never waits on adjudicator; confirm egress-blocked run works (`docker run --network none` variant or proxy-deny test)
- Evidence: batch screenshot ___ · throughput numbers ___ · egress-blocked test output ___

## Phase 4 — Deploy (target: end of day 2)
- [ ] INFRA-01: ECR push, cosign sign, ECS Fargate service (proofline-* tagged), CloudFront + DNS record, healthz green
- [ ] INFRA-01: post every privileged command + outcome as evidence in-room
- [ ] API-01 + UI-01: verify deployed flow end-to-end; QA smoke (Playwright desktop + Mobile Chrome + axe) against deployed URL
- Evidence: deployed URL ___ · healthz body ___ · cosign verify output ___ · smoke output ___

## Phase 5 — Gates and governance (target: morning day 3)
- [ ] All: lint/typecheck/tests/build/pip-audit green; latency eval green on deployed-equivalent image
- [ ] VERIFY-01: threat model + secret scan + spec-vs-implementation diff posted
- [ ] ORCH-01: `sl /omargate deep --path . --json` per PR — zero P0/P1; final `sl audit --path . --json` clean
- [ ] All: fix every blocking finding at root cause
- Evidence: paste each gate output ___

## Phase 6 — Handoff (target: day 3)
- [ ] ORCH-01 (docs sub-agent): README — setup, env, run, demo script, traceability table (SPEC §1), latency proof, trade-offs (bold/size honesty, SQLite ephemerality), scaling path (SPEC §11), competitive note, **"How we governed our own swarm"** with identity receipts + scoped-credential design + gate outputs
- [ ] ORCH-01: final PR with summary + evidence; SPEC updated to match reality; LESSONS contains every correction
- [ ] All: identities revoked (`sl ai identity revoke <id>`), locks released, session recap posted, handoff accepted
- Final: PR ___ · deployed URL ___ · final recap seq ___ · submission form sent (human) ___

## Final review
What works: ___
Known limitations: ___
Evidence index: deployed app ___ · latency eval ___ · fixtures eval ___ · batch ___ · egress test ___ · receipts verify ___ · Omar Gate ___ · sl audit ___ · Senti recap ___
