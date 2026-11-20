import { useEffect, useRef, useState } from 'react';
import { getRequirementDocumentParseStatus, listRequirementDocuments, uploadRequirementDocument } from '../../agent-platform/agentApi';
import { currentRequirement, rememberRequirement } from '../../agent-platform/requirementSelection';
import type { RequirementDocumentOption } from '../../agent-platform/types';
import { getErrorText } from '../../../utils/api';
import { fetchEvaluationSource } from './evaluationService';
import type { EvaluationSource } from './types';

export function useEvaluationResources(projectId: number, visible: boolean) {
  const [documents, setDocuments] = useState<RequirementDocumentOption[]>([]);
  const [selection, select] = useState<{ documentId: number; runId?: number } | null>(null);
  const [source, setSource] = useState<EvaluationSource | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [runError, setRunError] = useState('');
  const [documentError, setDocumentError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const document = documents.find(row => row.id === selection?.documentId);

  const selectedId = useRef<number | null>(null);
  useEffect(() => {
    if (!visible) return;
    let active = true;
    setDocumentError('');
    listRequirementDocuments(projectId).then(rows => {
      if (!active) return;
      setDocuments(rows);
      const previous = currentRequirement(projectId);
      const chosen = rows.find(row => row.id === previous) ?? rows.reduce<RequirementDocumentOption | undefined>((latest, row) => !latest || row.id > latest.id ? row : latest, undefined);
      if (chosen && chosen.id !== selectedId.current) {
        selectedId.current = chosen.id;
        setSource(null);
        setDraftId(null);
        setRunError('');
        select({ documentId: chosen.id });
      }
    }).catch(error => { if (active) setDocumentError(getErrorText(error)); });
    return () => { active = false; };
  }, [projectId, reload, visible]);

  useEffect(() => {
    if (!document || !['pending', 'parsing'].includes(document.parse_status)) return;
    let active = true;
    const poll = async () => {
      try {
        const status = await getRequirementDocumentParseStatus(document.id);
        if (!active) return;
        setDocuments(rows => rows.map(row => row.id === document.id ? { ...row, ...status } : row));
        if (status.parse_status === 'success') setReload(n => n + 1);
      } catch (error) {
        if (active) setDocumentError(getErrorText(error));
      }
    };
    const timer = window.setTimeout(poll, 2000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [document, reload]);

  useEffect(() => {
    if (!selection || document?.parse_status !== 'success') return;
    let active = true;
    setLoading(true);
    fetchEvaluationSource(projectId, selection.documentId, selection.runId).then(result => {
      if (active) setSource(result);
    }).catch(error => { if (active) setRunError(getErrorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, selection, document?.parse_status]);

  function bind(documentId: number, runId?: number) {
    setSource(null);
    setDraftId(runId === undefined ? null : String(runId));
    setRunError('');
    setDocumentError('');
    setLoading(false);
    selectedId.current = documentId;
    select({ documentId, runId });
    rememberRequirement(projectId, documentId);
  }
  async function upload(file: File) {
    setUploading(true);
    setDocumentError('');
    try {
      const uploaded = await uploadRequirementDocument(projectId, file, 'evaluation');
      if (!uploaded.success) throw new Error('需求上传失败');
      setDocuments(rows => [{
        doc_type: 'requirement', content_preview: '', linked_test_case_count: 0, ...rows.find(row => row.id === uploaded.id),
        ...uploaded, parse_error: null,
      }, ...rows.filter(row => row.id !== uploaded.id)]);
      bind(uploaded.id);
    } catch (error) {
      setDocumentError(getErrorText(error));
    } finally {
      setUploading(false);
    }
  }
  const runText = draftId ?? String(source?.run_id ?? '');
  const bound = Boolean(source && source.document_id === document?.id && document.parse_status === 'success'
    && runText === String(source.run_id) && !uploading);
  function applyRun() {
    if (!document) return;
    const id = Number(runText);
    if (!/^[1-9]\d*$/.test(runText) || !Number.isSafeInteger(id)) {
      setRunError('无效 Run ID，请输入正整数');
      return;
    }
    bind(document.id, id);
  }
  return { documents, document, source, bound, loading, uploading, documentError, runError, runText,
    changeRunText: (value: string) => { setDraftId(value); setRunError(''); },
    bind, applyRun, upload, retryDocuments: () => setReload(n => n + 1) };
}
