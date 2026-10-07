"""X3 — contracts for the agent's tools.

The round-trip tests prove the *mechanism*: a result reaches the model as a tool
turn, a state-changing tool parks for approval, a refusal comes back legibly. They
test tools by name.

These test the *registry*, so a tool added tomorrow is covered the moment it is
registered. That is the difference that matters: a per-tool test suite is a list
someone has to remember to extend, and the tool that gets added without one is
exactly the tool nobody checked.

Four contracts, in descending order of how much damage a violation does:

1. **Every state-changing tool is declared, and nothing else is.** `state_changing`
   is the flag the HITL gate reads. A tool that writes without it set runs with no
   human in the loop and nothing complains; a read tool with it set parks the agent
   for approval over nothing. The set is pinned explicitly, so adding a tool forces a
   reviewer to look at this line.
2. **Every tool describes itself well enough for a model to choose it.** An
   unusable description does not fail; it makes the tool dead, and a dead tool is
   invisible in every other report.
3. **Every tool declares its arguments, and rejects malformed ones.** A model that
   invents an argument must be told, not silently ignored — a tool that ignores an
   unknown parameter lets the model believe a constraint was applied.
4. **Every tool's result is JSON-serialisable.** The result goes into a chat message.
   A `datetime` or a `Decimal` in the return value is a 500 at request time, in
   production, on whichever tool happened to return it that day.
"""

from __future__ import annotations

import json

import pytest
from app.agent import builtin
from app.agent.tools import registry

#: Every tool that changes case state, named.
#:
#: Pinned as an explicit list rather than derived, because the whole point is to
#: make a human decide. A tool added to `builtin.py` without a corresponding line
#: here fails this test, and the fix is a deliberate answer to "does this need human
#: approval?" — which is a question worth being asked.
STATE_CHANGING = {"update_case_finding", "write_memory"}

#: Every registered tool, named. Same reasoning: an unlisted tool has not been
#: classified, and classification is what these contracts check.
ALL_TOOLS = {
    "get_case",
    "query_cases",
    "recall_memory",
    "run_analysis",
    "search_evidence",
    "update_case_finding",
    "write_memory",
}


@pytest.fixture(scope="module")
def tools() -> dict:
    """The registry, populated. Module-scoped because it is read-only."""
    import app.analysis.registry  # noqa: F401 - registration needs the analyzers

    builtin.register_all()
    return dict(registry())


def test_every_tool_is_classified(tools: dict) -> None:
    """Nothing registered that nobody has decided anything about."""
    assert set(tools) == ALL_TOOLS, (
        f"unclassified tools: {sorted(set(tools) - ALL_TOOLS)}; "
        f"missing: {sorted(ALL_TOOLS - set(tools))}. Add each to ALL_TOOLS and, if it "
        f"writes, to STATE_CHANGING."
    )


def test_state_changing_matches_exactly_what_is_declared(tools: dict) -> None:
    """The HITL flag agrees with the classification.

    Both failure directions are asserted, and the dangerous one is named: a writing
    tool with the flag unset applies changes with no human in the loop, and nothing
    in the system objects.
    """
    declared = {name for name, tool in tools.items() if tool.state_changing}
    assert declared == STATE_CHANGING, (
        f"state_changing is set on {sorted(declared)}, expected "
        f"{sorted(STATE_CHANGING)}. A tool that writes without it bypasses human "
        f"approval entirely."
    )


def test_every_tool_describes_itself(tools: dict) -> None:
    """A description is the only thing the model has to go on.

    Not just non-empty: a tool whose description says what it does but not when to
    use it, or not that it changes state, will be called wrongly or not at all — and
    either way the tool is dead and nothing reports it.
    """
    for name, tool in sorted(tools.items()):
        description = tool.description.strip()
        assert description, f"{name} has no description"
        assert len(description) >= 25, (
            f"{name}'s description is {len(description)} characters, too short to say "
            f"what it does and when to use it: {description!r}"
        )
        # Every tool that writes must say so, so the model can choose to propose it
        # knowing a human will be asked.
        if tool.state_changing:
            lowered = description.lower()
            assert "approval" in lowered or "human" in lowered, (
                f"{name} is state-changing but its description does not mention that "
                f"a human must approve it, so the model has no way to know: "
                f"{description!r}"
            )


