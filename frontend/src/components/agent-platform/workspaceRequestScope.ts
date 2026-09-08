export type WorkspaceRequestScope = {
  projectId: number | null;
  workspaceVersion: number;
  runId?: number | null;
  runVersion?: number;
  selectionVersion?: number;
};

export function isWorkspaceRequestCurrent(
  request: WorkspaceRequestScope,
  current: WorkspaceRequestScope,
): boolean {
  return request.projectId === current.projectId
    && request.workspaceVersion === current.workspaceVersion
    && (request.runVersion === undefined || (
      request.runVersion === current.runVersion && request.runId === current.runId
    ))
    && (request.selectionVersion === undefined
      || request.selectionVersion === current.selectionVersion);
}

export function releaseWorkspaceRequest(
  request: WorkspaceRequestScope,
  owner: { current: WorkspaceRequestScope | null },
  current: WorkspaceRequestScope,
): boolean {
  if (owner.current !== request) return false;
  owner.current = null;
  // 只有持有该操作的请求才能收尾；切换运行后可收回自己的加载状态，但不得影响新项目。
  return isWorkspaceRequestCurrent({
    projectId: request.projectId,
    workspaceVersion: request.workspaceVersion,
  }, current);
}
