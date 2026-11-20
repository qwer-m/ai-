// 两个工作台按项目共享当前需求选择，浏览器存储不可用时仍可手动切换。
export function rememberRequirement(projectId: number, documentId: number) {
  try { sessionStorage.setItem('workspace:requirement:' + projectId, String(documentId)); } catch {}
}

export function currentRequirement(projectId: number) {
  try { return Number(sessionStorage.getItem('workspace:requirement:' + projectId)); } catch { return null; }
}
