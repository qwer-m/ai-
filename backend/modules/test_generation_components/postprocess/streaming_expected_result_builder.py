from __future__ import annotations

import re

from .streaming_case_normalization import strip_step_prefix, strip_validation_prefix


def build_expected_result_from_case(
    *,
    module: str,
    description: str,
    normalized_steps: list[str],
) -> str:
    steps = [strip_step_prefix(step) for step in normalized_steps if strip_step_prefix(step)]
    first_step = steps[0] if steps else ""
    last_step = steps[-1] if steps else ""
    subject = strip_validation_prefix(description) or module or first_step or "该流程"
    subject = re.sub(r"[。！？.!?]+$", "", subject).strip() or "该流程"
    subject = subject[:64]
    step_anchor = last_step or first_step or "该步骤"
    context = " ".join([module, subject, " ".join(steps)]).lower()

    if any(token in context for token in ("删除", "移除", "delete", "remove")):
        return f"执行{step_anchor}后，应删除{subject}对应记录，且列表或查询中不再显示该记录"
    if any(token in context for token in ("更新", "修改", "编辑", "update", "modify", "edit")):
        return f"执行{step_anchor}后，应更新{subject}对应记录，且查询结果应反映新值"
    if any(token in context for token in ("创建", "新增", "保存", "提交", "create", "add", "save", "submit", "register")):
        return f"执行{step_anchor}后，应成功完成{subject}，且后续查询可验证结果"
    if any(token in context for token in ("查询", "搜索", "筛选", "list", "query", "search", "filter")):
        return f"执行{step_anchor}后，返回列表仅包含满足{subject}筛选条件的记录，且每条记录关键字段值正确"
    if any(token in context for token in ("登录", "退出", "login", "logout", "认证", "授权", "permission", "auth")):
        return f"执行{step_anchor}后，响应状态码正确，且用户仅可访问{subject}授权范围内页面或模块"
    if any(token in context for token in ("导出", "export", "download")):
        return f"执行{step_anchor}后，应生成可下载的{subject}导出结果"
    if any(token in context for token in ("导入", "import", "upload")):
        return f"执行{step_anchor}后，应完成{subject}导入并返回处理结果或统计信息"
    if any(
        token in context
        for token in (
            "付费拦截",
            "未付费",
            "paywall",
            "订阅",
            "禁止访问",
            "无权限",
            "permission denied",
        )
    ):
        return f"执行{step_anchor}后，应触发权限或付费拦截提示，且无法继续进入{subject}相关学习流程"
    if any(token in context for token in ("进入", "跳转", "返回", "打开", "open", "navigate", "redirect", "enter")):
        return f"执行{step_anchor}后，应跳转到目标页面，且页面路径与标题均与{subject}一致"
    if any(token in context for token in ("置灰", "禁用", "不可点击", "不可用", "可点击", "可用", "弹窗", "提示", "过期")):
        return f"执行{step_anchor}后，按钮可用状态与提示文案正确，且用户权限与交互限制一致"
    if any(
        token in context
        for token in (
            "参数校验",
            "格式校验",
            "边界值",
            "合法性",
            "size limit",
            "invalid",
            "forbidden",
            "超限",
            "上限",
        )
    ):
        return f"执行{step_anchor}后，应给出明确校验提示，并拦截不符合条件的输入"
    if any(token in context for token in ("显示", "展示", "render", "display")):
        return f"执行{step_anchor}后，应完整显示{subject}关键字段，且字段值与输入/后端数据一致"
    return ""
