# Temporal Standing in a Simulated Permit Office

**Can withdrawing an approval still stop an AI action that is already queued?**

This is a small, independent simulation of a government permit workflow. An AI agent queues permit requests after they are approved. While they wait, things change: an approval is withdrawn, a licence is suspended, a policy changes. The question is whether the issuing system catches the change before the permit goes out, and whether it still lets unrelated work continue.

The test design follows the paired structure of Moral Clarity AI's [Temporal Standing Test](https://studio.moralclarity.ai/temporal-standing-test):

- **T₀:** the permit is legitimately approved.
- **ΔN:** something changes.
- **Tₙ:** the moment of issuance.

The project does **not** test Moral Clarity AI's Harmonic system or any vendor product. Its [published evidence](https://www.solace-harmonic.com/preserved-examination-lineage.html) states that it does not establish downstream executor compliance. This project looks at that downstream question: what the issuing system actually does.

## Files

| File | What it does |
|---|---|
| `permit_office.py` | Step 1: issuing everything in the queue vs. re-checking at issuance |
| `step2_race.py` | Step 2: a withdrawal arrives *during* issuance (race condition) |
| `step3_change_types.py` | Step 3: eight kinds of change, each as a Case A / Case B pair |
| `step4_evidence.py` | Runs Steps 2–3 and saves fingerprinted evidence |
| `step5_receiver.py` | Step 5: a receiver of five separate processes, judged by whether the permit reaches an independent observer (exploratory) |
| `step6_faults.py` | Step 6: duplicates, lost acknowledgements, a crash and a freeze, scripted deterministically (exploratory) |
| `PREREGISTRATION.md` | Claim and failure criteria, committed before the evidence run |
| `evidence/` | Confirmatory evidence run |
| `exploratory_runs/` | Runs made during development (disclosed, not confirmatory) |

## Run it

You need Python 3.9 or later. There are no packages to install.

```
python permit_office.py
python step2_race.py
python step3_change_types.py
python step4_evidence.py
```

## Findings

1. **Checking only when an action is queued fails.** A withdrawn permit was issued.
2. **Re-checking just before issuing is not enough.** When the withdrawal arrived during issuance, the system told the official `WITHDRAWN` and issued the permit anyway, in 1,000 of 1,000 runs. The check and the action must happen as one locked step. With a per-permit lock: 0 of 1,000. A late withdrawal then gets an honest "too late, already issued."
3. **Watching for withdrawals alone misses most changes.** An issuer that re-checked only the approval issued permits after the authority, consent, eligibility, evidence, policy, account or jurisdiction changes (7 of 8 classes).
4. **Blocking on any change is also a failure.** It caught every real change but also held permits over harmless edits such as a phone number.
5. **Only an issuer that re-checks every load-bearing fact, in the same locked step as issuance, passed both halves of every pair.**

Results matched on Linux and Windows.

### Exploratory: distributed receiver (Steps 5–6)

These were developed after the pre-registered run and are not yet pre-registered.

6. **Re-checking across separate services still leaves the gap.** With five separate processes and only the applicant's inbox counting as irreversible, a design that re-checks before dispatch failed when the revocation landed between its check and the send.
7. **The authority registry must remember what is already out.** A dispatch lease passed the clean cases only after the registry permanently recorded deliveries. Without that, a later withdrawal was answered "WITHDRAWN" for a permit the applicant already held.
8. **A lease alone fails under faults.** Duplicates and lost acknowledgements produced two permits, and after a crash the sender trusted an expired lease. Idempotency keys and re-checking the lease fixed those.
9. **Enforcement has to sit at the irreversible point.** When the sender froze between checking its lease and sending, the lease expired, the official was told "WITHDRAWN", and the permit arrived anyway. Only a design where the delivery channel itself checks the lease with the registry at the moment of delivery passed all four faults.

## What this suggests agencies should ask vendors for

1. A list of every fact an action's authority depends on.
2. Evidence that each fact is re-checked at the moment of action, not only when the action is queued.
3. Where the "point of no return" is, with proof that a cancel accepted before it always stops the action, and a cancel after it always gets an honest "too late."
4. Paired results (a change that should block, and one that shouldn't) for each kind of change, repeated many times, with failure rates.
5. Whether the delivery point itself checks authority at the moment of delivery. If it doesn't, the vendor should state how long a slow or frozen component could still deliver after a revocation.

## Verify the evidence

Check the fingerprints:

- PowerShell: `Get-FileHash evidence\*.json -Algorithm SHA256`
- Linux or macOS: `cd evidence && sha256sum -c SHA256SUMS.txt`

Then compare the code hashes in the `_prereg.json` file against the files in the commit.

## Limitations

- This is a simulation: a single Python process with threads, not a real networked system.
- The change classes and facts are illustrative, not taken from a real agency's rules.
- The race test forces one ordering (a cancel during issuance). Other orderings aren't explored exhaustively.
- Step 6 scripts faults in a single process so each one happens at exactly the same moment every time. Step 5 covers real separate processes, but without faults.
- Passing here shows a design pattern works in this model. It does not prove any real system is safe.

## Author

Andrew Peters Okello. Feedback and challenges are welcome; please open an issue.
