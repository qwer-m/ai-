export const highlightJson = (text: string) => {
  if (!text) return { __html: "" };

  let html = "";
  let lastIndex = 0;

  // Token 正则匹配：字符串、数字、关键字、标点符号
  const tokenRegex =
    /("(?:[^\\"]|\\.)*")|(\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b(?:true|false|null)\b)|([\{\}\[\]:,])/g;

  let match;
  let errorPos = -1;
  try {
    JSON.parse(text);
  } catch (e) {
    const msg = (e as Error).message;
    const m = msg.match(/at position (\d+)/);
    if (m) errorPos = parseInt(m[1]);
  }

  while ((match = tokenRegex.exec(text)) !== null) {
    // 匹配片段前的普通文本
    if (match.index > lastIndex) {
      const snippet = text.slice(lastIndex, match.index);
      html += `<span class="json-text">${snippet.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
    }

    const [full, str, num, kw, punct] = match;
    let cls = "";
    if (str) {
      const rest = text.slice(tokenRegex.lastIndex);
      cls = /^\s*:/.test(rest) ? "json-key" : "json-string";
    } else if (num) {
      cls = "json-number";
    } else if (kw) {
      cls = "json-boolean";
    } else if (punct) {
      cls = "json-punctuation";
    }

    // 如果错误位置落在 token 范围内，则标记为错误
    if (errorPos >= match.index && errorPos < match.index + full.length) {
      cls += " json-error";
    }

    if (cls) {
      html += `<span class="${cls}">${full.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
    } else {
      html += `<span class="json-text">${full.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
    }

    lastIndex = tokenRegex.lastIndex;
  }

  if (lastIndex < text.length) {
    const snippet = text.slice(lastIndex);
    if (errorPos >= lastIndex) {
      const localPos = errorPos - lastIndex;
      if (localPos < snippet.length) {
        const pre = snippet.slice(0, localPos);
        const char = snippet.charAt(localPos);
        const post = snippet.slice(localPos + 1);
        html += `<span class="json-text">${pre.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
        html += `<span class="json-error json-text">${char.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
        html += `<span class="json-text">${post.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
      } else {
        html += `<span class="json-text">${snippet.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
      }
    } else {
      html += `<span class="json-text">${snippet.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>`;
    }
  }

  // 添加换行符以保证高亮层与文本层高度一致
  return { __html: html + "\n" };
};
