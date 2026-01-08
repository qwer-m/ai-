# UI/UX 终极重构计划 (V3.0 封神版)

## 1. 状态栏重构 (LogPanel.tsx) [🔴 优先级最高]
- **智能交互**：
    - 显示最近 5 条日志摘要 (`logs.slice(-5).join(' | ')`)。
    - 鼠标悬停 (`onMouseEnter`) 暂停滚动。
    - 键盘支持：`tabIndex="0"`, `onKeyDown` (Enter) 切换展开/收起。
- **错误分级系统 (Health Monitor)**：
    - **CRITICAL** (红色呼吸灯): 错误数 > 5。
    - **WARNING** (橙色常亮): 错误数 > 0。
    - **NORMAL** (绿色): 无错误。
- **样式升级**：深色背景 (`#2d3748`)，紧凑高度 (`32px`)，高对比度文字。

## 2. 全维防护文件上传 (TestGeneration.tsx) [🔴 优先级最高]
- **多维校验机制**：
    - **大小**: `MAX_FILE_SIZE = 50MB`。
    - **类型**: `ALLOWED_TYPES` (PDF, Word, Text, Image)。
    - **网络**: `navigator.onLine` 检测。
- **无障碍增强**：
    - 上传区 `role="button"`, `tabIndex="0"`.
    - `onKeyDown` 支持 Enter/Space 触发文件选择。
- **视觉反馈**：
    - 动态三态样式 (Idle/Active/Filled) + 清晰的错误 Toast 提示。

## 3. 智能 AI 引导 (AIHintBubble 组件) [🟡 推荐]
- **智能触发策略**：
    - **新用户**: 检查 `localStorage['tg_first_visit']`。
    - **犹豫检测**: 鼠标在上传区停留 > 5s 自动显示。
- **无障碍关闭**: 监听 `Escape` 键。

## 4. 响应式适配 (App.css) [⚪ 基础]
- 添加 `@media (max-width: 768px)` 规则，在移动端自动隐藏左侧 Sidebar。

## 执行流
1.  **LogPanel.tsx**: 实现状态栏逻辑与样式。
2.  **TestGeneration.tsx**: 重构上传区与气泡组件。
3.  **App.css**: 添加响应式规则。
4.  **验证**: 编译检查 + 模拟操作验证。
