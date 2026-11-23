import { useState } from 'react';
import { Nav, Tab } from 'react-bootstrap';
import { TestCaseFeedbackPanel } from './TestCaseFeedbackPanel';
import type { QualityReportPayload, TestEvaluationArtifact } from './state/types';

const coverage: Record<string, string> = { full: '完整', partial: '部分', missing: '缺失' };
const changes: Record<string, string> = {
  missing_points: '人工补足遗漏', modifications: '逻辑修正', hallucinations: '缺少需求依据',
};
type Props = { artifact: TestEvaluationArtifact | null; projectId: number | null; caseCount: number | null };

export function TestCaseEvaluationReport({ artifact, projectId, caseCount }: Props) {
  return <Tab.Container id="benchmark-coverage" defaultActiveKey="overview" transition={false} mountOnEnter>
    <div className="coverage-section-bar">
      <Nav className="coverage-section-tabs" aria-label="覆盖率报告内容">
        <Nav.Item><Nav.Link as="button" type="button" eventKey="overview">指标概览</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="evidence" disabled={!artifact}>逐组覆盖依据</Nav.Link></Nav.Item>
        <Nav.Item><Nav.Link as="button" type="button" eventKey="feedback" disabled={!artifact}>反馈确认</Nav.Link></Nav.Item>
      </Nav>
      {artifact && <small className="text-muted">生成 Run #{artifact.source_run_id} · 评测 Run #{artifact.evaluation_run_id}</small>}
    </div>
    <Tab.Content>
      <Tab.Pane eventKey="overview">
        <CoverageOverview report={artifact?.evaluation} caseCount={caseCount} />
      </Tab.Pane>
      <Tab.Pane eventKey="evidence">{artifact && <CoverageEvidence report={artifact.evaluation} />}</Tab.Pane>
      <Tab.Pane eventKey="feedback">{artifact && projectId !== null &&
        <TestCaseFeedbackPanel projectId={projectId} sourceRunId={artifact.source_run_id} evaluationRunId={artifact.evaluation_run_id} />}</Tab.Pane>
    </Tab.Content>
  </Tab.Container>;
}

function CoverageOverview({ report, caseCount }: { report?: QualityReportPayload; caseCount: number | null }) {
  const base = report?.requirement_baseline;
  return <div className="coverage-overview">
    <section aria-labelledby="effective-case-heading">
      <h4 id="effective-case-heading">有效测试用例覆盖率</h4>
      <p>待定义有效用例的判定条件和统计范围。</p>
      {caseCount !== null && <small>本次生成 {caseCount} 条用例</small>}
    </section>
    <section aria-labelledby="test-point-heading">
      <h4 id="test-point-heading">测试点覆盖率</h4>
      <p>待定义测试点清单和覆盖标准。</p>
      {base && <small>模型评审记录 · 正文 {base.groups.length} 组 · 生成稿判为完整 {base.generated_coverage_count} 组 · 跳过 {base.groups.filter(group => group.excluded).length} 组</small>}
    </section>
  </div>;
}

function CoverageEvidence({ report }: { report: QualityReportPayload }) {
  const [filter, setFilter] = useState('all');
  const base = report.requirement_baseline;
  const visible = base.groups.filter(group => {
    if (filter === 'excluded') return group.excluded;
    if (filter === 'changes') return !group.excluded && (group.difference !== 'none' || group.agent !== group.manual);
    if (filter === 'gaps') return !group.excluded && group.agent !== 'full';
    return true;
  });
  return <div>
    <select aria-label="筛选正文要求" className="form-select form-select-sm mb-2 w-auto" value={filter} onChange={event => setFilter(event.target.value)}>
      <option value="all">全部要求</option><option value="changes">双方差异</option>
      <option value="gaps">生成稿覆盖不足</option><option value="excluded">跳过计分</option>
    </select>
    <div className="table-responsive benchmark-coverage-rows" role="region" aria-label="逐组正文覆盖明细" tabIndex={0}><table className="table table-sm align-middle small">
      <caption className="visually-hidden">正文要求、双方用例引用与修改依据</caption>
      <thead><tr>{['正文要求', '生成稿', '人工稿', '差异与缺口'].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead>
      <tbody>{visible.map(group => <tr key={group.point}>
        <th scope="row">{group.point}</th>
        <td>{coverage[group.agent]}<div className="text-muted">{group.case_ids.join('、')}</div></td>
        <td>{coverage[group.manual]}<div className="text-muted">{group.manual_refs.join('、')}</div></td>
        <td>{group.excluded ? 'SKIP' : group.difference === 'none' && group.agent === 'full' && group.manual === 'full'
          ? <span className="text-muted">—</span>
          : <>{changes[group.difference] && <strong>{changes[group.difference]}</strong>}<div>{group.reason}</div></>}</td>
      </tr>)}</tbody>
    </table></div>
    {!visible.length && <p role="status">没有符合筛选条件的正文要求。</p>}
    {base.summary && <details><summary>模型评审摘要</summary><p>{base.summary}</p></details>}
  </div>;
}
