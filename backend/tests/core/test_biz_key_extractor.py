from core.processing.biz_key_extractor import extract_biz_key


def test_explicit_chinese_modules_remain_distinct_without_domain_registry() -> None:
    modules = ["官方区", "反馈区", "交流区", "消息区"]

    keys = [extract_biz_key("打开入口并查看内容", module) for module in modules]

    assert len(set(keys)) == len(modules)
    assert keys == [f"module*{module}" for module in modules]
    assert "general" not in keys


def test_arbitrary_module_uses_the_same_generic_normalization() -> None:
    first = extract_biz_key("创建一条工单", "售后工单域")
    second = extract_biz_key("关闭另一条工单", "售后工单域")

    assert first == second == "module*售后工单域"


def test_missing_module_uses_document_heading_instead_of_business_dictionary() -> None:
    key = extract_biz_key("## 设备巡检\n支持提交巡检结果", "")

    assert key == "heading*设备巡检"


def test_empty_structure_returns_general() -> None:
    assert extract_biz_key("", "") == "general"
