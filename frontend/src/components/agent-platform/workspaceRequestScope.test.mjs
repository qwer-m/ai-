import assert from 'node:assert/strict';
import test from 'node:test';
import { isWorkspaceRequestCurrent, releaseWorkspaceRequest } from './workspaceRequestScope.ts';

// 这里只验证请求身份和版本变化，不替换接口，也不构造业务响应。
const workspace = {
  projectId: 9,
  workspaceVersion: 1,
  runId: 13,
  runVersion: 1,
  selectionVersion: 1,
};

test('当前作用域允许提交，同运行的数据刷新不使请求过期', () => {
  assert.equal(isWorkspaceRequestCurrent({ ...workspace }, { ...workspace }), true);
});

test('项目不同和 A -> B -> A 均拒绝旧响应', () => {
  assert.equal(isWorkspaceRequestCurrent(workspace, { ...workspace, projectId: 10 }), false);
  assert.equal(isWorkspaceRequestCurrent(workspace, { ...workspace, workspaceVersion: 3 }), false);
});

test('同项目切换运行，以及回到原运行时均拒绝旧响应', () => {
  assert.equal(isWorkspaceRequestCurrent(workspace, { ...workspace, runId: 15 }), false);
  assert.equal(isWorkspaceRequestCurrent(workspace, { ...workspace, runVersion: 3 }), false);
});

test('更换文档后拒绝旧上传和旧查重响应', () => {
  assert.equal(isWorkspaceRequestCurrent(workspace, { ...workspace, selectionVersion: 2 }), false);
});

test('项目级文档列表不依赖当前运行和选中文档', () => {
  const request = { projectId: workspace.projectId, workspaceVersion: workspace.workspaceVersion };
  assert.equal(isWorkspaceRequestCurrent(request, {
    ...workspace, runId: 15, runVersion: 3, selectionVersion: 2,
  }), true);
});

test('旧请求收尾不能解除新请求持有的加载状态', () => {
  const request = { ...workspace };
  const newerRequest = { ...workspace };
  const owner = { current: newerRequest };
  assert.equal(releaseWorkspaceRequest(request, owner, workspace), false);
  assert.equal(owner.current, newerRequest);
});

test('自己的请求可以收尾，但不能在另一个项目生命周期更新加载状态', () => {
  const owner = { current: workspace };
  assert.equal(releaseWorkspaceRequest(workspace, owner, { ...workspace, workspaceVersion: 3 }), false);
  assert.equal(owner.current, null);
});

test('同一工作区内自己的加载状态可在运行或文档变更后正常结束', () => {
  const owner = { current: workspace };
  assert.equal(releaseWorkspaceRequest(workspace, owner, {
    ...workspace, runId: 15, runVersion: 2, selectionVersion: 2,
  }), true);
  assert.equal(owner.current, null);
  assert.equal(releaseWorkspaceRequest(workspace, owner, workspace), false);
});
