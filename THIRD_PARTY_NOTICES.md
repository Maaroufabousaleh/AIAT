# AIAT third-party metadata notice

AIAT is a personal, single-operator, internal-use programme. Third-party
resources are selected for their technical usefulness and are normally used
through dependencies, external processes, services, CLIs, or AIAT adapters.
They do not become AIAT authority code.

The machine-readable inventory is
[`mas/docs/provenance/third_party_components.yaml`](mas/docs/provenance/third_party_components.yaml).
Technical runtime/CLI reproducibility is tracked separately in
[`mas/docs/provenance/operator_pins.yaml`](mas/docs/provenance/operator_pins.yaml)
and checked by [`mas/scripts/check_operator_pins.py`](mas/scripts/check_operator_pins.py).
An exact technical pin or an explicitly unavailable host/deployment identity
is not a licence decision and does not create a licence allowlist.
The maintained documentation scope is checked by
[`mas/scripts/check_docs_index.py`](mas/scripts/check_docs_index.py), which
keeps concrete licence identifiers in these metadata surfaces rather than in
feature, plan, or status prose.

## Metadata-only policy

For each resource, AIAT records metadata when known:

- name, exact version/release/commit/image digest, and canonical source;
- integration mode and active adapter version;
- detected or declared licence identifier and licence/source link;
- notices and stated restrictions, including non-commercial,
  no-modification, source-disclosure, redistribution, network-use, or other
  conditions;
- dependency lock/SBOM and security provenance where available.

Licence classification is informational in this internal programme. It is not
an automated hiring, activation, installation, update, execution, or release
gate. Missing or unusual licence metadata creates an operator notice, not a
denial. AIAT has no licence allowlist or prohibited-component list for personal
internal use.

TruffleHog, Plane, ZITADEL, Vault, Ansible, OpenProject, Neo4j Community, and
other resources previously excluded for distribution concerns may be used
normally when the operator chooses them and their technical/security
integration is suitable.

## What remains enforced

This metadata policy does not weaken:

- source/version authenticity and reproducibility;
- vulnerability, secret, and malicious-instruction checks;
- sandbox, network, filesystem, credential, privacy, and data-loss controls;
- adapter compatibility and recovery tests;
- human approval for dangerous or consequential actions.

## Scope-change notice

Recording metadata does not change or waive third-party terms. The personal
operator remains responsible for the decision to use each resource and for any
obligations that apply. If AIAT is later distributed, sold, commercially
hosted, or operated for other people, perform a new distribution-specific
review; do not treat this internal-only metadata policy as that review.
