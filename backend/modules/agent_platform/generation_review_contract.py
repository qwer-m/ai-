"""独审只提交语义判断；范围、问题登记和可交付结论由代码编译。"""

import json

from .output_repair import OutputValidationError


def _error(message, **details):
    raise OutputValidationError(message, details=details)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        _error(f"{name}必须是非空文字")
    return value.strip()


def _ids(value, name, known, *, required=False):
    if not isinstance(value, list) or (required and not value):
        _error(f"{name}必须是{'非空' if required else ''}编号数组")
    if any(not isinstance(item, str) or not item for item in value) or len(set(value)) != len(value):
        _error(f"{name}存在无效或重复编号")
    if not set(value) <= known:
        _error(f"{name}引用未知或未实际提供的编号", unknown_ids=sorted(set(value) - known))
    return sorted(value)


def _fields(value, expected, name):
    if not isinstance(value, dict) or set(value) != expected:
        _error(f"{name}字段必须符合完整审查协议", required_fields=sorted(expected))


def _finding_refs(finding):
    return {ref["evidence_id"] for ref in finding.get("source_refs", [])}


def _verified_steps(steps, witnesses, case_records):
    """语义由独审判断，步骤编号按当前用例版本验证，交付时执行同一校验。"""
    if not isinstance(steps, list) or not steps:
        _error("覆盖判断必须提供实际操作及结果的步骤")
    anchors = set()
    for step in steps:
        _fields(step, {"case_id", "step_number"}, "验证步骤")
        key, number = step["case_id"], step["step_number"]
        if (not isinstance(key, str) or key not in witnesses or type(number) is not int
                or not 1 <= number <= len(case_records[key]["content"]["steps"])):
            _error("验证步骤未指向见证用例中的实际操作")
        if (key, number) in anchors:
            _error("验证步骤重复", case_id=key, step_number=number)
        anchors.add((key, number))
    if {key for key, _ in anchors} != set(witnesses):
        _error("每个见证用例都须定位实际步骤")
    return [{"case_id": key, "step_number": number} for key, number in sorted(anchors)]


def required_review_source_ids(payload):
    """审查范围由实际提供的文字、批准和原图决定，不让模型猜测图片是否已看。"""
    pages = {page["page_number"] for page in payload.get("image_pages", [])}
    catalog = payload["evidence_catalog"]["items"]
    if len({item["evidence_id"] for item in catalog}) != len(catalog):
        _error("审查来源目录包含重复证据编号")
    return [item["evidence_id"] for item in catalog if item["kind"] in {"text", "approved_decision", "task_requirement"}
            or item["kind"] == "image" and item.get("page_number") in pages]


def required_case_scope_ids(state):
    """用例自身及其覆盖目标已声明的约束均须审查，范围由现有引用关系计算。"""
    coverage = {item["coverage_id"]: item for item in state["coverage_plan"]["items"]}
    return {identity: sorted(_finding_refs(record["content"]) | set().union(*(
        _finding_refs(coverage[key]) for key in record["content"]["coverage_ids"]
    ))) for identity, record in state["cases"]["items"].items()}


