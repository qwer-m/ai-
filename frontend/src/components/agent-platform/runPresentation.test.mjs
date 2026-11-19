import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { parseExecutionDefinition } from './executionDefinition.ts';
import { parseAgentNodeRun } from './agentApi.ts';
import {
  actualModelDisplay, executionAgent, executionDisplayStages, networkActivity, nodeProgress,
  parseGenerationProjection, workspaceExecution,
} from './runPresentation.ts';

// 使用已保存的真实 Run 与当前后端内置定义，不替换接口或构造业务响应。
const snapshot = JSON.parse(readFileSync(new URL(
  '../../../../backend/tests/agent_platform/fixtures/run48.presentation.json', import.meta.url,
), 'utf8').replace(/^\uFEFF/, ''));
const run = { ...snapshot.run, nodes: snapshot.nodes };
const network = snapshot.nodes.find((node) => node.node_type === 'agent_network');
const contract = run.run_context.execution_contract;
const historical63 = JSON.parse(readFileSync(new URL('./fixtures/run63.presentation.json', import.meta.url), 'utf8'));
const run63 = historical63.historical_run;
const network63 = run63.nodes.find((node) => node.node_type === 'agent_network');
const initialized63 = JSON.parse(readFileSync(new URL(
  './fixtures/run63.current-domain-initialization.json', import.meta.url,
), 'utf8'));

test('真实 Run 按冻结 DAG 定义展示，当前目录缺失不影响历史展示', () => {
  const execution = workspaceExecution(run, null);
  assert.equal(execution.error, null);
  assert.deepEqual(execution.definition, parseExecutionDefinition(contract.workflow.definition, '冻结定义'));
  assert.deepEqual(executionDisplayStages(execution.definition), contract.workflow.definition.display_stages);
});

test('真实 Agent 名称、角色配置和模型来自同一个运行契约', () => {
  const frozen = contract.agents.find((agent) => agent.id === network.agent_definition_id);
  const agent = executionAgent(run, network);
  assert.equal(agent.name, frozen.name);
  assert.deepEqual(agent.runtime_config, frozen.runtime_config);
  assert.equal(actualModelDisplay(network, agent).name, network.sdk_state.model.name);
  const withoutRuntimeModel = { ...network, sdk_state: { ...network.sdk_state, model: {} } };
  const route = frozen.runtime_config.model_route || 'main';
  assert.equal(actualModelDisplay(withoutRuntimeModel, agent).name, frozen.model_routes[route]?.model || frozen.model);
  assert.equal(actualModelDisplay(withoutRuntimeModel, undefined).name, '本次运行未记录模型');
});

test('真实历史投影只保留通用行动回执，不把旧规划计数解释成当前进度', () => {
  const projection = network.sdk_state.action_checkpoint.projection;
  const activity = networkActivity(run, network);
  assert.equal(activity.action.key, projection.current_action.tool_key);
  assert.equal(activity.action.status, projection.current_action.status);
  assert.equal(activity.action.name, contract.tools.find((tool) => tool.key === activity.action.key).name);
  assert.equal(activity.children.reduce((sum, child) => sum + child.count, 0), projection.saved_child_call_count);
  assert.match(nodeProgress(network), /历史未提供该进度/);
  assert.doesNotMatch(nodeProgress(network), /规划路由|规划补充|来源审查/);
  assert.equal(Object.hasOwn(network.sdk_state.action_checkpoint, 'state'), false);
});

test('真实失败Run63保留状态、冻结定义和事件来源，不迁移旧领域投影', () => {
  const before = JSON.stringify(historical63);
  assert.equal(historical63.provenance.run_id, 63);
  assert.equal(historical63.provenance.domain_state_migrated, false);
  assert.equal(historical63.provenance.new_business_or_model_results, false);
  assert.equal(run63.status, 'failed');
  assert.ok(historical63.historical_events.length > 0);
  assert.ok(historical63.historical_events.every((event) => event.run_id === 63));
  assert.equal(parseGenerationProjection(network63.sdk_state.action_checkpoint.projection), null);
  assert.match(nodeProgress(network63), /历史未提供该进度/);
  assert.equal(workspaceExecution(run63, null).definition.execution_mode, 'agent_network');
  assert.equal(executionAgent(run63, network63).name,
    run63.run_context.execution_contract.agents.find((agent) => agent.id === network63.agent_definition_id).name);
  const activity = networkActivity(run63, network63);
  assert.equal(activity.action.key, network63.sdk_state.action_checkpoint.projection.current_action.tool_key);
  assert.equal(JSON.stringify(historical63), before);
});

test('实际Run63节点输入引用标记穿过API解析，保持原始引用头', () => {
  const parsed = parseAgentNodeRun(network63, '真实Run63.nodes[0]');
  assert.equal(network63.input_payload_is_reference, true);
  assert.equal(parsed.input_payload_is_reference, true);
  assert.deepEqual(parsed.input_payload, network63.input_payload);
  assert.equal(parsed.input_payload.__agent_artifact_input__, 1);
  assert.deepEqual(parsed.sdk_state.action_checkpoint, network63.sdk_state.action_checkpoint);
});

