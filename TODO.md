# TODO.md — ProofLine execution board

Canonical task board. No checkbox flips without evidence. ORCH-01 owns this file's lock.

## Phase 0 — Room boot (target: 30 min)
- [ ] ORCH-01: start session, post FIRST_SENTI_MESSAGE, confirm 7 ACKs with laws=5/5
- [ ] All: `sl ai identity provision --execute`; ORCH-01 posts identity list to room
- [ ] INFRA-01: verify `proofline-deployer` profile works and is scoped (attempt a read outside scope, paste the AccessDenied as evidence)
- [ ] ORCH-01: branch `proofline/takehome-v0`; protect main
- [ ] All: listeners/daemons running; ORCH-01 confirms via `sl session status --json`
- Evidence: session ID ___ · branch ___ · identity list seq ___ · first recap seq ___

## Phase 1 — Walking skeleton (target: end of hour 3)
A vertical slice that lies about nothing: upload → mock OCR → one real rule → verdict card → SSE timeline, deployed locally via compose.
- [ ] API-01: FastAPI app, /healthz, POST /api/runs with validation + error schema, FSM enum, SSE endpoint with real events
- [ ] VISION-01: provider interface + mock fixture provider (deterministic text+boxes+confidence)
- [ ] RULES-01: finding schema, BRAND_NAME_MATCH end-to-end, rule pack loader (spirits-v1.yaml skeleton)
- [ ] UI-01: single-screen shell, drop zone, fields form with sample autofill, verdict banner, timeline rendering SSE
- [ ] INFRA-01: Dockerfile + compose, GitHub Actions skeleton (lint/type/test/build)
- [ ] VERIFY-01: review the slice for seam quality (adapter boundaries, schemas) before anyone builds on it
- Evidence: local URL ___ · screenshot ___ · smoke output ___

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
