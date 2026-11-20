import { useEffect, useState } from 'react';
import { useEvaluationResources } from '../evaluation/state/useEvaluationResources';
import { TestCaseCoveragePanel } from '../evaluation/TestCaseCoveragePanel';

function ProjectEvaluation({ projectId, active, onLog }: { projectId: number; active: boolean; onLog: (message: string) => void }) {
  const resources = useEvaluationResources(projectId, active);
  return <TestCaseCoveragePanel key={`${resources.document?.id ?? 0}/${resources.source?.run_id ?? 0}`}
    resources={resources} onLog={onLog} />;
}

export function Evaluation({ projectId, active, onLog }: { projectId: number | null; active: boolean; onLog: (message: string) => void }) {
  const [opened, setOpened] = useState(active);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  if (!opened && !active) return null;
  if (!projectId) return <p className="p-3" role="status">请先选择项目。</p>;
  return <ProjectEvaluation key={projectId} projectId={projectId} active={active} onLog={onLog} />;
}
