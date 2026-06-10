# FIRST_SENTI_MESSAGE.md

Paste everything below the line as the first human message after `sl session start --json`. Replace `<SESSION_ID>` and confirm the repo slug.

---

# ProofLine — Operating Order (ACK required)

We are building **ProofLine** (see `SPEC.md`) in 1–3 days: a deployed, fast, evidence-receipt alcohol-label verification prototype for a Treasury/TTB-style take-home. The build itself runs under governance: ephemeral identities, scoped credentials, locks, Omar Gate. The room transcript is part of the deliverable.

Read order before any code: `SPEC.md` → `TODO.md` → `LESSONS.md` → `AGENTS.md` → `SWE_excellence_framework.md`. SPEC §0 has five laws; recite them in your ACK.

## 1. Join protocol (do this now, in order)

```bash
sl --help                                   # local help is source of truth, not memory
sl session join <SESSION_ID> --name <AGENT_ID>
sl session pins <SESSION_ID> --json
sl session read <SESSION_ID> --remote --agent <AGENT_ID>
sl session react <SESSION_ID> ack --target-sequence <THIS_MESSAGE_SEQ>
```

Then **reply threaded under this message** (never a new top-level post):

```text
ACK <AGENT_ID> role=<role> model=<model> laws=5/5 locks=<files you intend to claim> first_action=<one concrete action>
```

## 2. Identity and credentials (hard rules)

- Provision your ephemeral identity at start; it is revoked at session end:
  ```bash
  sl ai identity provision --execute
  sl ai identity list
  ```
- **Only INFRA-01 holds deploy credentials**, via the `proofline-deployer` AWS profile (scoped to `proofline-*` resources) and a single-record Cloudflare token. Everyone else: no AWS, no Cloudflare, no org tokens. GitHub via the repo-scoped fine-grained PAT only.
- Stop list (requires an explicit human message in this room before running): `aws s3 rb`, `aws ecs delete-*`, `aws ecr delete-repository`, any route53/cloudfront change outside the proofline record, `terraform destroy`, `gh repo delete`, force-push to main.
- Post every privileged action (deploy, DNS, secrets injection) to the room as `evidence:` with command + outcome. Never paste secret values anywhere — room, commits, logs, README, screenshots.

## 3. Event-loop discipline — never go dark

Run a listener or daemon for the whole session:

```bash
sl session listen --session <SESSION_ID> --agent <AGENT_ID>     # preferred
# or
sl session daemon --session <SESSION_ID>
# fallback poller:
while true; do sl session read <SESSION_ID> --remote --agent <AGENT_ID>; sleep 60; done
```

Rules:
1. Track `last_seen_sequence` in `.senti/<AGENT_ID>-state.json`. Read the **full** stream — some events hydrate only specific agents; look for new messages, replies, reactions, pins, and actions, not just mentions.
2. Reading records a view for the human dashboard; you must still explicitly `sl session react <SESSION_ID> ack --target-sequence <seq>` on every actionable message (assignments, human messages, direct replies, lock requests).
3. Respond in-thread: `sl session reply <SESSION_ID> <seq> "..."`. New top-level posts are reserved for: phase decisions, blockers, handoffs, recaps.
4. Claim work visibly: `sl session action <SESSION_ID> working_on --target-sequence <seq>`, then `accepted: task <id>` / `done: task <id> evidence=<link or output>`.
5. **Sleep policy:** active work → poll every 60s. No room events for 60 minutes AND your queue is empty → low-power poll every 5 minutes. Any new event → wake immediately, ACK, act or reply with a concrete ETA. You may fully exit only after ORCH-01 accepts your handoff in-thread.
6. Missed something? Say so, read `sl session recap now <SESSION_ID> --remote --json`, ACK the missed seq, recover. Never pretend you saw it.

## 4. Locks

```bash
sl session locks <SESSION_ID> --json                     # check first
sl session lock  <SESSION_ID> <files...> --intent "<why>"
sl session unlock <SESSION_ID> <files...>                # immediately after commit/abandon
```
Never edit a file locked by another agent. Lock conflicts go to ORCH-01 in-thread.

## 5. Status cadence

Every 20 minutes or at any phase boundary, threaded under the current phase post:

```text
STATUS <AGENT_ID>: done=<bullets> next=<one action> blockers=<none|what> evidence=<cmd/output/link> locks=<held>
```

ORCH-01 posts `sl session recap now <SESSION_ID> --remote --json` at every phase boundary and keeps `sl session status <SESSION_ID> --json` green.

## 6. Quality bar (no "done" without proof)

Per `SPEC.md` §10 and §13: lint, typecheck, tests, build, `pytest -m latency` (p95 ≤ 4.5s on fixtures), Playwright + axe smoke, pip-audit. Before any merge: `sl /omargate deep --path . --json` (P0/P1 block). Before the final PR: `sl audit --path . --json`. Paste outputs as `evidence:` in-thread.

## 7. Assignments

Roster, models, and per-agent task lists are in `AGENT_ROSTER.md` and `TODO.md`. ORCH-01: confirm every ACK, post Phase 0 recap, create branch `proofline/takehome-v0`, then release Phase 1.

Start now. Join, ACK, claim your locks, run your listener.
