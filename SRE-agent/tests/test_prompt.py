from sre_agent.agent import (
    PydanticSreAgent,
    SYSTEM_PROMPT,
    _exception_chain_text,
    _prompt_with_context,
    _pydantic_model_name,
)
from sre_agent.coral_mcp import detect_coral_mcp_args


def test_system_prompt_is_read_only_and_evidence_based():
    assert "read-only" in SYSTEM_PROMPT
    assert "Use Coral MCP tools" in SYSTEM_PROMPT
    assert "unknown" in SYSTEM_PROMPT


def test_detect_coral_mcp_args_accepts_installed_coral():
    args = detect_coral_mcp_args("coral")
    assert args in (["mcp"], ["mcp-stdio"])


def test_pydantic_model_name_defaults_to_anthropic_provider():
    assert _pydantic_model_name("claude-sonnet-4-6") == "anthropic:claude-sonnet-4-6"
    assert _pydantic_model_name("anthropic:claude-sonnet-4-6") == "anthropic:claude-sonnet-4-6"


def test_prompt_with_context_adds_slack_metadata():
    prompt = _prompt_with_context("What changed?", {"channel": "C123", "user": "U123"})

    assert "What changed?" in prompt
    assert "Slack event context" in prompt
    assert '"channel": "C123"' in prompt


def test_build_agent_uses_pydantic_ai_with_coral_mcp(monkeypatch):
    class StubCoralClient:
        coral_bin = "coral"
        mcp_args = ["mcp"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    agent = PydanticSreAgent(coral_client=StubCoralClient(), model="claude-sonnet-4-6")

    assert agent._build_agent().__class__.__name__ == "Agent"


def test_exception_chain_text_includes_root_cause():
    root = RuntimeError("channel_not_found")
    wrapped = RuntimeError("Tool 'sql' exceeded max retries")
    wrapped.__cause__ = root

    message = _exception_chain_text(wrapped)

    assert "Tool 'sql' exceeded max retries" in message
    assert "channel_not_found" in message
