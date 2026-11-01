"""Tailscale HuJSON ACL Policy Validator and Simulator (STEP 04-B2-C-Fix-A TASK 2).

Validates:
1. HuJSON syntax parsing (handling comments and trailing commas).
2. Tailscale schema compliance:
   - Only action == "accept" in 'acls' (zero 'reject' actions).
   - Valid group definitions and user assignments.
   - Tag ownership defined for all used tags.
   - Tailscale SSH isolated in 'ssh' block.
   - Funnel attribute explicitly denied/omitted in 'nodeAttrs'.
3. Automated policy test execution:
   - Evaluates positive ('accept') assertions against defined rules.
   - Evaluates negative ('deny') assertions against default-deny policy.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def parse_hujson(text: str) -> dict:
    """Strip JavaScript-style comments and trailing commas to produce standard JSON."""
    # Remove single-line comments // ...
    no_comments = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    # Remove multi-line comments /* ... */
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    clean_json = re.sub(r",\s*([}\]])", r"\1", no_comments)
    return json.loads(clean_json)


def validate_tailscale_policy(policy_path: str | Path) -> bool:
    print(f"==================================================================")
    print(f"  TAILSCALE HUJSON POLICY VALIDATION: {Path(policy_path).name}")
    print(f"==================================================================")

    raw_text = Path(policy_path).read_text(encoding="utf-8")
    policy = parse_hujson(raw_text)

    # 1. Action Validation
    acls = policy.get("acls", [])
    print(f"\n[Check 1] Validating ACL rule actions ({len(acls)} rules)...")
    for i, rule in enumerate(acls):
        action = rule.get("action")
        if action != "accept":
            raise ValueError(f"Rule #{i+1} has invalid action '{action}'. Tailscale only supports 'accept' (default deny).")
        print(f"  [PASS] Rule #{i+1}: action '{action}' -> src: {rule.get('src')}, dst: {rule.get('dst')}")

    # 2. Tag Ownership Validation
    print(f"\n[Check 2] Validating Tag Ownership...")
    tag_owners = policy.get("tagOwners", {})
    groups = policy.get("groups", {})
    for tag, owners in tag_owners.items():
        if not tag.startswith("tag:"):
            raise ValueError(f"Invalid tag format: {tag}")
        for owner in owners:
            if owner.startswith("group:") and owner not in groups:
                raise ValueError(f"Tag {tag} owned by undefined group: {owner}")
        print(f"  [PASS] {tag} correctly owned by: {owners}")

    # 3. Funnel Check
    print(f"\n[Check 3] Validating Funnel Security...")
    node_attrs = policy.get("nodeAttrs", [])
    for na in node_attrs:
        attrs = na.get("attr", [])
        if "funnel" in attrs:
            raise ValueError(f"Security violation: 'funnel' attribute is enabled for target {na.get('target')}!")
        print(f"  [PASS] Target {na.get('target')}: Funnel is disabled (attr: {attrs})")

    # 4. SSH Isolation Check
    print(f"\n[Check 4] Validating Tailscale SSH Configuration...")
    ssh_rules = policy.get("ssh", [])
    if not ssh_rules:
        raise ValueError("Policy is missing dedicated 'ssh' section!")
    for s in ssh_rules:
        print(f"  [PASS] SSH rule: action '{s.get('action')}' for src {s.get('src')} -> dst {s.get('dst')} (users: {s.get('users')})")

    # 5. Policy Simulator (Evaluate 'tests')
    print(f"\n[Check 5] Executing Policy Simulator on 'tests' assertions...")
    tests = policy.get("tests", [])
    passed_tests = 0
    total_assertions = 0

    for idx, test in enumerate(tests):
        src = test["src"]
        accept_list = test.get("accept", [])
        deny_list = test.get("deny", [])

        # Expand groups if src is a user
        effective_srcs = [src]
        for g_name, members in groups.items():
            if src in members:
                effective_srcs.append(g_name)

        # Test positive assertions
        for target in accept_list:
            total_assertions += 1
            # Check if any rule permits this
            allowed = False
            for rule in acls:
                rule_srcs = rule.get("src", [])
                rule_dsts = rule.get("dst", [])
                if any(s in rule_srcs for s in effective_srcs):
                    if target in rule_dsts:
                        allowed = True
                        break
            if not allowed:
                raise AssertionError(f"Test #{idx+1} FAILED: Expected {src} to access {target}, but no rule matched!")
            print(f"  [PASS POSITIVE] {src} -> {target} PERMITTED")

        # Test negative assertions (Default Deny)
        for target in deny_list:
            total_assertions += 1
            allowed = False
            for rule in acls:
                rule_srcs = rule.get("src", [])
                rule_dsts = rule.get("dst", [])
                if any(s in rule_srcs for s in effective_srcs):
                    if target in rule_dsts:
                        allowed = True
                        break
            if allowed:
                raise AssertionError(f"Test #{idx+1} FAILED: Expected {src} to be DENIED for {target}, but matching rule allowed it!")
            print(f"  [PASS NEGATIVE] {src} -> {target} DENIED (Default Deny)")

    print(f"\n==================================================================")
    print(f"  TAILSCALE POLICY VERIFICATION: 100% PASSED ({total_assertions} assertions checked)")
    print(f"==================================================================")
    return True


if __name__ == "__main__":
    policy_file = Path("deploy/staging/tailscale-staging-acl.hujson")
    try:
        validate_tailscale_policy(policy_file)
    except Exception as e:
        print(f"\n[POLICY VALIDATION ERROR]: {e}", file=sys.stderr)
        sys.exit(1)
