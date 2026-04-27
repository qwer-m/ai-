import { TestGenerationPageView } from '../../test-generation/TestGenerationPageView';
import type { TestGenerationProps } from '../../test-generation/types';
import { useTestGenerationController } from './hooks/useTestGenerationController';

export function TestGeneration(props: TestGenerationProps) {
  const controller = useTestGenerationController(props);
  return <TestGenerationPageView {...controller} />;
}
