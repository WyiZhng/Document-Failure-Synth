/**
 * Deterministic two-column pagination for marked synth HTML blocks.
 * Injected by Playwright; returns placement table in page pixel coordinates.
 *
 * 中英文列独立分页。普通 text 块可以在列底部切成多个物理片段,但每个片段
 * 保留同一个 source node id,供下游重建成一个跨页逻辑节点。
 */
function paginateDocument(config) {
  const { width, height, margin, columnGap } = config;
  const layout = config.columnLayout === "en-zh" ? "en-zh" : "zh-en";
  const synchronizePairs = Boolean(config.synchronizePairs);

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
  const zhX = hasEn && layout === "en-zh" ? rightX : leftX;
  const enX = hasEn && layout === "en-zh" ? leftX : rightX;

  function sideOf(lang) {
    if (!hasEn) return "left";
    if (layout === "en-zh") return lang === "en" ? "left" : "right";
    return lang === "zh" ? "left" : "right";
  }

  const measureRoot = document.createElement("div");
  measureRoot.style.cssText =
    "position:absolute;left:-99999px;top:0;visibility:hidden;width:0;height:0;overflow:hidden;";
  document.body.appendChild(measureRoot);

  function applyColumnSizing(el) {
    if (el.tagName === "IMG") {
      el.style.maxWidth = `${colW}px`;
      el.style.maxHeight = `${contentH}px`;
      el.style.width = "auto";
      el.style.height = "auto";
    } else {
      el.style.width = `${colW}px`;
    }
    el.style.boxSizing = "border-box";
    el.style.margin = "0";
  }

  function measureHeight(el, textOverride = null) {
    const clone = el.cloneNode(true);
    if (textOverride !== null) clone.textContent = textOverride;
    applyColumnSizing(clone);
    clone.style.position = "static";
    measureRoot.innerHTML = "";
    measureRoot.appendChild(clone);
    return clone.getBoundingClientRect().height;
  }

  const BLOCK_GAP = 8;
  const pageBottom = margin + contentH;

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
  const columns = {
    left: { page: 0, y: margin },
    right: { page: 0, y: margin },
  };

  function ensurePage(page) {
    while (pages.length <= page) pages.push({ zh: [], en: [] });
  }

  function advanceColumn(side) {
    columns[side].page += 1;
    columns[side].y = margin;
    ensurePage(columns[side].page);
  }

  function fits(y, blockH) {
    return y + blockH <= pageBottom;
  }

  function canSplit(item) {
    const tagName = item.el.tagName.toLowerCase();
    return (
      item.category === "text" &&
      tagName === "div" &&
      item.el.children.length === 0 &&
      !item.el.style.height &&
      item.text.length > 0
    );
  }

  function largestFittingPrefix(item, text, availableH) {
    const chars = Array.from(text);
    if (!chars.length || availableH <= 0) return null;

    let low = 1;
    let high = chars.length;
    let best = 0;
    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      const candidate = chars.slice(0, mid).join("");
      if (measureHeight(item.el, candidate) <= availableH) {
        best = mid;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }

    if (!best) return null;
    return {
      head: chars.slice(0, best).join(""),
      tail: chars.slice(best).join(""),
    };
  }

  function appendSlot(item, page, y, h, textOverride, fragmentIndex) {
    ensurePage(page);
    pages[page][item.lang].push({
      item,
      y,
      h,
      textOverride,
      fragmentIndex,
    });
  }

  function placeItem(item) {
    const side = sideOf(item.lang);
    const split = canSplit(item);
    let remaining = split ? item.text : null;
    let fragmentIndex = 0;

    while (true) {
      const state = columns[side];
      ensurePage(state.page);
      const fullH = measureHeight(item.el, split ? remaining : null);

      if (fits(state.y, fullH)) {
        appendSlot(item, state.page, state.y, fullH, split ? remaining : null, fragmentIndex);
        state.y += fullH + BLOCK_GAP;
        return;
      }

      if (!split) {
        if (state.y > margin) {
          advanceColumn(side);
          continue;
        }
        // Preserve the old behavior for an indivisible block taller than a page.
        appendSlot(item, state.page, state.y, fullH, null, fragmentIndex);
        state.y += fullH + BLOCK_GAP;
        return;
      }

      const availableH = state.y > margin ? pageBottom - state.y : contentH;
      const fragment = largestFittingPrefix(item, remaining, availableH);
      if (!fragment) {
        if (state.y > margin) {
          advanceColumn(side);
          continue;
        }
        // A pathological unbreakable text block should still make progress.
        appendSlot(item, state.page, state.y, fullH, remaining, fragmentIndex);
        state.y += fullH + BLOCK_GAP;
        return;
      }

      const fragmentH = measureHeight(item.el, fragment.head);
      appendSlot(item, state.page, state.y, fragmentH, fragment.head, fragmentIndex);
      state.y += fragmentH;
      remaining = fragment.tail;
      fragmentIndex += 1;
      if (!remaining) {
        state.y += BLOCK_GAP;
        return;
      }
    }
  }

  function alignColumnToPage(side, page) {
    const state = columns[side];
    while (state.page < page) {
      advanceColumn(side);
    }
  }

  function alignPairPage() {
    const page = Math.max(columns.left.page, columns.right.page);
    alignColumnToPage("left", page);
    alignColumnToPage("right", page);
    return page;
  }

  function fullItemHeight(item) {
    if (!item) return 0;
    return measureHeight(item.el, canSplit(item) ? item.text : null);
  }

  function needsPairPageBreak(item, side) {
    if (!item) return false;
    const state = columns[side];
    const fullH = fullItemHeight(item);
    // A block that can be split and is taller than one whole page must start
    // where it currently is; placeItem will split it. Every ordinary block
    // that does not fit moves the whole pair to the next page instead.
    return fullH <= contentH && !fits(state.y, fullH);
  }

  function placePair(pair) {
    if (!pair.zh || !pair.en) {
      if (pair.zh) placeItem(pair.zh);
      if (pair.en) placeItem(pair.en);
      alignPairPage();
      return;
    }

    alignPairPage();
    while (
      needsPairPageBreak(pair.zh, sideOf(pair.zh.lang)) ||
      needsPairPageBreak(pair.en, sideOf(pair.en.lang))
    ) {
      advanceColumn("left");
      advanceColumn("right");
    }

    // If one side is an oversized splittable block, it may extend to a later
    // page. The other side is aligned to that continuation page before it is
    // placed, so subsequent pairs still share a page boundary.
    placeItem(pair.zh);
    alignColumnToPage(sideOf(pair.en.lang), columns[sideOf(pair.zh.lang)].page);
    placeItem(pair.en);
    alignPairPage();
  }

  for (const pair of pairs) {
    if (synchronizePairs) {
      placePair(pair);
    } else {
      if (pair.zh) placeItem(pair.zh);
      if (pair.en) placeItem(pair.en);
    }
  }

  document.body.innerHTML = "";
  document.body.style.margin = "0";
  document.body.style.padding = "0";

  const root = document.createElement("div");
  root.id = "synth-root";
  document.body.appendChild(root);

  const placements = [];

  function placeBlock(pageEl, sourceEl, x, y, placedPage, slot) {
    const el = sourceEl.cloneNode(true);
    if (slot.textOverride !== null) el.textContent = slot.textOverride;
    applyColumnSizing(el);
    el.style.position = "absolute";
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.setAttribute("data-placed", "true");
    el.setAttribute("data-fragment-index", String(slot.fragmentIndex));
    pageEl.appendChild(el);

    const pageRect = pageEl.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    placements.push({
      node_id: slot.item.nodeId,
      lang: slot.item.lang,
      category: slot.item.category,
      page: placedPage,
      fragment_index: slot.fragmentIndex,
      x1: rect.left - pageRect.left,
      y1: rect.top - pageRect.top,
      x2: rect.right - pageRect.left,
      y2: rect.bottom - pageRect.top,
      text: slot.textOverride === null ? slot.item.text : slot.textOverride,
      order: slot.item.order,
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
      placeBlock(pageEl, slot.item.el, zhX, slot.y, idx, slot);
    }
    for (const slot of pages[idx].en) {
      placeBlock(pageEl, slot.item.el, enX, slot.y, idx, slot);
    }
  }

  measureRoot.remove();
  return placements;
}
