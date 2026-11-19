/* Full-text search stays in the browser and uses only this site's static index. */
(() => {
  "use strict";
  const script = document.currentScript;
  const root = new URL("../", script.src);
  const chinese = document.documentElement.lang.toLowerCase().startsWith("zh");
  const language = chinese ? "zh-TW" : "en";
  const t = chinese ? {
    label: "搜尋文件", placeholder: "輸入關鍵字或指令…", scope: "搜尋範圍",
    current: "繁體中文", all: "全部語言", hint: "輸入字串搜尋文件；按 / 開始、Esc 關閉。",
    loading: "正在搜尋…", empty: "找不到符合的段落，試試其他字串或全部語言。",
    error: "無法載入搜尋資料，請重新輸入以重試。", count: n => `${n} 個符合段落`,
    limited: n => `顯示前 20 個，共 ${n} 個符合段落`, en: "English", zh: "繁體中文",
  } : {
    label: "Search docs", placeholder: "Search a phrase or command…", scope: "Search scope",
    current: "English", all: "All languages", hint: "Search any string. Press / to focus, Esc to close.",
    loading: "Searching…", empty: "No matching passages. Try another string or all languages.",
    error: "Could not load search data. Type again to retry.", count: n => `${n} matching passage${n === 1 ? "" : "s"}`,
    limited: n => `Showing the first 20 of ${n} matching passages`, en: "English", zh: "繁體中文",
  };
  const navigation = document.querySelector("nav.toc .wrap");
  if (!navigation) return;

  // Keep generated result links stable without rewriting the authored headings.
  document.querySelectorAll("main h2, main h3").forEach((heading, index) => {
    if (!heading.id) heading.id = `doc-search-section-${index + 1}`;
  });
  if (location.hash.startsWith("#doc-search-section-")) {
    requestAnimationFrame(() => document.getElementById(location.hash.slice(1))?.scrollIntoView());
  }

  const element = (tag, className, content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  };
  const widget = element("div", "doc-search");
  const form = element("form", "doc-search-form");
  form.setAttribute("role", "search");
  const label = element("label", "doc-search-label", t.label);
  label.htmlFor = "doc-search-input";
  const input = element("input", "doc-search-input");
  Object.assign(input, {id: "doc-search-input", type: "search", placeholder: t.placeholder,
    autocomplete: "off", spellcheck: false, maxLength: 200});
  input.setAttribute("aria-controls", "doc-search-results");
  input.setAttribute("aria-expanded", "false");
  const scope = element("select", "doc-search-scope");
  scope.setAttribute("aria-label", t.scope);
  for (const [value, title] of [["current", t.current], ["all", t.all]]) {
    const option = element("option", "", title);
    option.value = value;
    scope.append(option);
  }
  const panel = element("div", "doc-search-results");
  panel.id = "doc-search-results";
  panel.hidden = true;
  const status = element("p", "doc-search-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const results = element("ol", "doc-search-list");
  panel.append(status, results);
  form.append(label, input, scope);
  widget.append(form, panel);
  navigation.prepend(widget);

  const normalize = value => value.normalize("NFKC").toLowerCase().replace(/\s+/gu, " ").trim();
  let indexPromise;
  function loadIndex() {
    if (window.LLaDARSearchIndex) return Promise.resolve(window.LLaDARSearchIndex);
    if (!indexPromise) {
      indexPromise = new Promise((resolve, reject) => {
        const asset = document.createElement("script");
        asset.src = new URL("assets/search-index.js", root).href;
        asset.onload = () => {
          if (window.LLaDARSearchIndex?.version === 1) resolve(window.LLaDARSearchIndex);
          else reject(new Error("Invalid search index"));
          asset.remove();
        };
        asset.onerror = () => { asset.remove(); reject(new Error("Search index unavailable")); };
        document.head.append(asset);
      }).catch(error => { indexPromise = undefined; throw error; });
    }
    return indexPromise;
  }

  function snippet(value, query) {
    const content = value.normalize("NFKC");
    const match = content.toLowerCase().indexOf(query);
    const start = Math.max(0, match - 45);
    const end = Math.min(content.length, start + Math.max(150, query.length + 50));
    const excerpt = content.slice(start, end);
    const paragraph = element("p", "doc-search-snippet");
    if (start) paragraph.append("…");
    let cursor = 0;
    while (cursor < excerpt.length) {
      const next = excerpt.toLowerCase().indexOf(query, cursor);
      if (next < 0) { paragraph.append(excerpt.slice(cursor)); break; }
      paragraph.append(excerpt.slice(cursor, next), element("mark", "", excerpt.slice(next, next + query.length)));
      cursor = next + query.length;
    }
    if (end < content.length) paragraph.append("…");
    return paragraph;
  }

  let revision = 0;
  let timer;
  function fitPanel() {
    if (panel.hidden) return;
    const bounds = widget.getBoundingClientRect();
    const below = Math.max(96, innerHeight - bounds.bottom - 22);
    const above = Math.max(96, bounds.top - 16);
    const upwards = below < 200 && above > below;
    panel.classList.toggle("doc-search-above", upwards);
    panel.style.maxHeight = `${Math.min(innerHeight * 0.65, 576, upwards ? above : below)}px`;
  }
  function close() {
    clearTimeout(timer);
    revision += 1;
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
  }
  async function search() {
    const run = ++revision;
    const query = normalize(input.value);
    panel.hidden = false;
    fitPanel();
    input.setAttribute("aria-expanded", "true");
    results.replaceChildren();
    if (!query) { status.textContent = t.hint; return; }
    status.textContent = t.loading;
    try {
      const index = await loadIndex();
      if (run !== revision) return;
      const hits = [];
      for (const doc of index.documents) {
        if (scope.value !== "all" && doc.lang !== language) continue;
        const titleMatch = normalize(doc.title).includes(query);
        let matched = false;
        for (const section of doc.sections) {
          const headingMatch = normalize(section.heading).includes(query);
          const contentMatch = normalize(section.text).includes(query);
          if (headingMatch || contentMatch) {
            matched = true;
            hits.push({doc, section, score: (titleMatch ? 100 : 0) + (headingMatch ? 40 : 0) + (contentMatch ? 10 : 0)});
          }
        }
        if (titleMatch && !matched && doc.sections.length) {
          hits.push({doc, section: doc.sections[0], score: 100});
        }
      }
      hits.sort((a, b) => b.score - a.score || a.doc.url.localeCompare(b.doc.url));
      status.textContent = hits.length ? (hits.length > 20 ? t.limited(hits.length) : t.count(hits.length)) : t.empty;
      for (const {doc, section} of hits.slice(0, 20)) {
        const item = element("li", "doc-search-result");
        const link = element("a", "doc-search-link");
        const url = new URL(doc.url, root);
        if (section.anchor) url.hash = section.anchor;
        link.href = url.href;
        link.append(element("strong", "", section.heading || doc.title),
          element("span", "doc-search-page", `${doc.title} · ${doc.lang === "en" ? t.en : t.zh}`),
          snippet(section.text || section.heading, query));
        item.append(link);
        results.append(item);
      }
    } catch {
      if (run === revision) status.textContent = t.error;
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    revision += 1;
    results.replaceChildren();
    panel.hidden = false;
    fitPanel();
    input.setAttribute("aria-expanded", "true");
    status.textContent = normalize(input.value) ? t.loading : t.hint;
    timer = setTimeout(search, 120);
  });
  input.addEventListener("focus", search);
  scope.addEventListener("change", search);
  form.addEventListener("submit", event => { event.preventDefault(); clearTimeout(timer); search(); });
  widget.addEventListener("keydown", event => { if (event.key === "Escape") { event.preventDefault(); close(); } });
  document.addEventListener("pointerdown", event => { if (!widget.contains(event.target)) close(); });
  document.addEventListener("keydown", event => {
    const target = event.target;
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey &&
        !target.closest("input, textarea, select, [contenteditable]")) {
      event.preventDefault(); input.focus();
    }
  });
  window.addEventListener("resize", fitPanel);
  window.addEventListener("scroll", fitPanel, {passive: true});
})();
