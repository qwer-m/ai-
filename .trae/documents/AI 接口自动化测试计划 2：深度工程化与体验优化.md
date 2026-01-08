# 接口测试增强与体验优化计划 (Plan 2.2)

收到您对 Plan 2.1 的高度评价与详细建议！根据您的反馈以及最新的“接口定义导入”需求，我制定了以下合并实施计划，旨在巩固工程基础的同时，进一步扩展测试能力。

## 1. 响应 Plan 2.1 反馈优化 (立即执行)
针对您在 Review 中提出的宝贵建议，我将优先完成以下优化：

*   **Prompt Loader 性能优化** (`core/prompt_loader.py`):
    *   引入基于文件 `last_modified` 时间戳的缓存机制。
    *   仅在文件修改后重新读取磁盘，提升高并发下的响应速度。
*   **工程文档完善**:
    *   在根目录创建 `ERROR_CODES.md`。
    *   详细记录 `ERR_SYS_001` (系统故障) 到 `ERR_TEST_004` (断言失败) 的含义与处理建议，方便团队查阅。
*   **前端体验微调** (`frontend/src/components/APITesting.tsx`):
    *   将 **断言失败 (ERR_TEST_004)** 的 UI 状态颜色从 <span style="color:red">红色 (Danger)</span> 调整为 <span style="color:orange">黄色 (Warning)</span>。
    *   **设计意图**：区分“系统崩溃”与“业务逻辑不符”，降低用户对正常测试失败的心理焦虑。

## 2. 新功能：接口定义导入与深度测试
针对您“粘贴接口 -> 自动生成等价类/边界值 -> 自动执行”的需求，我将构建一套新的**深度测试链路**：

### 后端改造 (Python)
1.  **新建结构化 Prompt** (`prompts/api_test_generator_structured.yaml`):
    *   **核心指令**：专门处理 JSON/cURL 格式的接口定义。
    *   **测试策略**：强制要求 AI 对每个参数进行 **等价类划分 (Valid/Invalid)** 和 **边界值分析 (Boundary)**。
    *   **输出规范**：生成包含多个测试函数（`test_xxx_valid`, `test_xxx_boundary`）的 Pytest 脚本。
2.  **升级执行引擎** (`modules/api_testing.py`):
    *   增强 `generate_api_test_script`：增加 `mode` 参数（`natural` vs `structured`）。
    *   根据模式动态加载对应的 YAML Prompt。

### 前端改造 (React)
1.  **界面升级** (`components/APITesting.tsx`):
    *   **Tab 切换架构**：
        *   **Tab 1: 需求描述**（现有）：适合自然语言描述。
        *   **Tab 2: 接口定义**（新增）：提供大文本区域，支持粘贴 JSON 数组或 Swagger 片段。
    *   **模板辅助**：提供“插入 JSON 模板”按钮，引导用户输入标准格式（如 `[{"url": "...", "method": "POST", "params": {...}}]`）。

## 3. 预期价值
*   **深度覆盖**：从“跑通接口”进化为“测透接口”，自动覆盖边界和异常场景。
*   **体验升级**：更友好的错误展示和更高效的配置读取。
*   **工程规范**：完整的错误码文档为团队协作奠定基础。
