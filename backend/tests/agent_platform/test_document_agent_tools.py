from __future__ import annotations

import pytest

from modules.agent_platform import document_agent_tools
from modules.agent_platform.registry import DOCUMENT_AGENT_TOOL_SPECS, tool_registry


def test_document_tools_keep_data_access_and_remove_sdk_external_vision_forwarding() -> None:
    assert {
        str(spec["tool_key"]): str(spec["handler_key"])
        for spec in DOCUMENT_AGENT_TOOL_SPECS
    } == {
        "document_get_manifest": "document.get_manifest",
        "document_search": "document.search",
        "document_get_page_text": "document.get_page_text",
    }

    removed_handlers = {
        "document.inspect_page",
        "document.inspect_relevant_page",
        "document.inspect_region",
    }
    assert removed_handlers.isdisjoint(tool_registry.keys())
    assert not hasattr(document_agent_tools, "inspect_page")
    assert not hasattr(document_agent_tools, "inspect_relevant_page")
    assert not hasattr(document_agent_tools, "inspect_region")
    assert not hasattr(document_agent_tools, "_inspect_image")

    for handler_key in removed_handlers:
        with pytest.raises(KeyError, match="未注册工具处理器"):
            tool_registry.resolve(handler_key)
