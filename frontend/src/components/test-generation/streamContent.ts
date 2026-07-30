export function cleanStreamingContent(content: string) {
  if (!content) return '';
  return content
    .replace(/```json\s*/g, '')
    .replace(/```\s*/g, '')
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      return !(
        trimmed.startsWith('GEN_DIAG:')
        || trimmed.startsWith('GEN_COVERAGE_DIAG:')
        || trimmed.startsWith('@@STATUS@@:')
        || trimmed.startsWith('@@CONTEXT_DEBUG@@:')
        || trimmed.startsWith('@@DUPLICATE@@')
      );
    })
    .join('\n');
}

export function getCopyContent(result: any, streamingContent: string) {
  if (result && typeof result === 'object') return JSON.stringify(result, null, 2);
  if (typeof result === 'string' && result.trim()) return result;
  if (streamingContent) return cleanStreamingContent(streamingContent);
  return '';
}

type TerminalErrorPayload = {
  error?: unknown;
  abort_code?: unknown;
  message?: unknown;
  diagnostic?: Record<string, unknown>;
};

function extractTerminalJsonErrorPayload(content: string): TerminalErrorPayload | null {
  const cleaned = cleanStreamingContent(String(content || '')).trim();
  if (!cleaned.endsWith('}')) return null;

  // 终止错误对象位于流末尾；从后向前尝试对象起点，避免把前面的用例数组当成错误。
  let start = cleaned.lastIndexOf('{');
  while (start >= 0) {
    try {
      const parsed = JSON.parse(cleaned.slice(start));
      if (
        parsed
        && typeof parsed === 'object'
        && !Array.isArray(parsed)
        && ('error' in parsed || 'abort_code' in parsed)
      ) {
        return parsed as TerminalErrorPayload;
      }
    } catch {
      // 当前起点可能是 diagnostic 内层对象，继续寻找外层对象。
    }
    start = cleaned.lastIndexOf('{', start - 1);
  }
  return null;
}

function formatTerminalJsonError(payload: TerminalErrorPayload): string {
  const errorCode = String(payload.abort_code || payload.error || '').trim();
  const message = String(payload.message || '').trim();
  const diagnostic = payload.diagnostic && typeof payload.diagnostic === 'object'
    ? payload.diagnostic
    : {};

  if (errorCode === 'PUBLIC_BATCH_UNDERFILLED_ABORT') {
    const batchIndex = Number(diagnostic.batch_index || 0);
    const totalBatches = Number(diagnostic.total_batches || 0);
    const targetCount = Number(diagnostic.batch_target_count || 0);
    const acceptedCount = Number(diagnostic.accepted_case_count || 0);
    const diagnosticGap = Number(diagnostic.gap_count || 0);
    const gapCount = diagnosticGap > 0
      ? diagnosticGap
      : Math.max(0, targetCount - acceptedCount);
    const batchText = batchIndex > 0
      ? `第 ${batchIndex}${totalBatches > 0 ? `/${totalBatches}` : ''} 批`
      : '公共批次';
    const countText = targetCount > 0
      ? `目标 ${targetCount} 条，实际 ${acceptedCount} 条，缺口 ${gapCount} 条。`
      : '';
    return `${batchText}未补齐：${countText}${message}`.trim();
  }

  return message || errorCode || '后端返回终止错误';
}

export function extractTerminalStreamError(content: string): string | null {
  const matches = Array.from(String(content || '').matchAll(/(?:^|\r?\n)Error:\s*([^\r\n]+)/g));
  const message = matches[matches.length - 1]?.[1]?.trim();
  if (message) return message;

  const payload = extractTerminalJsonErrorPayload(content);
  return payload ? formatTerminalJsonError(payload) : null;
}

export function parseMultipleJsonArrays(text: string): any[] {
  if (extractTerminalStreamError(text)) return [];

  const clean = cleanStreamingContent(text).trim();
  if (!clean) return [];

  const foundItems: any[] = [];
  let cursor = 0;

  while (cursor < clean.length) {
    const startArray = clean.indexOf('[', cursor);
    if (startArray === -1) break;
    cursor = startArray + 1;

    while (cursor < clean.length) {
      while (cursor < clean.length && /[\s,]/.test(clean[cursor])) cursor++;
      if (cursor >= clean.length) break;
      if (clean[cursor] === ']') {
        cursor++;
        break;
      }

      if (clean[cursor] === '{') {
        const startObj = cursor;
        let balance = 0;
        let endObj = -1;
        let inString = false;
        let escape = false;

        for (let i = startObj; i < clean.length; i++) {
          const char = clean[i];
          if (escape) {
            escape = false;
            continue;
          }
          if (char === '\\') {
            escape = true;
            continue;
          }
          if (char === '"') {
            inString = !inString;
            continue;
          }

          if (!inString) {
            if (char === '{') balance++;
            else if (char === '}') {
              balance--;
              if (balance === 0) {
                endObj = i;
                break;
              }
            }
          }
        }

        if (endObj !== -1) {
          const jsonStr = clean.substring(startObj, endObj + 1);
          try {
            const obj = JSON.parse(jsonStr);
            if (obj && typeof obj === 'object') foundItems.push(obj);
          } catch {}
          cursor = endObj + 1;
        } else {
          cursor = clean.length;
        }
      } else {
        cursor++;
      }
    }
  }

  if (foundItems.length === 0) {
    try {
      const parsed = JSON.parse(clean);
      if (Array.isArray(parsed)) return parsed;
    } catch {}
  }

  return foundItems;
}
