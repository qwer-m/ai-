const DUPLICATE_TAG = '@@DUPLICATE@@';
const CONTROL_TAGS = ['@@STATUS@@:', DUPLICATE_TAG, '@@CONTEXT_DEBUG@@:'];

type ControlLineHandlers = {
  onStatus: (message: string) => void;
  onContextDebug: (rawPayload: string) => void;
};

type DuplicateSplit =
  | { duplicateDetected: false; buffer: string }
  | { duplicateDetected: true; before: string; payload: string };

type DuplicateParseResult =
  | { ok: true; data: unknown }
  | { ok: false };

export function consumeCompleteControlLines(
  buffer: string,
  handlers: ControlLineHandlers,
): string {
  let nextBuffer = buffer;

  while (true) {
    const statusMatch = nextBuffer.match(/@@STATUS@@:(.*?)(?:\r?\n)/);
    if (statusMatch) {
      const statusMessage = (statusMatch[1] || '').trim();
      handlers.onStatus(statusMessage);
      nextBuffer = nextBuffer.replace(statusMatch[0], '');
      continue;
    }

    const contextDebugMatch = nextBuffer.match(/@@CONTEXT_DEBUG@@:(.*?)(?:\r?\n)/);
    if (contextDebugMatch) {
      const rawPayload = (contextDebugMatch[1] || '').trim();
      handlers.onContextDebug(rawPayload);
      nextBuffer = nextBuffer.replace(contextDebugMatch[0], '');
      continue;
    }

    return nextBuffer;
  }
}

export function splitDuplicateTag(buffer: string): DuplicateSplit {
  const duplicateIndex = buffer.indexOf(DUPLICATE_TAG);
  if (duplicateIndex < 0) {
    return { duplicateDetected: false, buffer };
  }

  return {
    duplicateDetected: true,
    before: buffer.slice(0, duplicateIndex),
    payload: buffer.slice(duplicateIndex + DUPLICATE_TAG.length),
  };
}

export function parseDuplicatePayload(payload: string): DuplicateParseResult {
  try {
    const jsonPayload = payload.startsWith(':') ? payload.substring(1) : payload;
    return { ok: true, data: JSON.parse(jsonPayload) };
  } catch {
    return { ok: false };
  }
}

export function splitFlushableStreamText(buffer: string): {
  flushText: string;
  remainder: string;
} {
  let safeEndIndex = buffer.length;
  const searchLimit = Math.max(0, buffer.length - 20);

  for (let index = buffer.length - 1; index >= searchLimit; index--) {
    const suffix = buffer.slice(index);
    if (CONTROL_TAGS.some((tag) => tag.startsWith(suffix))) {
      safeEndIndex = index;
    }
  }

  return {
    flushText: buffer.slice(0, safeEndIndex),
    remainder: buffer.slice(safeEndIndex),
  };
}

export function stripTrailingControlLines(buffer: string): string {
  return buffer
    .replace(/@@STATUS@@:.*$/gm, '')
    .replace(/@@CONTEXT_DEBUG@@:.*$/gm, '');
}
