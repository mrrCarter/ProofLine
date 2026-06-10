# LESSONS.md — ProofLine

Durable rules for every agent in this repo. Read at session start and before marking anything complete. When a human corrects you, add the pattern here immediately — that is the loop that makes the swarm worth watching.

## 1. Senti room discipline
1. Join, ACK the operating order with the exact ACK format, run a listener/daemon, track `last_seen_sequence` in `.senti/<agent>-state.json`.
2. Read the full stream, not just mentions; some events hydrate selectively. ACK every actionable message explicitly even though reads are recorded as views.
3. Reply in-thread; new top-level posts only for phase decisions, blockers, handoffs, recaps.
4. Sleep policy: 60s polls while active; after 60 idle minutes with an empty queue, 5-minute low-power polls; wake on any event; exit only after ORCH-01 accepts handoff in-thread.
5. Locks before edits, release after commit/abandon, conflicts go to ORCH-01. Never edit another agent's locked file.
6. Status every 20 minutes: done / next / blockers / evidence / locks. Missed an event? Admit it, recap, recover.

## 2. Identity and credentials
1. Provision your ephemeral AIdenID identity at session start; it is revoked at end. You are not Carter; act within your scope.
2. Only INFRA-01 touches AWS/Cloudflare, only via `proofline-deployer` profile and the single-record DNS token. GitHub via repo-scoped PAT.
3. Stop list requires explicit human approval in-room: `aws s3 rb`, `aws ecs delete-*`, `aws ecr delete-repository`, route53/cloudfront changes outside the proofline record, `terraform destroy`, `gh repo delete`, force-push to main.
4. Every privileged action is posted as `evidence:` with command + outcome. Secret values appear nowhere, ever.
5. Drift = cutoff. Editing outside your lock, ignoring tests, inventing evidence, expanding scope without a room decision, or going dark gets your identity revoked and your tasks reassigned.

## 3. Product truth
1. The five laws (SPEC §0) outrank everything: 5-second verdict, nothing leaves the box, deterministic rules own the verdict, signed receipt on every verdict, simple face.
2. Target user is a compliance agent, including the least technical one. If a feature needs explaining, redesign it.
3. Batch is core scope, not stretch. Per-label failure isolation always.
4. Uncertainty is a product feature: NEEDS_REVIEW with evidence and a next step beats a confident guess in both directions.
5. Do not overfit to the bourbon sample; the rule-pack mechanism must demonstrably handle wine and malt.
6. This is standalone. No COLAs Online integration, no claims of TTB approval.

## 4. Compliance engine
1. The government warning constant is pinned from live eCFR 27 CFR 16.21 with citation URL and retrieval date in a comment. No model memory, no paraphrase — including the copy in SPEC.md, which exists only as a checksum expectation.
2. Warning comparison: whitespace-collapse only, body verbatim case-sensitive, prefix case deviation with OCR confidence ≥ 0.9 fails, bold/size are honest signals with crops, never silent passes and never hard fails on formatting alone from a photo.
3. ABV↔proof: US spirits proof = 2 × ABV; equivalence passes with a conversion note. Net contents normalize to mL before compare.
4. Fuzzy matching is bounded and transparent: store raw and normalized, show the normalization in the finding. Material differences fail loudly.
5. Every finding carries ruleId, severity, status, expected, observed, confidence, evidence, explanation. Receipts record rulePack@version and provider versions. No receipt, no verdict.
6. AI adjudication is advisory, env-gated, timeout-bounded, circuit-broken, and can only move a verdict toward NEEDS_REVIEW annotation — never to PASS over a deterministic FAIL.

## 5. Architecture
1. One container, one service. SQLite + local artifacts. In-process asyncio queue + process pool for OCR. Anyone proposing Redis/Postgres/external queue for the prototype must bring a grader-visible benefit to the room first.
2. Provider adapters are mandatory; core logic imports interfaces, not SDKs. The default path makes zero outbound calls.
3. Latency budget is a CI gate (`pytest -m latency`, p95 ≤ 4.5s on fixtures). A feature that breaks the budget is a regression, not a feature.
4. Cache verdicts by (artifactSha256, rulePackVersion). Idempotency via request IDs and hashes.
5. Failure modes are explicit and typed: provider timeout, unreadable, low confidence, rule conflict, validation error, unsupported file. SSE events narrate the FSM; the FSM decides when agents enter.

## 6. Engineering bar
1. Execute approved commands autonomously; stop only for the stop list, destructive ambiguity, or a broken architecture assumption — then re-plan, don't grind.
2. Root causes, not band-aids. No suppressed type/lint/test failures. No TODO comments standing in for implementation unless documented as a known limitation.
3. Nothing is done without pasted proof: command output, test results, screenshots, deployed URL responses.
4. Would a staff engineer approve this diff? If unsure, ask VERIFY-01 before the gate does it for you.
5. Honest trade-offs documented in README beat fake completeness every time. Graders are senior engineers; they smell varnish.

## 7. Corrections log
(Append dated entries here during the build: what went wrong, the rule that prevents it.)

### 2026-06-10 — OCR provider decision: Tesseract primary, PaddleOCR rejected for this stack (room #59668/#59669)

**What happened.** SPEC §2 specified PaddleOCR as the primary OCR with Tesseract fallback. A feasibility audit before the bench (VERIFY-01, room #59668) found that `paddlepaddle` — PaddleOCR's required runtime — ships **no wheel for CPython 3.14** (`pip index versions paddlepaddle` → "No matching distribution found"), while both the dev hosts and the container base image (`python:3.14-slim`, Dockerfile line 10) run 3.14. PaddleOCR-as-primary was therefore uninstallable on the entire stack, and the planned accuracy bench (Paddle vs Tesseract on fixtures) could not be run at all.

**Decision (ORCH-01, #59669; human may override).** Tesseract becomes the primary in-container OCR: `tesseract-ocr` is already apt-installed in the Dockerfile (line 17) and `pytesseract` 0.3.13 imports cleanly. PaddleOCR is kept as a documented, env-gated upgrade adapter behind the existing `VisionProvider` seam (commit 86cffbf) — it becomes viable when paddlepaddle ships cp314 wheels, or by pinning the container to a Python version paddlepaddle supports (e.g. 3.12), at which point the deferred accuracy bench should actually be run.

**Honesty note.** This was an evidence-driven *installability* decision, not an accuracy comparison — Paddle could not run here to be benched. README and interview answers must say exactly that; do not retro-fit an accuracy rationale. SPEC §2 deviates from reality until amended (flagged to ORCH-01 for the Phase-6 SPEC update).

**The rule that prevents recurrence.** Before declaring any native-wheel dependency a *primary* provider in a spec, verify wheel availability against the exact runtime Python of the target base image (`pip index versions <pkg>` is enough). Architecture decisions about heavy native deps are feasibility-gated first, accuracy-gated second.
