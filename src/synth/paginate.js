/**
 * Deterministic two-column pagination for marked synth HTML blocks.
 * Injected by Playwright; returns placement table in page pixel coordinates.
 *
 * 中英成对同页:下游 merge 用 member 身份对齐,无法把跨页的 zh/en 半节点合并回一个节点。
 */
function paginateDocument(config) {
  const { width, height, margin, columnGap } = config;

  const allNodes = Array.from(document.querySelectorAll("[data-node-id]"));
  const items = allNodes.map((el, order) => ({
    el,
    order,
    nodeId: el.getAttribute("data-node-id"),
    category: el.getAttribute("data-category") || "text",
    lang: el.getAttribute("data-lang") || "zh",
    text: (el.textContent || "").trim(),
  }));

  const zhItems = items.filter((item) => item.lang !== "en");
  const enItems = items.filter((item) => item.lang === "en");
  const hasEn = enItems.length > 0;

  const contentW = width - 2 * margin;
  const contentH = height - 2 * margin;
  const colW = hasEn ? (contentW - columnGap) / 2 : contentW;
  const leftX = margin;
  const rightX = margin + colW + columnGap;

  const measureRoot = document.createElement("div");
  measureRoot.style.cssText =
    "position:absolute;left:-99999px;top:0;visibility:hidden;width:0;height:0;overflow:hidden;";
  document.body.appendChild(measureRoot);

  function applyColumnSizing(el) {
    if (el.tagName === "IMG") {
      el.style.maxWidth = `${colW}px`;
      el.style.width = "auto";
      el.style.height = "auto";
    } else {
      el.style.width = `${colW}px`;
    }
    el.style.boxSizing = "border-box";
    el.style.margin = "0";
  }

  function measureHeight(el) {
    const clone = el.cloneNode(true);
    applyColumnSizing(clone);
    clone.style.position = "static";
    measureRoot.innerHTML = "";
    measureRoot.appendChild(clone);
    return clone.getBoundingClientRect().height;
  }

  const BLOCK_GAP = 8;

  const enBuckets = new Map();
  for (const en of enItems) {
    if (!enBuckets.has(en.nodeId)) enBuckets.set(en.nodeId, []);
    enBuckets.get(en.nodeId).push(en);
  }
  const usedEn = new Set();
  const pairs = [];
  for (const zh of zhItems) {
    const bucket = enBuckets.get(zh.nodeId) || [];
    const en = bucket.find((item) => !usedEn.has(item));
    if (en) usedEn.add(en);
    pairs.push({ zh, en: en || null });
  }
  for (const en of enItems) {
    if (!usedEn.has(en)) pairs.push({ zh: null, en });
  }

  const pages = [{ zh: [], en: [] }];
  let pageIdx = 0;
  let leftY = margin;
  let rightY = margin;

  function fits(y, blockH) {
    return y + blockH <= margin + contentH;
  }

  function newPage() {
    pageIdx += 1;
    pages[pageIdx] = { zh: [], en: [] };
    leftY = margin;
    rightY = margin;
  }

  for (const pair of pairs) {
    const zhH = pair.zh ? measureHeight(pair.zh.el) : 0;
    const enH = pair.en ? measureHeight(pair.en.el) : 0;
    const zhNeedsPage = Boolean(pair.zh) && !fits(leftY, zhH) && leftY > margin;
    const enNeedsPage = Boolean(pair.en) && !fits(rightY, enH) && rightY > margin;
    if (zhNeedsPage || enNeedsPage) newPage();

    if (pair.zh) {
      pages[pageIdx].zh.push({ item: pair.zh, y: leftY, h: zhH });
      leftY += zhH + BLOCK_GAP;
    }
    if (pair.en) {
      pages[pageIdx].en.push({ item: pair.en, y: rightY, h: enH });
      rightY += enH + BLOCK_GAP;
    }
  }

  document.body.innerHTML = "";
  document.body.style.margin = "0";
  document.body.style.padding = "0";

  const root = document.createElement("div");
  root.id = "synth-root";
  document.body.appendChild(root);

  const placements = [];

  function placeBlock(pageEl, sourceEl, x, y, placedPage, item) {
    const el = sourceEl.cloneNode(true);
    applyColumnSizing(el);
    el.style.position = "absolute";
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.setAttribute("data-placed", "true");
    pageEl.appendChild(el);

    const pageRect = pageEl.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    placements.push({
      node_id: item.nodeId,
      lang: item.lang,
      category: item.category,
      page: placedPage,
      x1: rect.left - pageRect.left,
      y1: rect.top - pageRect.top,
      x2: rect.right - pageRect.left,
      y2: rect.bottom - pageRect.top,
      text: item.text,
      order: item.order,
    });
  }

  for (let idx = 0; idx < pages.length; idx += 1) {
    const pageEl = document.createElement("div");
    pageEl.className = "synth-page";
    pageEl.style.position = "relative";
    pageEl.style.width = `${width}px`;
    pageEl.style.height = `${height}px`;
    pageEl.style.overflow = "hidden";
    pageEl.style.background = "#fff";
    pageEl.style.boxSizing = "border-box";
    root.appendChild(pageEl);

    for (const slot of pages[idx].zh) {
      placeBlock(pageEl, slot.item.el, leftX, slot.y, idx, slot.item);
    }
    for (const slot of pages[idx].en) {
      placeBlock(pageEl, slot.item.el, rightX, slot.y, idx, slot.item);
    }
  }

  measureRoot.remove();
  return placements;
}
