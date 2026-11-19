"""待本轮终态后整合的CPU修复草稿；没有修改正在运行的生产模块。"""

from copy import deepcopy


def _generation_request(item, guidance):
    """按真实事实目录的明确列表顺序构建请求，不使用映射的恢复迭代顺序。"""
    request = deepcopy(item)
    contract = request["case_fact_contract"]
    available_fact_ids = contract["available_fact_ids"]
    routes = contract["fact_design_item_ids"]
    if (available_fact_ids != [fact["fact_id"] for fact in request["authoritative_facts"]]
            or set(routes) != set(available_fact_ids)):
        raise ValueError("生成请求事实路由与当前批次事实目录不一致")
    contract["design_support_requirements"] = [
        {"test_design_item_id": design_id,
         "support_fact_ids": [fact_id for fact_id in available_fact_ids if design_id in routes[fact_id]]}
        for design_id in contract["required_test_design_item_ids"]
    ]
    if guidance:
        request["_planning_feedback"] = {"instruction": guidance, "source": "协调者建议，不是新增事实或预算"}
    return request


