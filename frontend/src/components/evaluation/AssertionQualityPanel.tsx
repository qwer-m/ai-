import { useState } from 'react';
import { assertionLabels, type EvaluationSource, type TestEvaluationArtifact } from './state/types';

type Props = { source: EvaluationSource | null; artifact: TestEvaluationArtifact | null };
type Filter = 'all' | 'weak' | 'unclear' | 'excluded';
const weak = (verdict: string) => ['missing_trigger', 'unobservable', 'wrong_oracle'].includes(verdict);

export function AssertionQualityPanel({ source, artifact }: Props) {
  const [filter, setFilter] = useState<Filter>('weak');
  const groups = artifact?.evaluation.requirement_baseline.groups ?? [];
  const rows = groups.flatMap(group => group.assertion ? [{ group, evidence: group.assertion }] : []);
  const assessed = rows.filter(({ group, evidence }) => !group.excluded
    && (evidence.verdict === 'detects_fault' || weak(evidence.verdict)));
  const detected = assessed.filter(row => row.evidence.verdict === 'detects_fault').length;
  const visible = rows.filter(({ group, evidence }) => {
    if (filter === 'weak') return !group.excluded && weak(evidence.verdict);
    if (filter === 'unclear') return !group.excluded && ['unclear_requirement', 'no_case'].includes(evidence.verdict);
    if (filter === 'excluded') return group.excluded;
    return true;
  });
  const cases = new Map(source?.cases.map(c => [c.case_id, c]));
  return <>
    {!source || !artifact ? <p>评测完成后显示逐组断言依据。</p>
      : rows.length !== groups.length || !rows.length ? <p>这份历史评测未记录完整的断言证据。重新评测当前材料后可查看。</p>
      : <>
        <p className="small">生成 Run #{source.run_id} · 评测 Run #{artifact.evaluation_run_id}</p>
        <strong>可检错组占比　{assessed.length ? (100 * detected / assessed.length).toFixed(1) + '%' : '—'}</strong>
        <p className="small">可检错 {detected} 组 / 可判定 {assessed.length} 组</p>
        <label htmlFor="assertion-filter">查看断言依据</label>
        <select id="assertion-filter" className="form-select form-select-sm w-auto" value={filter}
          onChange={e => setFilter(e.target.value as Filter)}>
          <option value="weak">可改进（{assessed.length - detected}）</option><option value="all">全部（{rows.length}）</option>
          <option value="unclear">无用例或依据不足</option><option value="excluded">跳过计分</option>
        </select>
        {visible.map(({ group, evidence }) => {
          const c = cases.get(evidence.case_id);
          return <details key={group.point} className="border-top pt-2">
            <summary>{group.point} · {group.excluded ? 'SKIP' : assertionLabels[evidence.verdict]}</summary>
            <p><strong>错误实现与检出依据</strong>　{evidence.fault}</p>
            {c && <><div className="small">{c.case_id} · {c.title}</div>
              {evidence.steps.map(n => {
                const step = c.steps[n - 1];
                return step && <div key={n} className="small mt-1"><strong>步骤 {n}</strong>　{step.action}<div>预期：{step.expected}</div></div>;
              })}</>}
            <p className="small">人工稿依据：{group.manual_refs.join('、') || '无对应引用'}。{group.reason}</p>
          </details>;
        })}
        {!visible.length && <p role="status">该筛选下没有要求组。</p>}
      </>}
  </>;
}