def _check_receipts(state, payload, output, *, wire):
    """同一套校验处理模型语义和内部回执；wire 不接受平台计算的范围字段。"""
    case_records = state["cases"]["items"]
    case_ids, eligible = set(case_records), set(required_review_source_ids(payload))
    images = {item["evidence_id"] for item in payload["evidence_catalog"]["items"] if item["kind"] == "image"}
    source_checks, case_checks = output.get("source_checks"), output.get("case_checks")
    if not isinstance(source_checks, list) or not isinstance(case_checks, list):
        _error("独审必须提供完整source_checks和case_checks，不能仅返回已检查编号")
    source_rows = {}
    for row in source_checks:
        _fields(row, {"evidence_id", "branches"}, "来源审查回执")
        evidence_id = row["evidence_id"]
        if not isinstance(evidence_id, str) or evidence_id not in eligible or evidence_id in source_rows:
            _error("来源审查回执编号未知、未实际提供或重复", evidence_id=evidence_id)
        branches = row["branches"]
        if not isinstance(branches, list) or not branches:
            _error("每项来源至少需要一条行为、条件分支或例外的检查回执", evidence_id=evidence_id)
        normalized, identities = [], set()
        for branch in branches:
            disposition = branch.get("disposition") if isinstance(branch, dict) else None
            step_field = ("transition_steps" if disposition == "transition_covered" else
                          "verification_steps" if disposition == "effect_covered" else None)
            fields = {"rule", "case_ids", "disposition", "related_evidence_ids"}
            _fields(branch, fields | ({step_field} if step_field else set()), "来源分支审查回执")
            rule = _text(branch["rule"], "分支规则及适用范围")
            if not isinstance(disposition, str) or disposition not in {"covered", "transition_covered", "effect_covered", "gap", "superseded", "reference"}:
                _error("来源分支处置无效", evidence_id=evidence_id)
            if evidence_id in images and disposition != "reference":
                _error("图片只作参考，覆盖和缺口须登记在实际支持规则的文字来源", evidence_id=evidence_id)
            witnesses = _ids(branch["case_ids"], "分支用例见证", case_ids, required=bool(step_field) or disposition == "covered")
            related = _ids(branch["related_evidence_ids"], "相关约束来源", eligible)
            # 当前行已绑定本来源；related只保留额外来源，不假设同源内部不能发生替代。
            related = [identity for identity in related if identity != evidence_id]
            if disposition == "superseded" and related and set(related) <= images:
                _error("图片不能作为替代正文规则的唯一依据", evidence_id=evidence_id)
            item = {"rule": rule, "case_ids": witnesses, "disposition": disposition, "related_evidence_ids": related}
            if step_field:
                item[step_field] = _verified_steps(branch[step_field], witnesses, case_records)
            identity = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if identity in identities:
                _error("同一来源重复提交相同分支回执", evidence_id=evidence_id)
            identities.add(identity)
            normalized.append(item)
        source_rows[evidence_id] = {"evidence_id": evidence_id, "branches": normalized}
    if set(source_rows) != eligible:
        _error("独审来源回执必须覆盖全部已提供文字、批准和实际查看图像",
               missing_evidence_ids=sorted(eligible - set(source_rows)))

    required_scopes, case_rows, scope_errors = required_case_scope_ids(state), {}, []
    scope_field = "additional_evidence_ids" if wire else "scope_evidence_ids"
    for row in case_checks:
        _fields(row, {"case_id", scope_field, "scope_reason", "valid"}, "用例适用条件检查")
        identity = row["case_id"]
        if not isinstance(identity, str) or identity not in case_ids or identity in case_rows:
            _error("用例适用条件检查编号未知或重复", case_id=identity)
        selected = _ids(row[scope_field], "用例适用条件来源", eligible)
        required = set(required_scopes[identity])
        if not required <= eligible:
            _error("用例引用的适用条件来源未实际提供", case_id=identity,
                   missing_evidence_ids=sorted(required - eligible))
        scope = sorted(required | set(selected)) if wire else selected
        if not required <= set(scope):
            scope_errors.append({"case_id": identity, "missing_evidence_ids": sorted(required - set(scope))})
        reason = _text(row["scope_reason"], "当前用例前提、输入与预期如何满足适用条件及例外的说明")
        valid = row["valid"]
        if type(valid) is not bool:
            _error("用例适用条件检查valid必须是布尔值", case_id=identity)
        case_rows[identity] = {"case_id": identity, "scope_evidence_ids": scope, "scope_reason": reason, "valid": valid}
    if set(case_rows) != case_ids:
        _error("独审适用条件回执必须检查全部当前用例", missing_case_ids=sorted(case_ids - set(case_rows)))
    if scope_errors:
        _error("用例适用条件检查遗漏已声明的来源约束", case_scope_errors=scope_errors)
    return {"source_checks": [source_rows[key] for key in sorted(source_rows)],
            "case_checks": [case_rows[key] for key in case_records], "checked_case_ids": list(case_records)}


def compile_review_checks(state, payload, output):
    """从唯一语义判断生成问题；模型不再复写缺陷、范围和总裁决。"""
    _fields(output, {"source_checks", "case_checks", "business_questions", "issue_resolutions"}, "独立评审")
    if not isinstance(output["business_questions"], list) or not isinstance(output["issue_resolutions"], list):
        _error("业务问题与原问题处理结果必须是数组")
    questions = []
    for question in output["business_questions"]:
        if not isinstance(question, dict) or question.get("kind") not in {"source_conflict", "missing_rule"}:
            _error("business_questions仅允许必须由用户明确的source_conflict或missing_rule")
        _fields(question, {"kind", "target_case_ids", "reason", "source_refs"}, "业务问题")
        targets = _ids(question["target_case_ids"], "业务问题目标用例", set(state["cases"]["items"]))
        # 覆盖编号只是现有用例的关系索引，按真实目标关联生成，不让模型复写或猜测。
        coverage = sorted({key for identity in targets
            for key in state["cases"]["items"][identity]["content"]["coverage_ids"]})
        questions.append({**question, "target_case_ids": targets, "coverage_ids": coverage})
    checks = _check_receipts(state, payload, output, wire=True)
    derived = []
    for row in checks["case_checks"]:
        if not row["valid"]:
            derived.append({"kind": "case_error", "target_case_ids": [row["case_id"]],
                "coverage_ids": list(state["cases"]["items"][row["case_id"]]["content"]["coverage_ids"]),
                "reason": row["scope_reason"], "source_refs": list(row["scope_evidence_ids"])})
    for row in checks["source_checks"]:
        for branch in row["branches"]:
            if branch["disposition"] == "gap":
                coverage = sorted({key for identity in branch["case_ids"]
                    for key in state["cases"]["items"][identity]["content"]["coverage_ids"]})
                derived.append({"kind": "coverage_gap", "target_case_ids": list(branch["case_ids"]),
                    "coverage_ids": coverage, "reason": branch["rule"],
                    "source_refs": sorted({row["evidence_id"], *branch["related_evidence_ids"]})})
    return {**checks, "derived_issues": derived, "compiled_business_questions": questions}


def validate_review_checks(state, payload, output):
    """交付时重验证已编译回执，不重放结案前的原问题编号集合。"""
    checks = _check_receipts(state, payload, output, wire=False)
    if output.get("checked_case_ids") != checks["checked_case_ids"]:
        _error("独审已检查编号必须与当前完整用例一致")
    if output.get("verdict") == "approved" and (any(not row["valid"] for row in checks["case_checks"])
            or any(branch["disposition"] == "gap" for row in checks["source_checks"] for branch in row["branches"])):
        _error("存在来源缺口或无效用例时不能批准交付")
    return checks
