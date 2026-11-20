import { useMemo, type ReactNode } from 'react';

function highlightJson(json: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let end = 0;
  // 输入来自 JSON.stringify，保留原有标点、缩进和转义字符，只给值和字段名着色。
  const tokens = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g;
  for (const match of json.matchAll(tokens)) {
    const start = match.index;
    const token = match[0];
    nodes.push(json.slice(end, start));
    end = start + token.length;
    let kind = 'number';
    if (token.startsWith('"')) kind = json[end] === ':' ? 'key' : 'string';
    else if (token === 'null') kind = 'null';
    else if (token === 'true' || token === 'false') kind = 'boolean';
    nodes.push(<span key={start} className={`json-${kind}`}>{token}</span>);
  }
  nodes.push(json.slice(end));
  return nodes;
}

export function GeneratedCaseList({ cases }: { cases: Record<string, unknown>[] }) {
  const highlighted = useMemo(() => {
    const records = cases.map(item => ({
      用例编号: item.case_id,
      用例标题: item.title,
      测试模块: item.module,
      前置条件: item.preconditions,
      测试输入: item.test_input,
      执行步骤: (Array.isArray(item.steps) ? item.steps : []).map(step =>
        step && typeof step === 'object' ? { 操作: step.action, 预期结果: step.expected } : step),
      用例级别: item.priority,
    }));
    return highlightJson(JSON.stringify({ 测试用例: records }, null, 2));
  }, [cases]);

  return <pre className="delivered-json" role="region" aria-label="测试用例 JSON" tabIndex={0}>
    <code>{highlighted}</code>
  </pre>;
}
