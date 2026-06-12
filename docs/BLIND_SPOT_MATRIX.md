# Blind-Spot Matrix — ProofLine

Created after an early miss where a clean label could never reach PASS through the
real UI, because `warningBoldSignal` was never computed and the eval fixtures *injected* the answer.

## The rule that produced F1, stated once

> **A fixture may never supply a value the production pipeline is responsible for deriving.**
> Every end-to-end verification runs through the real UI/API contract with **zero injected context** —
> `application_data` may contain *only* the fields the UI actually sends (brand, class/type, alcohol
> content, net contents, origin). Any test that supplies `warningBoldSignal`, `warningSizeSignal`,
> `readabilityScore`, token confidences, etc. — values a real user cannot — is **rigged** and rejected.

## The matrix

Each row is a real-world input class a real user can actually produce. Status is what happens through
the **real path** (UI fields only), not through a unit test that feeds the engine answers.

| ID | Real-world input | Required real-path behavior | Status | Owner / fix |
|----|------------------|------------------------------|--------|-------------|
| BS1 | Clean, legible spirits label, visible-fields only | **PASS reachable** (≥1 such label verdict=PASS) | ❌ broken (F1) | vision (real boldSignal) + rules (precedence) |
| BS2 | Real phone photo of a curved bottle, warning+name legible | verdict (PASS or NEEDS_REVIEW w/ crops), **never false UNREADABLE** | ❌ broken (R1) | vision (region-weighted readability) + rules (anchor-aware demotion) |
| BS3 | Genuine glare / blown-out photo | **UNREADABLE** (no false pass) | ✅ holds (must stay) | preserve through BS2 fix |
| BS4 | Title-case "Government Warning" prefix, high OCR conf | **FAIL** (Jenny's trap) | ✅ holds | rules |
| BS5 | "90 Proof" label vs application 45% ABV | **PASS** + conversion note | ✅ holds | rules |
| BS6 | Imported product, no country of origin on label | finding emitted (not silently skipped) | ✅ holds (M1/M2 closed) | rules |
| BS7 | Wine / malt commodity label | routes to correct rule pack | ✅ holds (commodity selection) | rules |
| BS8 | "Try these" gallery tile click (cold-grader 30s) | chip verdict == banner verdict | ❌ broken (F2) | ui (real fixture PNGs) |
| BS9 | "Run 50-label demo" | real pipeline results, real receipts | ❌ broken (F3, fabricated) | api (real /api/batches/demo) |

## Acceptance gates (executable — see `tests/test_blind_spots.py`)

- **PASS-reachability:** at least one committed legible label, posted with visible-fields-only, reaches
  `verdict == PASS`. If no input can ever PASS, the build is broken regardless of unit-green.
- **Real-bottle:** `real_bottle_iphone.jpg` (Carter's photo — the only real fixture) → verdict, never
  UNREADABLE; **and** the glare fixture still → UNREADABLE in the same run.
- **No injected context:** a guard test scans the user-path eval fixtures and fails if any case carries
  `warningBoldSignal` / `warningSizeSignal` / `readabilityScore` / token-confidence keys.

## Why each blind spot existed (so it doesn't recur)

The common root: verification covered **components and mechanics** (latency, receipts, supply chain,
egress, cache, individual rules) but never traced the **real-user outcome invariant** — "can a real
user, sending only the visible fields, get the verdict the demo promises?" Rigged fixtures hid it.
The matrix makes that invariant a first-class, executable gate.
