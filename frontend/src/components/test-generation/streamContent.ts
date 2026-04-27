export function cleanStreamingContent(content: string) {
  if (!content) return '';
  return content.replace(/```json\s*/g, '').replace(/```\s*/g, '');
}

export function getCopyContent(result: any, streamingContent: string) {
  if (result && typeof result === 'object') return JSON.stringify(result, null, 2);
  if (typeof result === 'string' && result.trim()) return result;
  if (streamingContent) return cleanStreamingContent(streamingContent);
  return '';
}

export function parseMultipleJsonArrays(text: string): any[] {
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
