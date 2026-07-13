import { RagWorkbenchPanel } from './rag/RagWorkbenchPanel';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function RagValidationPanel({ projectId, onLog }: Props) {
  return <RagWorkbenchPanel projectId={projectId} onLog={onLog} />;
}

