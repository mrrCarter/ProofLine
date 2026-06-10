# AGENT_ROSTER.md — ProofLine v2

Trimmed from 11 to 7. Fewer mouths, stronger verification. Research and docs are sub-agent tasks, not standing seats. One tmux pane per agent + one pane tailing `sl session read <id> --tail 20` = 8 panes, fully watchable.

Model names below are tiers; ORCH-01 confirms exact current model strings at session start and posts them to the room (no stale model-version claims in specs).

| Agent | Role | Model tier | Credentials | Why this model here |
|---|---|---|---|---|
| ORCH-01 | Orchestrator / integration captain. Owns TODO.md, phases, locks arbitration, merge order, recaps, final gates. Spawns read-only research sub-agents (eCFR text pin, competitor facts). | Claude Opus-tier | GitHub PAT (repo-scoped) | Long-horizon planning, spec adherence, conflict resolution |
| API-01 | FastAPI service: pipeline wiring, run/batch endpoints, SSE, storage adapter, receipts (Ed25519), FSM. | GPT/Codex frontier-tier | none beyond repo | Strongest at API + integration volume |
| VISION-01 | Preprocess + OCR: Pillow/OpenCV normalize, PaddleOCR primary with Tesseract fallback, provider interface, env-gated Azure/Textract adapters, readability scoring. Day-1 task: benchmark Paddle vs Tesseract on fixtures, decide by eval numbers, record in LESSONS. | GPT/Codex or Claude Sonnet-tier | none beyond repo | CV/OCR edge-case judgment |
| RULES-01 | Rule packs (YAML), normalization utils, ABV↔proof conversion, warning canonicalization (pin live eCFR 27 CFR 16.21 text with citation + date), verdict aggregation, eval fixtures + snapshots, latency eval. | Haiku/mini/Flash-tier | none beyond repo | Bounded, test-heavy, cheap; correctness enforced by evals not eloquence |
| UI-01 | React SPA: single-screen flow, verdict cards, evidence crops, orchestrator timeline, batch table, "Try these" gallery, a11y, Playwright smoke. | Claude Sonnet-tier | none beyond repo | UI clarity and copy |
| INFRA-01 | Dockerfile, compose, GitHub Actions gate, ECR push, ECS Fargate service, CloudFront + DNS record, cosign sign, healthz verification. | Haiku/mini-tier (Gemini optional alt for long-context config sweeps) | **`proofline-deployer` AWS profile + single-record Cloudflare token — the only agent with cloud creds** | Config work is bounded; scoped creds cap blast radius |
| VERIFY-01 | Adversarial reviewer: threat model, secret/dependency scan, spec-vs-implementation diff, latency-budget audit, pre-Omar-Gate review on every PR. **Holds no write locks. Writes only review reports.** | Claude Opus-tier | none | Independent eyes; the reviewer who cannot merge is the reviewer you can trust |

After implementation: Omar Gate (`sl /omargate deep`) on every PR and the full `sl audit --path . --json` 15-agent swarm before the final PR — that's where the 13–15 persona depth lives, instead of 13 simultaneous writers stepping on each other.

Sub-agent rule (build AND runtime): top-level isolated agents per write-domain with their own locks, context, and credential scope; sub-agents only for read-only fan-out (research, exploration, running tests) reporting back to their parent. Two writers never share a context.