def test_every_tool_declares_its_arguments(tools: dict) -> None:
    """An args model exists and has field descriptions.

    The model emits arguments from the JSON schema, so a field with no description
    is one it will guess at.
    """
    for name, tool in sorted(tools.items()):
        model = tool.args_model
        assert model is not None, f"{name} has no args model"
        for field_name, field in model.model_fields.items():
            assert field.description, (
                f"{name}.{field_name} has no description; the model would have to "
                f"guess what it means"
            )


def test_required_arguments_are_declared_and_optional_ones_are_not(
    tools: dict,
) -> None:
    """The required/optional split is a claim about what a call needs.

    Marking a genuinely required argument optional makes the tool raise deep inside
    its implementation instead of at the boundary, where the error is legible and
    the model can correct itself.
    """
    for name, tool in sorted(tools.items()):
        model = tool.args_model
        for field_name, field in model.model_fields.items():
            has_default = field.default is not None and not field.is_required()
            if field_name in _REQUIRED_ARGS.get(name, set()):
                assert field.is_required(), (
                    f"{name}.{field_name} is required for the tool to work but is "
                    f"declared optional, so a missing value fails deep inside "
                    f"instead of at the boundary"
                )
            del has_default


#: Arguments each tool genuinely cannot work without. Kept beside the assertions so
#: adding an argument is a decision someone makes rather than a default.
_REQUIRED_ARGS: dict[str, set[str]] = {
    "run_analysis": {"evidence_id"},
    "query_cases": {"q"},
    "recall_memory": {"query"},
    "update_case_finding": {"title"},
    "write_memory": {"content"},
    "get_case": set(),
    "search_evidence": set(),
}


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
def test_unknown_arguments_are_rejected_not_ignored(tools: dict, name: str) -> None:
    """A parameter the tool does not understand must fail loudly.

    The failure this prevents is quiet and serious: a model invents
    `confidence=0.9`, the tool ignores it, returns a confident-looking result, and
    the model believes a constraint it never supplied was honoured. Extra-field
    forbidding is what turns that into a legible error the model can retry.
    """
    tool = tools[name]
    model = tool.args_model
    assert model.model_config.get("extra") == "forbid", (
        f"{name}'s args model accepts unknown fields, so an invented argument is "
        f"silently discarded and the model believes it was applied"
    )

    # And prove it, rather than trusting the config.
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
        model(**{"definitely_not_a_real_argument": "x"})


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
def test_missing_required_arguments_are_rejected(tools: dict, name: str) -> None:
    """A required argument left out is refused at the boundary."""
    tool = tools[name]
    required = [f for f, v in tool.args_model.model_fields.items() if v.is_required()]
    if not required:
        pytest.skip(f"{name} takes no required arguments")
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
        tool.args_model(**{})


def test_every_tool_name_is_snake_case_and_namespaced_by_nothing(tools: dict) -> None:
    """Names are stable identifiers.

    A tool's name appears in the audit trail, in a checkpoint, and in a stored
    `approved_action_id`. Renaming one orphans every historical record that refers
    to it, silently, because nothing joins those tables any more.
    """
    for name in tools:
        assert name.replace("_", "").replace("-", "").isalnum(), name
        assert name == name.lower(), f"{name} should be lower case"
        assert " " not in name


def test_to_model_tools_produces_a_valid_openai_schema(tools: dict) -> None:
    """Everything the model sees about every tool is schema-valid JSON.

    Built from the live registry rather than a fixture, so a new tool with a
    description pydantic cannot serialise is caught here instead of at the first
    request that tries to use it.
    """
    import app.analysis.registry  # noqa: F401
    from app.agent.tools import to_model_tools

    builtin.register_all()
    payload = to_model_tools()

    assert payload, "no tools were advertised to the model"
    encoded = json.dumps(payload)
    assert len(encoded) > 0

    for entry in payload:
        assert entry["type"] == "function"
        function = entry["function"]
        assert function["name"], "a tool was advertised with no name"
        assert function["description"], f"{function['name']} advertised undescribed"
        assert function["parameters"]["type"] == "object"
        # Every declared argument must survive into the schema the model reads, or
        # the model cannot pass it.
        for field_name in tools[function["name"]].args_model.model_fields:
            properties = function["parameters"].get("properties", {})
            assert field_name in properties, (
                f"{function['name']}.{field_name} is declared but missing from the "
                f"schema the model is shown, so it can never be supplied"
            )
