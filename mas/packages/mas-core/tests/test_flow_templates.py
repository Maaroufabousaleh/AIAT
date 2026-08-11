from mas_core.workflow import parse_flow_definition, validate_flow
from mas_core.workflow.templates import flow_template_catalog


def test_all_canonical_flow_templates_are_valid_and_deterministic() -> None:
    first = flow_template_catalog()
    second = flow_template_catalog()

    assert first == second
    assert {item["template_id"] for item in first["templates"]} == {
        "software_delivery",
        "research",
        "hiring",
        "incident_response",
        "integration_rollout",
        "self_improvement",
    }
    for item in first["templates"]:
        definition = parse_flow_definition(item["definition_json"])
        assert validate_flow(definition) == [], item["template_id"]

    self_improvement = next(item for item in first["templates"] if item["template_id"] == "self_improvement")
    metadata = self_improvement["definition_json"]["metadata"]
    assert metadata["lifecycle_contract"] == "aiat.self-improvement.v1"
    assert metadata["required_gates"] == [
        "coding",
        "testing",
        "review",
        "security",
        "migration",
        "rollback",
        "human_approval",
    ]
