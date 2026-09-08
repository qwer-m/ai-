from __future__ import annotations

from types import SimpleNamespace

from modules.agent_platform.context_compression import (
    compress_evidence_catalog,
    context_compression_enabled,
)
from modules.agent_platform.test_generation_semantics import _compression_model_catalog


def test_context_compression_switch_uses_canonical_field() -> None:
    assert context_compression_enabled({}) is True
    assert context_compression_enabled({"enable_context_compression": False}) is False
    assert context_compression_enabled({"compress": False}) is True


def test_enabled_compression_preserves_authoritative_ids_and_records_candidates() -> None:
    catalog = {
        "document_id": 9,
        "items": [
            {
                "evidence_id": "EV-0001",
                "page_number": 1,
                "text": "这是第一条完整业务事实，不能因为预算过小而丢失。" * 20,
            },
            {
                "evidence_id": "EV-0002",
                "page_number": 1,
                "text": "短",
            },
            {
                "evidence_id": "EV-0003",
                "page_number": 2,
                "text": "这是第二页的完整业务事实，仍然需要进入来源语义分析。" * 20,
            },
        ],
    }

    compressed, stats = compress_evidence_catalog(
        catalog,
        enabled=True,
        max_tokens=128,
    )

    assert [item["evidence_id"] for item in compressed["items"]] == [
        "EV-0001",
        "EV-0002",
        "EV-0003",
    ]
    assert stats["omitted_evidence_ids"] == []
    assert set(stats["candidate_omitted_evidence_ids"]) == {"EV-0002", "EV-0003"}
    assert stats["selected_evidence_count"] == stats["raw_evidence_count"]
    assert stats["budget_expanded_for_authority"] is False
    assert stats["authority_budget_overflow"] is True
    assert stats["authority_token_estimate"] > stats["max_tokens"]
    assert stats["model_view_mode"] == "full_authoritative"
    assert stats["model_reduction_applied"] is False
    assert stats["candidate_reduction_applied"] is True
    assert [item["evidence_id"] for item in compressed["candidate_items"]] == [
        "EV-0001"
    ]


def test_disabled_compression_returns_original_catalog_and_zero_reduction() -> None:
    catalog = {
        "document_id": None,
        "items": [
            {"evidence_id": "EV-0001", "text": "完整需求正文"},
        ],
    }

    compressed, stats = compress_evidence_catalog(
        catalog,
        enabled=False,
        max_tokens=1800,
    )

    assert compressed == catalog
    assert stats["enabled"] is False
    assert stats["omitted_evidence_ids"] == []
    assert stats["char_reduction_ratio"] == 0.0


def test_stale_selection_is_recomputed_when_catalog_gains_evidence() -> None:
    """恢复产物不能沿用旧目录选择而漏掉当前新增证据。"""

    catalog = {
        "document_id": 9,
        "items": [
            {"evidence_id": "EV-0001", "text": "第一条当前需求事实，必须保留。"},
            {"evidence_id": "EV-0002", "text": "第二条当前需求事实，必须保留。"},
        ],
    }
    context = SimpleNamespace(
        run_input={
            "enable_context_compression": True,
            "context_compression_max_tokens": 1800,
        },
        artifacts={
            "context_compression": {
                "enabled": True,
                "selected_evidence_ids": ["EV-0001"],
                # 旧产物只绑定到上一版目录，当前目录已新增 EV-0002。
                "raw_evidence_ids": ["EV-0001"],
                "evidence_catalog_fingerprint": "stale-fingerprint",
            }
        },
    )

    selected, stats = _compression_model_catalog(context, catalog)

    assert [item["evidence_id"] for item in selected] == [
        "EV-0001",
        "EV-0002",
    ]
    assert stats["raw_evidence_ids"] == ["EV-0001", "EV-0002"]
    assert stats["evidence_catalog_fingerprint"] != "stale-fingerprint"


def test_selection_is_recomputed_when_compression_budget_changes() -> None:
    """目录未变但预算变更时，不能复用上一预算生成的选择结果。"""

    catalog = {
        "document_id": 9,
        "items": [
            {"evidence_id": "EV-0001", "text": "第一条当前需求事实，必须保留。"},
            {"evidence_id": "EV-0002", "text": "第二条当前需求事实，必须保留。"},
        ],
    }
    _, previous_stats = compress_evidence_catalog(
        catalog,
        enabled=True,
        max_tokens=128,
    )
    previous_artifact = {
        **previous_stats,
        # 模拟上一预算下实际形成的压缩子集。
        "selected_evidence_ids": ["EV-0001"],
    }
    context = SimpleNamespace(
        run_input={
            "enable_context_compression": True,
            "context_compression_max_tokens": 1800,
        },
        artifacts={"context_compression": previous_artifact},
    )

    selected, stats = _compression_model_catalog(context, catalog)

    assert [item["evidence_id"] for item in selected] == [
        "EV-0001",
        "EV-0002",
    ]
    assert stats["max_tokens"] == 1800
    assert stats["effective_max_tokens"] == 1800
