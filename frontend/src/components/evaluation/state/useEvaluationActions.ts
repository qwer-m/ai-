import { useEffect, useRef, useState } from 'react';
import { getAgentRun, getAgentRunSummary } from '../../agent-platform/agentApi';
import { getErrorText } from '../../../utils/api';
import { evaluateTestCasesRequest, readTestEvaluationArtifact } from './evaluationService';
import type { EvaluationSource } from './types';

type Draft = { text: string; file: File | null; baseline: string };

// 按项目、文档、生成运行和报告版本隔离草稿，切回同一份材料时可以继续编辑。
async function draftStore(key: string, value?: Draft | null): Promise<Draft | null> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const open = indexedDB.open('ai-test-platform-evaluation-drafts', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('compare-files');
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  try {
    return await new Promise<Draft | null>((resolve, reject) => {
      const tx = db.transaction('compare-files', value === undefined ? 'readonly' : 'readwrite');
      const store = tx.objectStore('compare-files');
      const request = value === undefined ? store.get(key) : value === null ? store.delete(key) : store.put(value, key);
      tx.oncomplete = () => resolve(value === undefined ? request.result ?? null : null);
      tx.onabort = tx.onerror = () => reject(tx.error);
    });
  } finally { db.close(); }
}

const delay = () => new Promise(resolve => window.setTimeout(resolve, 2000));

export function useEvaluationActions(source: EvaluationSource | null, onLog: (message: string) => void) {
  const initial = source?.evaluation;
  const [artifact, setArtifact] = useState(initial);
  const [draft, setDraft] = useState<Draft>({
    text: initial?.reference_content ?? '', file: null,
    baseline: initial?.evaluation.requirement_baseline.groups.map(group => group.point).join('\n') ?? '',
  });
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [evaluationId, setEvaluationId] = useState<number | null>(null);
  const live = useRef(true);
  const edited = useRef(false);
  const submitting = useRef(false);
  const draftKey = source && [source.project_id, source.document_id, source.run_id, artifact?.evaluation_run_id ?? 0].join('/');
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  useEffect(() => {
    if (!draftKey) return;
    let active = true;
    draftStore(draftKey).then(stored => {
      if (active && stored && !edited.current) { setDraft(stored); setDirty(true); }
    }).catch(() => {});
    return () => { active = false; };
  }, [draftKey]);

  function edit(update: Partial<Draft>) {
    edited.current = true;
    const next = { ...draft, ...update };
    setDraft(next);
    setDirty(true);
    setError('');
    if (draftKey) void draftStore(draftKey, next).catch(() => {});
  }
  async function evaluate() {
    if (!source || submitting.current || (!draft.file && !draft.text.trim())) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    setEvaluationId(null);
    try {
      const form = new FormData();
      form.append('project_id', String(source.project_id));
      form.append('requirement_doc_id', String(source.document_id));
      form.append('run_id', String(source.run_id));
      form.append('baseline_groups', JSON.stringify(draft.baseline.split(/\r?\n/).map(line => line.trim()).filter(Boolean)));
      if (draft.file) form.append('file', draft.file);
      else form.append('reference_content', draft.text.trim());
      const { run } = await evaluateTestCasesRequest(form);
      onLog(`生成 Run #${source.run_id} 已启动用例质量评测 #${run.id}`);
      if (!live.current) return;
      setEvaluationId(run.id);
      for (let attempt = 0; attempt < 600; attempt++) {
        await delay();
        if (!live.current) return;
        const { run: status } = await getAgentRunSummary(run.id);
        if (!live.current) return;
        if (status.project_id !== source.project_id) throw new Error('评测项目不匹配');
        if (['failed', 'cancelled'].includes(status.status)) throw new Error(status.error_message || '评测运行已终止');
        if (status.status !== 'success') continue;
        const { run: completed } = await getAgentRun(run.id);
        if (!live.current) return;
        const result = readTestEvaluationArtifact(completed, source.run_id, run.id);
        if (!result) throw new Error('评测运行缺少用例质量报告');
        if (draftKey) void draftStore(draftKey, null).catch(() => {});
        setArtifact(result);
        setDirty(false);
        setDraft({ text: result.reference_content, file: null,
          baseline: result.evaluation.requirement_baseline.groups.map(group => group.point).join('\n') });
        onLog(`用例质量评测 #${run.id} 已完成`);
        return;
      }
      throw new Error(`等待评测 #${run.id} 超时，请稍后重新载入生成 Run 查看结果`);
    } catch (reason) {
      if (live.current) setError(getErrorText(reason));
    } finally {
      submitting.current = false;
      if (live.current) setBusy(false);
    }
  }
  return { draft, edit, artifact: dirty ? null : artifact, busy, error, evaluationId, evaluate };
}
