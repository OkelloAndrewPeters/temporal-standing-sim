"""
Step 3: Many kinds of change (Tim's delta-N classes), each tested as a PAIR.

A permit only "stands" if ALL of its supporting facts still hold:
  approval     - the official's approval hasn't been withdrawn
  authority    - the approving official still has the power to approve
  consent      - the landowner still consents
  eligibility  - the applicant's contractor licence is still valid
  evidence     - the inspection report hasn't been retracted
  policy       - the CURRENT zoning policy still allows this permit
  account      - the application fee hasn't been reversed
  jurisdiction - the site is still inside this agency's area

For each class we run two cases:
  Case A: a change that DEFEATS standing   -> permit must be HELD
  Case B: a change that does NOT matter    -> permit must be ISSUED
Plus a CONTROL class (applicant's phone number changed) that must never block.

Three issuers are compared:
  ApprovalOnlyIssuer - re-checks only "was approval withdrawn?" (like Step 2)
  AnyChangeIssuer    - blocks if ANYTHING changed since T0 (over-cautious)
  FullStandingIssuer - re-checks every supporting fact at issuance
"""

import copy
import threading


# ---------------------------------------------------------------
# 1. The facts behind one permit
# ---------------------------------------------------------------
def fresh_facts():
    return {
        "approval": "approved",
        "approver_has_authority": True,
        "approver_office": "Room 4",
        "landowner_consent": True,
        "landowner_email": "owner@example.com",
        "contractor_licence": "valid",
        "contractor_address": "Plot 7",
        "inspection_report": "valid",
        "report_file_format": "pdf",
        "zoning_policy_version": "v1",
        "policy_allows_permit_type": True,
        "fee_status": "paid",
        "payment_method": "card",
        "site_in_jurisdiction": True,
        "site_parcel_label": "LR-221",
        "applicant_phone": "0700 000 000",
    }


def has_standing(f):
    """The full rule: every load-bearing fact must still hold."""
    return (
        f["approval"] == "approved"
        and f["approver_has_authority"]
        and f["landowner_consent"]
        and f["contractor_licence"] == "valid"
        and f["inspection_report"] == "valid"
        and f["policy_allows_permit_type"]
        and f["fee_status"] == "paid"
        and f["site_in_jurisdiction"]
    )


# ---------------------------------------------------------------
# 2. The change classes: (Case A defeats, Case B preserves)
# ---------------------------------------------------------------
CHANGE_CLASSES = {
    "approval": (
        {"approval": "withdrawn"},
        {"approval": "approved"},  # re-confirmed: an event happened, nothing material changed
    ),
    "authority": (
        {"approver_has_authority": False},  # delegation revoked
        {"approver_office": "Room 9"},      # official moved office
    ),
    "consent": (
        {"landowner_consent": False},
        {"landowner_email": "new-owner-email@example.com"},
    ),
    "eligibility": (
        {"contractor_licence": "suspended"},
        {"contractor_address": "Plot 12"},
    ),
    "evidence": (
        {"inspection_report": "retracted"},
        {"report_file_format": "pdf/a"},
    ),
    "policy": (
        {"zoning_policy_version": "v2", "policy_allows_permit_type": False},
        {"zoning_policy_version": "v2", "policy_allows_permit_type": True},
    ),
    "account": (
        {"fee_status": "reversed"},
        {"payment_method": "mobile money"},
    ),
    "jurisdiction": (
        {"site_in_jurisdiction": False},
        {"site_parcel_label": "LR-221/A"},
    ),
    "control": (
        None,  # no Case A: a control change should never defeat standing
        {"applicant_phone": "0711 111 111"},
    ),
}


# ---------------------------------------------------------------
# 3. Registry (one lock per permit, as Step 2 taught us)
# ---------------------------------------------------------------
class Registry:
    def __init__(self):
        self.permits = {}

    def approve(self, pid):
        facts = fresh_facts()
        self.permits[pid] = {
            "facts": facts,
            "snapshot_at_T0": copy.deepcopy(facts),
            "lock": threading.Lock(),
        }

    def apply_change(self, pid, change):
        with self.permits[pid]["lock"]:
            self.permits[pid]["facts"].update(change)


# ---------------------------------------------------------------
# 4. Three issuers (all lock check+issue together)
# ---------------------------------------------------------------
class ApprovalOnlyIssuer:
    def decide(self, permit):
        return "ISSUED" if permit["facts"]["approval"] == "approved" else "HELD"


class AnyChangeIssuer:
    def decide(self, permit):
        return "ISSUED" if permit["facts"] == permit["snapshot_at_T0"] else "HELD"


class FullStandingIssuer:
    def decide(self, permit):
        return "ISSUED" if has_standing(permit["facts"]) else "HELD"


def issue(registry, issuer, pid):
    permit = registry.permits[pid]
    with permit["lock"]:
        return issuer.decide(permit)


# ---------------------------------------------------------------
# 5. Run every pair against every issuer
# ---------------------------------------------------------------
def build_cases():
    cases = []  # (class_name, case_label, change, expected)
    for name, (defeat, preserve) in CHANGE_CLASSES.items():
        if defeat is not None:
            cases.append((name, "A", defeat, "HELD"))
        cases.append((name, "B", preserve, "ISSUED"))
    return cases


def run(issuer):
    registry = Registry()
    cases = build_cases()
    results = []
    for n, (name, label, change, expected) in enumerate(cases):
        pid = 200 + n
        registry.approve(pid)                    # T0
        registry.apply_change(pid, change)       # dN
        got = issue(registry, issuer, pid)       # Tn
        results.append((name, label, expected, got))

    # Unrelated permit: no change at all, must be issued
    registry.approve(999)
    results.append(("unrelated", "-", "ISSUED", issue(registry, issuer, 999)))
    return results


if __name__ == "__main__":
    issuers = {
        "ApprovalOnly": ApprovalOnlyIssuer(),
        "AnyChange": AnyChangeIssuer(),
        "FullStanding": FullStandingIssuer(),
    }
    all_results = {name: run(iss) for name, iss in issuers.items()}

    header = f"{'Change class':<13}{'Case':<6}{'Expected':<10}"
    header += "".join(f"{n:<15}" for n in issuers)
    print(header)
    print("-" * len(header))

    rows = list(zip(*all_results.values()))
    for row in rows:
        name, label, expected, _ = row[0]
        line = f"{name:<13}{label:<6}{expected:<10}"
        for (_, _, _, got) in row:
            mark = "" if got == expected else " X"
            line += f"{got + mark:<15}"
        print(line)

    print()
    for name, results in all_results.items():
        a_total = sum(1 for r in results if r[1] == "A")
        b_total = sum(1 for r in results if r[1] != "A")
        a_ok = sum(1 for r in results if r[1] == "A" and r[2] == r[3])
        b_ok = sum(1 for r in results if r[1] != "A" and r[2] == r[3])
        verdict = "PAIRED PASS" if (a_ok == a_total and b_ok == b_total) else "FAIL"
        print(f"{name:<13} blocked-when-should {a_ok}/{a_total}   "
              f"allowed-when-should {b_ok}/{b_total}   -> {verdict}")
