import { cleanStreamingContent, parseMultipleJsonArrays } from '../../../test-generation/streamContent';
import {
  deduplicateStandardCases,
  normalizeStandardCases,
  sanitizeStandardCases,
  validateStandardCases,
} from './testGenerationCaseUtils';

type AssembleGenerationResultInput = {
  rawText: string;
  appendMode: boolean;
  existingCases: any[];
  expectedCount: number;
  onLog: (message: string) => void;
};

export function buildStreamingPreviewCases(
  rawText: string,
  appendMode: boolean,
  existingCases: any[],
): any[] | null {
  const parsedCases = parseMultipleJsonArrays(rawText);
  const sanitizedCases = sanitizeStandardCases(parsedCases);
  if (sanitizedCases.valid.length === 0) return null;

  const newCases = deduplicateStandardCases(sanitizedCases.valid);
  return appendMode
    ? deduplicateStandardCases([...normalizeStandardCases(existingCases), ...newCases])
    : newCases;
}

export function assembleFinalGeneratedCases({
  rawText,
  appendMode,
  existingCases,
  expectedCount,
  onLog,
}: AssembleGenerationResultInput): any[] {
  const skipNormalize = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('skipNormalize') === '1';
  const cleaned = cleanStreamingContent(rawText).trim();
  if (!cleaned) {
    throw new Error('Generation result is empty; check model config, quota, or network and retry.');
  }

  const errorMatches = Array.from(cleaned.matchAll(/(?:^|\r?\n)Error:\s*([^\r\n]+)/g));
  let parsedCases: any[] = [];
  try {
    parsedCases = parseMultipleJsonArrays(rawText);
  } catch {
    parsedCases = [];
  }

  let finalCases: any[] = [];
  if (skipNormalize) {
    const mergedCases = appendMode
      ? [...(Array.isArray(existingCases) ? existingCases : []), ...parsedCases]
      : parsedCases;
    finalCases = deduplicateStandardCases(normalizeStandardCases(mergedCases));
  } else {
    const sanitizedCases = sanitizeStandardCases(parsedCases);
    const validCases = sanitizedCases.valid;

    if (sanitizedCases.dropped.length > 0) {
      onLog(`Filtered ${sanitizedCases.dropped.length} invalid case(s) from streamed output.`);
    }

    if (validCases.length === 0) {
      if (errorMatches.length > 0) {
        const lastError = errorMatches[errorMatches.length - 1]?.[1] || '';
        throw new Error(
          lastError
            ? `Generation failed: ${lastError}`
            : 'Generation failed: backend returned an error',
        );
      }

      throw Array.isArray(parsedCases) && parsedCases.length === 0
        ? new Error('Generation result is an empty array, please retry')
        : new Error('Generation result is not an array of case objects; ensure the model returns a JSON array of objects');
    }

    const newCases = deduplicateStandardCases(validCases);
    const newCasesValidation = validateStandardCases(newCases);
    if (!newCasesValidation.ok) {
      onLog(`Validation warning (tolerated): ${newCasesValidation.error}`);
    }

    if (appendMode) {
      const mergedCases = deduplicateStandardCases(
        normalizeStandardCases([...normalizeStandardCases(existingCases), ...newCases]),
      );
      const mergedValidation = validateStandardCases(mergedCases);
      if (!mergedValidation.ok) {
        onLog(`Merged validation warning (tolerated): ${mergedValidation.error}`);
      }
      finalCases = mergedCases;
    } else {
      finalCases = deduplicateStandardCases(newCases);
    }
  }

  if (!appendMode && finalCases.length > expectedCount) {
    finalCases = finalCases.slice(0, expectedCount);
  }

  if (!Array.isArray(finalCases) || finalCases.length === 0) {
    throw new Error('Generation completed but no valid test cases were produced');
  }

  return finalCases;
}
