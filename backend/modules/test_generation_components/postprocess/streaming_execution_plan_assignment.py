from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_REQUIRED_GAP_SCORE = 0
_OPTIONAL_GAP_SCORE = 0


@dataclass
class _FlowEdge:
    to: int
    reverse_index: int
    capacity: int
    cost: int
    stage_key: str = ""
    candidate_key: str = ""
    case_signature: str = ""
    edge_kind: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stage_rows(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_stage in enumerate(stages):
        if not isinstance(raw_stage, dict):
            continue
        stage_key = _text(raw_stage.get("stage_key") or raw_stage.get("id"))
        if not stage_key or stage_key in seen:
            continue
        seen.add(stage_key)
        required = raw_stage.get("required") is True
        rows.append(
            {
                **raw_stage,
                "stage_key": stage_key,
                "stage_label": _text(raw_stage.get("stage_label") or raw_stage.get("label") or stage_key),
                "required": required,
                "stage_order": int(raw_stage.get("stage_order") or index + 1),
            }
        )
    return sorted(rows, key=lambda item: (int(item["stage_order"]), _text(item["stage_key"])))


def _edge_rows(
    edges: list[dict[str, Any]],
    *,
    stage_keys: set[str],
) -> list[dict[str, Any]]:
    # 同一阶段和候选实体只保留最高分边；文本签名只作诊断，不作容量节点。
    best_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            continue
        stage_key = _text(raw_edge.get("stage_key"))
        candidate_key = _text(raw_edge.get("candidate_key"))
        signature = _text(raw_edge.get("case_signature"))
        if stage_key not in stage_keys or not candidate_key:
            continue
        try:
            score = int(raw_edge.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        row = {
            **raw_edge,
            "stage_key": stage_key,
            "candidate_key": candidate_key,
            "case_signature": signature,
            "score": score,
        }
        pair = (stage_key, candidate_key)
        existing = best_by_pair.get(pair)
        stable_key = json.dumps(row, ensure_ascii=True, sort_keys=True, default=str)
        existing_stable_key = (
            json.dumps(existing, ensure_ascii=True, sort_keys=True, default=str)
            if existing is not None
            else ""
        )
        if (
            existing is None
            or score > int(existing.get("score") or 0)
            or (score == int(existing.get("score") or 0) and stable_key < existing_stable_key)
        ):
            best_by_pair[pair] = row
    return [best_by_pair[key] for key in sorted(best_by_pair)]


def _add_flow_edge(
    graph: list[list[_FlowEdge]],
    source: int,
    target: int,
    capacity: int,
    cost: int,
    *,
    stage_key: str = "",
    candidate_key: str = "",
    case_signature: str = "",
    edge_kind: str = "",
) -> None:
    forward = _FlowEdge(
        to=target,
        reverse_index=len(graph[target]),
        capacity=capacity,
        cost=cost,
        stage_key=stage_key,
        candidate_key=candidate_key,
        case_signature=case_signature,
        edge_kind=edge_kind,
    )
    reverse = _FlowEdge(
        to=source,
        reverse_index=len(graph[source]),
        capacity=0,
        cost=-cost,
    )
    graph[source].append(forward)
    graph[target].append(reverse)


def _send_min_cost_flow(
    graph: list[list[_FlowEdge]],
    *,
    source: int,
    sink: int,
    expected_flow: int,
) -> int:
    flow = 0
    node_count = len(graph)
    while flow < expected_flow:
        distances: list[int | None] = [None] * node_count
        previous_node = [-1] * node_count
        previous_edge = [-1] * node_count
        distances[source] = 0

        # 网络规模最多为十余个阶段乘候选用例，Bellman-Ford 足够且可直接处理负费用。
        for _ in range(node_count - 1):
            changed = False
            for node in range(node_count):
                base = distances[node]
                if base is None:
                    continue
                for edge_index, edge in enumerate(graph[node]):
                    if edge.capacity <= 0:
                        continue
                    candidate = base + edge.cost
                    if distances[edge.to] is None or candidate < distances[edge.to]:
                        distances[edge.to] = candidate
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index
                        changed = True
            if not changed:
                break
        if distances[sink] is None:
            raise ValueError("stage assignment flow is incomplete")

        node = sink
        while node != source:
            parent = previous_node[node]
            edge_index = previous_edge[node]
            if parent < 0 or edge_index < 0:
                raise ValueError("stage assignment flow path is broken")
            edge = graph[parent][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse_index].capacity += 1
            node = parent
        flow += 1
    return flow


def maximum_weight_stage_assignment(
    stages: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算阶段与唯一候选实体之间的确定性最大权匹配。"""
    stage_rows = _stage_rows(list(stages or []))
    if not stage_rows:
        return {
            "algorithm": "maximum_weight_bipartite_min_cost_flow_v1",
            "selected": [],
            "gaps": [],
            "stage_count": 0,
            "candidate_edge_count": 0,
            "selected_case_count": 0,
            "required_gap_count": 0,
            "optional_gap_count": 0,
            "total_score": 0,
        }

    stage_keys = {str(item["stage_key"]) for item in stage_rows}
    edge_rows = _edge_rows(list(edges or []), stage_keys=stage_keys)
    edge_by_pair = {
        (str(item["stage_key"]), str(item["candidate_key"])): item
        for item in edge_rows
    }
    candidate_keys = sorted({str(item["candidate_key"]) for item in edge_rows})

    source = 0
    stage_offset = 1
    case_offset = stage_offset + len(stage_rows)
    gap_offset = case_offset + len(candidate_keys)
    sink = gap_offset + len(stage_rows)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

    stage_node_by_key = {
        str(stage["stage_key"]): stage_offset + index
        for index, stage in enumerate(stage_rows)
    }
    case_node_by_key = {
        candidate_key: case_offset + index
        for index, candidate_key in enumerate(candidate_keys)
    }

    # 主得分优先，低位按阶段顺序和签名字典序做唯一且稳定的平局决策。
    tie_base = max(3, len(candidate_keys) + 2)
    score_scale = tie_base ** len(stage_rows)
    case_rank = {candidate_key: index for index, candidate_key in enumerate(candidate_keys)}
    max_positive_score = max(
        (int(item["score"]) for item in edge_rows if int(item["score"]) > 0),
        default=0,
    )
    required_coverage_bonus = len(stage_rows) * max_positive_score + 1

    for stage_index, stage in enumerate(stage_rows):
        stage_key = str(stage["stage_key"])
        stage_node = stage_node_by_key[stage_key]
        _add_flow_edge(graph, source, stage_node, 1, 0)
        place_value = tie_base ** (len(stage_rows) - stage_index - 1)
        for candidate_key in candidate_keys:
            row = edge_by_pair.get((stage_key, candidate_key))
            if row is None:
                continue
            stable_preference = len(candidate_keys) - case_rank[candidate_key]
            score = int(row["score"])
            coverage_bonus = required_coverage_bonus if bool(stage["required"]) and score > 0 else 0
            utility = (score + coverage_bonus) * score_scale + stable_preference * place_value
            _add_flow_edge(
                graph,
                stage_node,
                case_node_by_key[candidate_key],
                1,
                -utility,
                stage_key=stage_key,
                candidate_key=candidate_key,
                case_signature=str(row.get("case_signature") or ""),
                edge_kind="case",
            )

        gap_score = _REQUIRED_GAP_SCORE if bool(stage["required"]) else _OPTIONAL_GAP_SCORE
        gap_preference = len(candidate_keys) + 1
        gap_utility = gap_score * score_scale + gap_preference * place_value
        gap_node = gap_offset + stage_index
        _add_flow_edge(
            graph,
            stage_node,
            gap_node,
            1,
            -gap_utility,
            stage_key=stage_key,
            edge_kind="gap",
        )
        _add_flow_edge(graph, gap_node, sink, 1, 0)

    for candidate_key in candidate_keys:
        _add_flow_edge(graph, case_node_by_key[candidate_key], sink, 1, 0)

    _send_min_cost_flow(
        graph,
        source=source,
        sink=sink,
        expected_flow=len(stage_rows),
    )

    selected: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    total_score = 0
    for stage in stage_rows:
        stage_key = str(stage["stage_key"])
        chosen = next(
            (
                edge
                for edge in graph[stage_node_by_key[stage_key]]
                if edge.edge_kind in {"case", "gap"} and edge.capacity == 0
            ),
            None,
        )
        if chosen is None:
            raise ValueError(f"stage assignment missing result: {stage_key}")
        if chosen.edge_kind == "gap":
            gaps.append(
                {
                    "stage_key": stage_key,
                    "stage_label": str(stage["stage_label"]),
                    "required": bool(stage["required"]),
                    "reason": "no_globally_selected_candidate",
                }
            )
            continue
        row = edge_by_pair[(stage_key, chosen.candidate_key)]
        total_score += int(row["score"])
        selected.append(
            {
                **row,
                "stage_label": str(stage["stage_label"]),
                "required": bool(stage["required"]),
            }
        )

    return {
        "algorithm": "maximum_weight_bipartite_min_cost_flow_v1",
        "selected": selected,
        "gaps": gaps,
        "stage_count": int(len(stage_rows)),
        "candidate_edge_count": int(len(edge_rows)),
        "selected_case_count": int(len(selected)),
        "required_gap_count": int(sum(1 for item in gaps if bool(item["required"]))),
        "optional_gap_count": int(sum(1 for item in gaps if not bool(item["required"]))),
        "total_score": int(total_score),
    }


__all__ = ["maximum_weight_stage_assignment"]
