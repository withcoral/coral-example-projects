from sre_agent.agent import SYSTEM_PROMPT
from sre_agent.coral_mcp import detect_coral_mcp_args


def test_system_prompt_is_read_only_and_evidence_based():
    assert "read-only" in SYSTEM_PROMPT
    assert "Use Coral MCP tools" in SYSTEM_PROMPT
    assert "unknown" in SYSTEM_PROMPT


def test_detect_coral_mcp_args_accepts_installed_coral():
    args = detect_coral_mcp_args("coral")
    assert args in (["mcp"], ["mcp-stdio"])

