import { readArray, readNumber, readObject, readString, readStringArray, readStringMap } from './jsonContract.ts';
import type { AgentWorkflowDefinition, WorkflowDisplayStage, WorkflowNode } from './types';

function parseWorkflowNode(value: unknown, path: string): WorkflowNode {
  const object = readObject(value, path);
  const nodeType = readString(object.node_type, `${path}.node_type`);
  if (nodeType !== 'agent' && nodeType !== 'agent_network'
    && nodeType !== 'agent_map' && nodeType !== 'tool') {
    throw new Error(`${path}.node_type 包含未知节点类型: ${nodeType}`);
  }
  return {
    node_key: readString(object.node_key, `${path}.node_key`),
    node_type: nodeType,
    reference_key: readString(object.reference_key, `${path}.reference_key`),
    depends_on: readStringArray(object.depends_on, `${path}.depends_on`),
    max_attempts: readNumber(object.max_attempts, `${path}.max_attempts`),
    time_budget_seconds: object.time_budget_seconds == null
      ? null
      : readNumber(object.time_budget_seconds, `${path}.time_budget_seconds`),
    input_mapping: readStringMap(object.input_mapping, `${path}.input_mapping`),
    map_config: object.map_config == null
      ? null
      : readObject(object.map_config, `${path}.map_config`),
  };
}

function parseWorkflowDisplayStage(value: unknown, path: string): WorkflowDisplayStage {
  const object = readObject(value, path);
  return {
    stage_key: readString(object.stage_key, `${path}.stage_key`),
    label: readString(object.label, `${path}.label`),
    description: readString(object.description, `${path}.description`),
    node_keys: readStringArray(object.node_keys, `${path}.node_keys`),
  };
}

export function parseExecutionDefinition(value: unknown, path: string): AgentWorkflowDefinition {
  const definition = readObject(value, path);
  const executionMode = readString(definition.execution_mode, `${path}.execution_mode`);
  if (executionMode !== 'dag' && executionMode !== 'agent_network') {
    throw new Error(`${path}.execution_mode 包含未知执行模式: ${executionMode}`);
  }
  const allowedKeys = executionMode === 'agent_network'
    ? ['execution_mode', 'entry_agent_key', 'input_schema', 'max_attempts', 'time_budget_seconds', 'required_artifact_key']
    : ['execution_mode', 'nodes', 'output_node_key', 'input_schema', 'display_stages'];
  const unknownKeys = Object.keys(definition).filter((key) => !allowedKeys.includes(key));
  if (unknownKeys.length > 0) throw new Error(`${path} 包含未知字段: ${unknownKeys.join('、')}`);
  return executionMode === 'agent_network'
    ? {
        execution_mode: 'agent_network' as const,
        entry_agent_key: readString(
          definition.entry_agent_key,
          `${path}.entry_agent_key`,
        ),
        input_schema: readObject(definition.input_schema, `${path}.input_schema`),
        max_attempts: readNumber(definition.max_attempts, `${path}.max_attempts`),
        time_budget_seconds: definition.time_budget_seconds == null
          ? null
          : readNumber(
              definition.time_budget_seconds,
              `${path}.time_budget_seconds`,
            ),
        required_artifact_key: definition.required_artifact_key == null
          ? null
          : readString(
              definition.required_artifact_key,
              `${path}.required_artifact_key`,
            ),
      }
    : {
        execution_mode: 'dag' as const,
        nodes: readArray(definition.nodes, `${path}.nodes`).map((node, index) =>
          parseWorkflowNode(node, `${path}.nodes[${index}]`),
        ),
        output_node_key: readString(
          definition.output_node_key,
          `${path}.output_node_key`,
        ),
        input_schema: readObject(definition.input_schema, `${path}.input_schema`),
        display_stages: definition.display_stages == null
          ? []
          : readArray(definition.display_stages, `${path}.display_stages`).map(
              (stage, index) => parseWorkflowDisplayStage(
                stage,
                `${path}.display_stages[${index}]`,
              ),
            ),
      };
}
