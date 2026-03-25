/**
 * 渐进式重构入口层。
 *
 * 说明：
 * 1. 保留原有模块路径与导出名，避免影响现有引用。
 * 2. 真实实现已迁移到 StandardAPITestingPage，便于后续继续按功能域拆分。
 */
export { StandardAPITesting } from './StandardAPITestingPage';
