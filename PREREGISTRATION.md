# Pre-registration

**Author:** Andrew Peters Okello
**Date:** 18 November 2026

This file is committed **before** the confirmatory evidence run. The git history shows the order: this file and the frozen code come first, then the `evidence/` folder.

## Question

When an agency withdraws or changes an approval, does that decision still stop an AI-initiated action that is already in the queue, while unrelated authorised work continues?

This follows the paired structure of Moral Clarity AI's [Temporal Standing Test](https://studio.moralclarity.ai/temporal-standing-test): T₀ (standing established) → ΔN (material change) → Tₙ (the moment of action).

## Frozen implementation

The code in this commit: `step2_race.py`, `step3_change_types.py` and `step4_evidence.py`. Each evidence run also records a SHA-256 hash of each file.

## Proposition

An issuer that re-evaluates every load-bearing fact in the same locked step as issuance:

- (a) never issues a permit after a standing-defeating change has been accepted, and
- (b) still issues permits whose changes are not load-bearing, without blocking unrelated permits.

## Falsifiers (fixed in advance)

**Race test.** Permit 101 is withdrawn while it is being issued. A run is a violation if the withdrawal was accepted before issuance was recorded. An issuer fails if it has more than 0 violations in 1,000 runs.

**Paired test.** Eight change classes (approval, authority, consent, eligibility, evidence, policy, account, jurisdiction) each get:

- Case A, a standing-defeating change, which must be HELD.
- Case B, a standing-preserving change, which must be ISSUED.

A control change (applicant phone number) and an unrelated permit must also be ISSUED. An issuer fails if any case is wrong.

## Predictions

| Issuer | Race test | Paired test |
|---|---|---|
| CheckingIssuer (re-checks, but not in one locked step) | FAIL | — |
| LockedIssuer | PASS | — |
| ApprovalOnlyIssuer | — | FAIL (misses non-approval changes) |
| AnyChangeIssuer | — | FAIL (blocks harmless changes) |
| FullStandingIssuer | — | PAIRED PASS |

## Disclosure

Exploratory runs were made while the code was being written, before this pre-registration. They are kept unedited in `exploratory_runs/` for transparency. They are not the confirmatory evidence.
