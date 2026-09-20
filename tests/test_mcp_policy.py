"""DA-27 (Capability Registry + Agent Execution Policy) - testes de
app/mcp/policy.py: enforce() precisa ser FAIL-CLOSED (tool nao
registrada = negada, nunca permitida por omissao) e respeitar scopes/
aprovacao por ExecutionContext, sem depender de nenhuma infraestrutura
externa (dataclasses puros)."""

import pytest

from app.mcp.policy import (
    CAPABILITY_REGISTRY,
    DEFAULT_EXECUTION_CONTEXT,
    ExecutionContext,
    PolicyDeniedError,
    ToolPolicy,
    enforce,
)


def test_both_current_tools_are_registered():
    assert "diagnose_incident" in CAPABILITY_REGISTRY
    assert "list_connectors" in CAPABILITY_REGISTRY


def test_current_tools_are_all_non_destructive_and_low_risk():
    # As duas tools atuais do MCP server sao estritamente read-only
    # (ver docstring de app/mcp/server.py) - o registry deve refletir
    # isso fielmente, nao subestimar nem superestimar o risco.
    for policy in CAPABILITY_REGISTRY.values():
        assert policy.destructive is False
        assert policy.risk_level == "low"
        assert policy.approval_required is False


def test_enforce_allows_registered_tool_with_default_context():
    policy = enforce("diagnose_incident")
    assert isinstance(policy, ToolPolicy)
    assert policy.name == "diagnose_incident"


def test_enforce_denies_unregistered_tool_fail_closed():
    with pytest.raises(PolicyDeniedError, match="Capability Registry"):
        enforce("restart_iflow")  # tool hipotetica, nunca registrada


def test_enforce_denies_when_required_scope_missing():
    context = ExecutionContext(granted_scopes=frozenset())  # nenhum scope
    with pytest.raises(PolicyDeniedError, match="escopo"):
        enforce("diagnose_incident", context=context)


def test_enforce_allows_when_required_scope_present():
    context = ExecutionContext(granted_scopes=frozenset({"integration.read"}))
    assert enforce("diagnose_incident", context=context).name == "diagnose_incident"


def test_enforce_denies_approval_required_tool_without_approval():
    hypothetical_write_tool = ToolPolicy(
        name="restart_iflow",
        risk_level="critical",
        destructive=True,
        scopes_required=("integration.write",),
        approval_required=True,
        data_sensitivity="confidential",
    )
    CAPABILITY_REGISTRY["restart_iflow"] = hypothetical_write_tool
    try:
        context = ExecutionContext(granted_scopes=frozenset({"integration.write"}), approved=False)
        with pytest.raises(PolicyDeniedError, match="aprovacao"):
            enforce("restart_iflow", context=context)
    finally:
        del CAPABILITY_REGISTRY["restart_iflow"]


def test_enforce_allows_approval_required_tool_when_approved():
    hypothetical_write_tool = ToolPolicy(
        name="restart_iflow",
        risk_level="critical",
        destructive=True,
        scopes_required=("integration.write",),
        approval_required=True,
        data_sensitivity="confidential",
    )
    CAPABILITY_REGISTRY["restart_iflow"] = hypothetical_write_tool
    try:
        context = ExecutionContext(granted_scopes=frozenset({"integration.write"}), approved=True)
        assert enforce("restart_iflow", context=context).name == "restart_iflow"
    finally:
        del CAPABILITY_REGISTRY["restart_iflow"]


def test_default_execution_context_grants_only_read_scope():
    assert DEFAULT_EXECUTION_CONTEXT.granted_scopes == frozenset({"integration.read"})
    assert DEFAULT_EXECUTION_CONTEXT.approved is False
