"""
Step 4: The evidence log.

Tim's protocol asks for three kinds of discipline. This script does all three:

  1. FREEZE   - record a SHA-256 fingerprint of every code file, so anyone can
                check later that the code wasn't changed after the results.
  2. PRE-REGISTER - write down the claim (proposition) and what counts as
                failure (falsifier) to a file BEFORE running any test.
  3. PRESERVE - save every input, decision, timestamp and the machine details
                to a JSON results file, and fingerprint that too.

Output goes into an  evidence/  folder:
  <run_id>_prereg.json    - claim + falsifier + code fingerprints (written first)
  <run_id>_results.json   - everything that happened
  SHA256SUMS.txt          - fingerprints of the files above (appended each run)

Needs step2_race.py and step3_change_types.py in the same folder.
"""

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone

import step2_race as s2
import step3_change_types as s3

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_DIR = os.path.join(HERE, "evidence")
RACE_RUNS = 1000

PROPOSITION = (
    "An issuer that re-evaluates every load-bearing fact in the same locked step "
    "as issuance (a) never issues a permit after a standing-defeating change has "
    "been accepted, and (b) still issues permits whose changes are not load-bearing, "
    "without blocking unrelated permits."
)

FALSIFIER = {
    "race_test": (
        "A run is a violation if a withdrawal was accepted before issuance was "
        "recorded. An issuer FAILS if it has more than 0 violations in "
        f"{RACE_RUNS} runs."
    ),
    "paired_test": (
        "An issuer FAILS if any Case A permit is ISSUED, or any Case B, control "
        "or unrelated permit is HELD."
    ),
}

SCOPE_AND_LIMITS = [
    "Simulation only: a single Python process using threads, not a real networked system.",
    "Does not test Harmonic or any vendor system.",
    "Change classes and facts are illustrative, not drawn from a real agency's permit rules.",
    "The race test forces one interleaving (cancel during issuance); other orderings are not exhaustively explored.",
]


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return sha256_file(path)


def freeze_code():
    files = ["step2_race.py", "step3_change_types.py", "step4_evidence.py"]
    return {name: sha256_file(os.path.join(HERE, name)) for name in files}


def environment():
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


# ---------------------------------------------------------------
# The two tests
# ---------------------------------------------------------------
def race_test():
    out = {}
    for issuer_class in (s2.CheckingIssuer, s2.LockedIssuer):
        violations = 0
        first_run = None
        for i in range(RACE_RUNS):
            registry, results, messages = s2.run_scenario(issuer_class)
            r = registry.records[101]
            violated = bool(
                r["withdrawn_at"] and r["issued_at"] and r["withdrawn_at"] < r["issued_at"]
            )
            violations += violated
            if i == 0:
                first_run = {
                    "permit_101_outcome": results[101],
                    "official_was_told": messages.get(101),
                    "violation": violated,
                }
        out[issuer_class.__name__] = {
            "runs": RACE_RUNS,
            "violations": violations,
            "first_run_detail": first_run,
            "verdict": "PASS" if violations == 0 else "FAIL",
        }
    return out


def paired_test():
    issuers = {
        "ApprovalOnlyIssuer": s3.ApprovalOnlyIssuer(),
        "AnyChangeIssuer": s3.AnyChangeIssuer(),
        "FullStandingIssuer": s3.FullStandingIssuer(),
    }
    changes = {(name, label): change for name, label, change, _ in s3.build_cases()}

    out = {}
    for name, issuer in issuers.items():
        cases = []
        for cls, label, expected, got in s3.run(issuer):
            cases.append({
                "change_class": cls,
                "case": label,
                "change_applied": changes.get((cls, label)),
                "expected": expected,
                "got": got,
                "correct": expected == got,
            })
        out[name] = {
            "cases": cases,
            "verdict": "PAIRED PASS" if all(c["correct"] for c in cases) else "FAIL",
        }
    return out


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")

    # 1 + 2. Freeze code and pre-register BEFORE running anything
    prereg = {
        "run_id": run_id,
        "written_at": utc_now(),
        "proposition": PROPOSITION,
        "falsifier": FALSIFIER,
        "code_sha256": freeze_code(),
        "environment": environment(),
    }
    prereg_name = f"{run_id}_prereg.json"
    prereg_hash = write_json(os.path.join(EVIDENCE_DIR, prereg_name), prereg)

    # 3. Run and preserve
    started = utc_now()
    race = race_test()
    paired = paired_test()
    finished = utc_now()

    results = {
        "run_id": run_id,
        "preregistration_file": prereg_name,
        "preregistration_sha256": prereg_hash,
        "started_at": started,
        "finished_at": finished,
        "race_test": race,
        "paired_test": paired,
        "scope_and_limits": SCOPE_AND_LIMITS,
    }
    results_name = f"{run_id}_results.json"
    results_hash = write_json(os.path.join(EVIDENCE_DIR, results_name), results)

    with open(os.path.join(EVIDENCE_DIR, "SHA256SUMS.txt"), "a", encoding="utf-8") as f:
        f.write(f"{prereg_hash}  {prereg_name}\n")
        f.write(f"{results_hash}  {results_name}\n")

    # Summary
    print(f"Run {run_id}  ({environment()['platform']})\n")
    print("Race test (cancel during issuance):")
    for name, r in race.items():
        print(f"  {name:<20} {r['violations']}/{r['runs']} violations -> {r['verdict']}")
    print("\nPaired test (8 change classes + control + unrelated):")
    for name, r in paired.items():
        wrong = sum(1 for c in r["cases"] if not c["correct"])
        print(f"  {name:<20} {wrong} wrong -> {r['verdict']}")
    print(f"\nEvidence saved in: {EVIDENCE_DIR}")
    print(f"  {prereg_name}\n  {results_name}\n  SHA256SUMS.txt")


if __name__ == "__main__":
    main()
