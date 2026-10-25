/* A small, safe Markdown renderer for streamed model output.
   HTML in the source is always escaped; only the Markdown below becomes markup. */
"use strict";
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = esc(s);
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  }

  function table(rows) {
    const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const head = cells(rows[0]);
    const body = rows.slice(2).map(cells);
    return `<table><thead><tr>${head.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>` +
      body.map((r) => `<tr>${head.map((_, i) => `<td>${inline(r[i] || "")}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
  }

  function list(lines) {
    // nested lists by indentation
    let html = "", stack = [];
    for (const line of lines) {
      const m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (!m) { html += " " + inline(line.trim()); continue; }
      const depth = Math.floor(m[1].replace(/\t/g, "  ").length / 2), tag = /\d/.test(m[2]) ? "ol" : "ul";
      while (stack.length > depth + 1) html += `</li></${stack.pop()}>`;
      if (stack.length === depth + 1 && stack[depth] !== tag) html += `</li></${stack.pop()}>`;
      if (stack.length < depth + 1) { html += `<${tag}>`; stack.push(tag); } else html += "</li>";
      let item = m[3].replace(/^\[( |x)\]\s+/i, (_, x) => (x.trim() ? "☑ " : "☐ "));
      html += `<li>${inline(item)}`;
    }
    while (stack.length) html += `</li></${stack.pop()}>`;
    return html;
  }

  function render(src, streaming) {
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    let out = "", i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+#.-]*)\s*$/);
      if (fence) {
        const buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
        i++;
        const lang = fence[1] ? `<span class="lang">${esc(fence[1])}</span>` : "";
        out += `<pre>${lang}<button class="copy" data-copy aria-label="Copy code"><svg><use href="#i-copy"/></svg></button><code>${esc(buf.join("\n"))}</code></pre>`;
        continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) { out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; i++; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { out += "<hr>"; i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        out += table(rows);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        out += `<blockquote>${render(buf.join("\n"))}</blockquote>`;
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const buf = [];
        while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && buf.length))) buf.push(lines[i++]);
        out += list(buf);
        continue;
      }
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|>|([-*+]|\d+[.)])\s+|\|.*\|\s*$)/.test(lines[i])) buf.push(lines[i++]);
      if (!buf.length) buf.push(lines[i++]);
      out += `<p>${buf.map(inline).join("<br>")}</p>`;
    }
    if (streaming) out = out.replace(/(<\/(p|li|td|h\d|code)>)(\s*<\/[^>]+>)*\s*$/, (m) => m.replace(/^(<\/(p|li|td|h\d|code)>)/, '<span class="caret"></span>$1'));
    return out;
  }

  window.MD = { render, esc };
})();
