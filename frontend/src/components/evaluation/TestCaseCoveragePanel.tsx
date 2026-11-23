import { Nav, Tab } from 'react-bootstrap';
import { AssertionQualityPanel } from './AssertionQualityPanel';
import { TestCaseEvaluationReport } from './TestCaseEvaluationReport';
import { useEvaluationActions } from './state/useEvaluationActions';
import type { useEvaluationResources } from './state/useEvaluationResources';
import './benchmark.css';

type Props = { resources: ReturnType<typeof useEvaluationResources>; onLog: (message: string) => void };

export function TestCaseCoveragePanel({ resources: r, onLog }: Props) {
  const { document, source, bound } = r;
  const a = useEvaluationActions(source, onLog);
  const reference = a.draft;
  const ready = bound && Boolean(reference.file || reference.text.trim());
  const archived = a.artifact ?? null;
  const report = bound && !a.busy ? archived : null;
  return <main className="benchmark workbench-shell" aria-label="测试用例质量评估">
    <div className="benchmark-materials">
      <section className="benchmark-card ui-section-card">
        <h3>需求文档</h3>
        <label htmlFor="benchmark-document">已上传的需求</label>
        <select id="benchmark-document" className="form-select" value={document?.id ?? ''}
          disabled={a.busy || r.uploading || !r.documents.length} onChange={e => r.bind(Number(e.target.value))}>
          {!document && <option value="">请选择需求文档</option>}
          {r.documents.map(doc => <option value={doc.id} key={doc.id}>{doc.filename} · #{doc.id}</option>)}
        </select>
        <label htmlFor="benchmark-upload">上传需求文档</label>
        <input id="benchmark-upload" type="file" className="form-control form-control-sm" disabled={a.busy || r.uploading}
          onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void r.upload(file); }} />
        {r.uploading && <p role="status">正在上传需求文档…</p>}
        {document?.parse_status === 'failed' && <p role="alert">{document.parse_error || '需求解析失败'}</p>}
        {document && ['pending','parsing'].includes(document.parse_status) && <p role="status">正在解析需求文档…</p>}
        {r.documentError && <p role="alert">{r.documentError} <button className="btn btn-link btn-sm" onClick={r.retryDocuments}>重试读取</button></p>}
      </section>
      <section className="benchmark-card ui-section-card">
        <h3>生成的测试用例</h3>
        {bound && source ? <>
          <p className="benchmark-count">{source.cases.length} <small>条用例</small></p>
          <div className="benchmark-cases">{source.cases.map(c => <details key={c.case_id}>
            <summary>{c.case_id} · {c.title}</summary>
            <div className="small py-2"><div>{c.module} · {c.priority}</div>
              {c.preconditions.length > 0 && <div>前置条件：{c.preconditions.join('；')}</div>}
              {c.test_input && <div>测试输入：{c.test_input}</div>}
              <ol>{c.steps.map((step, i) => <li key={i}>{step.action}<div>预期：{step.expected}</div></li>)}</ol>
            </div>
          </details>)}</div></> : <p className="text-muted">关联生成 Run 后显示已交付的用例。</p>}
      </section>
      <section className="benchmark-card ui-section-card benchmark-reference">
        <h3>人工修改后的测试用例</h3>
        <label htmlFor="benchmark-reference-file">上传人工修改稿</label>
        <input key={reference.file?.name ?? 'empty'} id="benchmark-reference-file" type="file" className="form-control"
          disabled={a.busy || !bound} onChange={e => a.edit({ file: e.target.files?.[0] ?? null, text: '' })} />
        {bound && <p className="small text-muted">{reference.file?.name || archived?.upload.filename || '可上传人工执行后的 Excel，保留用例行与 SKIP 标记。'}</p>}
        <form className="benchmark-binding" onSubmit={e => { e.preventDefault(); r.applyRun(); }}>
          <label htmlFor="benchmark-run">生成 Run ID</label>
          <input id="benchmark-run" className="form-control" inputMode="numeric" autoComplete="off" value={r.runText}
            aria-invalid={Boolean(r.runError)} aria-describedby={bound ? undefined : 'benchmark-run-status'} disabled={!document || a.busy || r.uploading || document.parse_status !== 'success'}
            onChange={e => r.changeRunText(e.target.value)} />
          <button type="submit" className="btn btn-primary" disabled={a.busy || r.uploading || document?.parse_status !== 'success' || r.loading}>载入 Run</button>
          <button type="button" className="btn btn-outline-secondary" disabled={a.busy || r.uploading || document?.parse_status !== 'success' || r.loading}
            onClick={() => document && r.bind(document.id)}>使用最新 Run</button>
          {!bound && <div id="benchmark-run-status" role="status" className={r.runError ? 'text-danger' : 'text-muted'}>
            {r.runError || (r.loading ? '正在核对运行与需求版本…' :
              r.runText ? '请载入此 Run 以核对来源。' : document?.parse_status === 'success' ? '该文档暂无已交付用例的生成 Run。' : '请先选择已解析的需求文档。')}
          </div>}
        </form>
      </section>
    </div>
    <section className="benchmark-card ui-section-card benchmark-criteria" aria-label="评测报告">
      <Tab.Container id="benchmark-report" defaultActiveKey="coverage" transition={false}>
        <div className="benchmark-report-toolbar">
          <Nav className="benchmark-report-tabs" aria-label="评测维度">
            <Nav.Item><Nav.Link as="button" type="button" eventKey="coverage">覆盖率测试</Nav.Link></Nav.Item>
            <Nav.Item><Nav.Link as="button" type="button" eventKey="assertions">断言质量</Nav.Link></Nav.Item>
          </Nav>
          <button className="btn btn-primary btn-sm" disabled={!ready || a.busy} onClick={() => void a.evaluate()}>
            {a.busy ? '正在评测…' : '评估覆盖率与断言质量'}
          </button>
        </div>
        {a.busy && a.evaluationId && <p role="status">评测 Run #{a.evaluationId}</p>}
        {a.error && <p role="alert" className="text-danger">{a.error}</p>}
        <Tab.Content>
          <Tab.Pane eventKey="coverage">
            <TestCaseEvaluationReport key={report?.evaluation_run_id ?? 'pending'}
              artifact={report} projectId={source?.project_id ?? null} caseCount={bound && source ? source.cases.length : null} />
          </Tab.Pane>
          <Tab.Pane eventKey="assertions">
            <AssertionQualityPanel source={bound ? source : null} artifact={report} />
          </Tab.Pane>
        </Tab.Content>
      </Tab.Container>
    </section>
  </main>;
}