test('真实来源经当前初始化器产生的CPU投影按原值展示，未生成不算验收成功', () => {
  const before = JSON.stringify(initialized63);
  const proof = initialized63.provenance;
  assert.equal(proof.source_run_id, 63);
  assert.equal(proof.source_run_status, 'failed');
  assert.equal(proof.model_requests, 0);
  assert.equal(proof.database_writes, 0);
  assert.equal(proof.old_domain_state_migrated, false);
  assert.equal(proof.online_generation_accepted, false);
  assert.equal(proof.new_business_or_model_results, false);
  assert.ok(proof.evidence_catalog_items > 0);
  assert.equal(proof.approved_decisions_count, 3);
  assert.deepEqual(initialized63.protected_run_before, initialized63.protected_run_after);
  const projection = parseGenerationProjection(initialized63.projection);
  assert.deepEqual(projection, initialized63.projection);
  assert.deepEqual(projection.progress, initialized63.observation.progress);
  assert.deepEqual(projection.versions, initialized63.observation.versions);
  assert.equal(projection.completed, false);
  assert.equal(projection.progress.target_cases, initialized63.input.case_budget);
  assert.equal(projection.progress.review_current, false);
  // 仅把CPU产出的投影接到真实节点外壳测试展示；不称为该历史Run的新状态。
  const cpuPresentation = {
    ...network63,
    sdk_state: { ...network63.sdk_state, action_checkpoint: { projection } },
  };
  const label = nodeProgress(cpuPresentation);
  assert.match(label, /覆盖目标 0\/0/);
  assert.match(label, /用例 0\/70/);
  assert.match(label, /待处理 0 项/);
  assert.match(label, /待确认 0 项/);
  assert.match(label, /独立评审 0 条（当前版本待复核）/);
  assert.doesNotMatch(label, /历史未提供|规划路由|规划补充|来源审查|候选/);
  assert.equal(JSON.stringify(initialized63), before);
});

test('当前真实CPU投影删除必需字段或损坏类型后明确拒绝，不补零', () => {
  for (const key of Object.keys(initialized63.projection.progress)) {
    const broken = structuredClone(initialized63.projection);
    delete broken.progress[key];
    assert.throws(() => parseGenerationProjection(broken), new RegExp(`progress\\.${key}`));
  }
  const wrongCount = structuredClone(initialized63.projection);
  wrongCount.progress.cases = -1;
  assert.throws(() => parseGenerationProjection(wrongCount), /progress.cases 应为非负整数/);
  const wrongBoolean = structuredClone(initialized63.projection);
  wrongBoolean.progress.review_current = 'false';
  assert.throws(() => parseGenerationProjection(wrongBoolean), /progress.review_current 应为布尔值/);
  assert.equal(parseGenerationProjection(undefined), null);
});

test('当前CPU投影与真实历史节点分别通过API解析，不改写冻结证据', () => {
  const historicalBefore = JSON.stringify(network63);
  const control = structuredClone(network63);
  control.sdk_state.action_checkpoint.projection = structuredClone(initialized63.projection);
  const parsed = parseAgentNodeRun(control, 'CPU投影控制输入');
  assert.deepEqual(parsed.sdk_state.action_checkpoint.projection, initialized63.projection);
  assert.equal(parsed.input_payload_is_reference, network63.input_payload_is_reference);
  delete control.sdk_state.action_checkpoint.projection.progress.cases;
  assert.throws(() => parseAgentNodeRun(control, 'CPU投影损坏控制输入'), /progress.cases/);
  assert.equal(JSON.stringify(network63), historicalBefore);
});

test('运行契约缺失时拒绝回读当前目录', () => {
  const withoutContract = { ...run, run_context: { ...run.run_context } };
  delete withoutContract.run_context.execution_contract;
  const execution = workspaceExecution(withoutContract, { definition: contract.workflow.definition });
  assert.equal(execution.definition, null);
  assert.match(execution.error, /运行冻结执行定义/);
});

test('真实定义删除 execution_mode 后明确拒绝，不默认为 DAG', () => {
  const missingMode = { ...contract.workflow.definition };
  delete missingMode.execution_mode;
  assert.throws(() => parseExecutionDefinition(missingMode, '真实定义'), /execution_mode 应为字符串/);
});

test('当前后端生成定义按顶层单主 Agent 投影，交付契约保留', () => {
  const builtin = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', [
    'import json',
    'from modules.agent_platform.test_generation_builtin_specs import BUILTIN_WORKFLOW_SPECS',
    'print(json.dumps(BUILTIN_WORKFLOW_SPECS[0], ensure_ascii=False))',
  ].join('; ')], {
    cwd: fileURLToPath(new URL('../../../../backend/', import.meta.url)),
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    encoding: 'utf8',
  }));
  const definition = parseExecutionDefinition(builtin.definition, '当前内置生成定义');
  assert.equal(definition.execution_mode, 'agent_network');
  assert.equal(definition.entry_agent_key, 'test_generation_coordinator');
  assert.equal(definition.required_artifact_key, 'test_generation');
  assert.deepEqual(Object.keys(definition.input_schema.properties).sort(), [
    'batch_case_limit', 'case_budget', 'disable_result_cache', 'requirement', 'requirement_doc_id',
  ]);
  assert.equal(definition.input_schema.properties.batch_case_limit.maximum, 20);
  assert.equal(Object.hasOwn(definition, 'nodes'), false);
  assert.deepEqual(executionDisplayStages(definition).map((stage) => stage.node_keys), [['agent_network']]);
  assert.throws(() => parseExecutionDefinition({
    ...builtin.definition, nodes: contract.workflow.definition.nodes,
  }, '混入旧 DAG 的真实新定义'), /包含未知字段: nodes/);
});
