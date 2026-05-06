import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pptxgen from 'pptxgenjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SLIDE_W = 13.333;
const SLIDE_H = 7.5;
const DEFAULT_FONT_FACE = 'Microsoft YaHei';

function argValue(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function stripHash(value = '', fallback = '0F172A') {
  const text = String(value || '').trim();
  const match = text.match(/^#?([0-9a-fA-F]{6})$/);
  if (match) return match[1].toUpperCase();
  const fb = String(fallback || '').trim().match(/^#?([0-9a-fA-F]{6})$/);
  return fb ? fb[1].toUpperCase() : '0F172A';
}

function luminance(hex) {
  const raw = stripHash(hex);
  const channels = [0, 2, 4].map((i) => parseInt(raw.slice(i, i + 2), 16) / 255);
  const linear = channels.map((value) => (value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function readableOn(background, preferred = '0F172A') {
  if (luminance(background) < 0.42) return 'FFFFFF';
  const prefer = stripHash(preferred);
  const bg = luminance(background);
  const fg = luminance(prefer);
  const contrast = (Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05);
  return contrast >= 4.5 ? prefer : '0F172A';
}

function themeOf(deck) {
  const raw = deck.theme || {};
  const text = stripHash(raw.text, '0F172A');
  let coverBg = stripHash(raw.cover_bg || raw.coverBg || raw.text, text);
  if (luminance(coverBg) >= 0.42) coverBg = luminance(text) < 0.42 ? text : '0F172A';
  return {
    name: raw.name || 'Executive Blue',
    accent: stripHash(raw.accent, '2F6BFF'),
    accent2: stripHash(raw.accent2, '7C3AED'),
    background: stripHash(raw.background, 'F8FAFC'),
    surface: stripHash(raw.surface, 'FFFFFF'),
    text,
    muted: stripHash(raw.muted, '64748B'),
    success: stripHash(raw.success, '16A34A'),
    warning: stripHash(raw.warning, 'F97316'),
    coverBg,
    coverTitle: 'FFFFFF',
    coverSubtitle: 'E2E8F0',
    coverMuted: 'CBD5E1',
    fontFace: cleanText(raw.fontFace || raw.font_face) || DEFAULT_FONT_FACE,
  };
}

function addText(slide, text, opts, theme) {
  const value = Array.isArray(text) ? cleanLines(text).join('\n') : cleanText(text);
  if (!value.trim()) return;
  slide.addText(value, {
    fontFace: theme.fontFace,
    margin: 0.07,
    breakLine: false,
    ...opts,
  });
}

function addTitle(slide, title, theme) {
  addText(slide, budgetText(title, { maxWeight: 44, maxLines: 1 }), {
    x: 0.55, y: 0.35, w: 12.0, h: 0.55,
    fontSize: 25, bold: true, color: theme.text,
  }, theme);
}

function addFooter(slide, page, total, theme, color = theme.muted) {
  addText(slide, `${page}/${total}`, {
    x: 11.45, y: 7.05, w: 1.1, h: 0.22,
    fontSize: 8.5, color, align: 'right',
  }, theme);
}

function contentOf(slide) {
  return slide && typeof slide.content === 'object' && slide.content ? slide.content : {};
}

function cleanText(value) {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  if (!text || text === 'undefined' || text === 'null') return '';
  return text;
}

function cleanLines(values) {
  return (Array.isArray(values) ? values : [])
    .map(cleanText)
    .filter(Boolean);
}

function charWeight(ch) {
  return /[\u2E80-\u9FFF\uF900-\uFAFF]/.test(ch) ? 2 : 1;
}

function textWeight(value) {
  return Array.from(cleanText(value)).reduce((sum, ch) => sum + charWeight(ch), 0);
}

function truncateByWeight(value, maxWeight) {
  const text = cleanText(value);
  if (!maxWeight || textWeight(text) <= maxWeight) return text;
  let used = 0;
  let out = '';
  const limit = Math.max(1, maxWeight - 3);
  for (const ch of Array.from(text)) {
    const next = used + charWeight(ch);
    if (next > limit) break;
    out += ch;
    used = next;
  }
  return `${out.trimEnd()}...`;
}

function wrapByWeight(value, maxLineWeight, maxLines) {
  const text = cleanText(value);
  if (!text) return '';
  const lines = [];
  let line = '';
  let used = 0;
  for (const ch of Array.from(text)) {
    if (ch === '\n') {
      if (line.trim()) lines.push(line.trim());
      line = '';
      used = 0;
      continue;
    }
    const weight = charWeight(ch);
    if (line && used + weight > maxLineWeight) {
      lines.push(line.trim());
      line = ch;
      used = weight;
      continue;
    }
    line += ch;
    used += weight;
  }
  if (line.trim()) lines.push(line.trim());
  if (!maxLines || lines.length <= maxLines) return lines.join('\n');
  const kept = lines.slice(0, maxLines);
  kept[maxLines - 1] = truncateByWeight(kept[maxLines - 1], maxLineWeight);
  return kept.join('\n');
}

function budgetText(value, { maxWeight = 80, maxLineWeight = maxWeight, maxLines = 1 } = {}) {
  return truncateByWeight(wrapByWeight(value, maxLineWeight, maxLines), maxWeight);
}

function cardOverBudget(card, titleWeight, bodyWeight) {
  return textWeight(card.title) > titleWeight || textWeight(card.body) > bodyWeight;
}

function normalizeLayout(layout) {
  const value = String(layout || '').trim();
  return value || 'summary';
}

function cardsFrom(slide) {
  const c = contentOf(slide);
  const raw = c.cards || c.steps || [];
  return Array.isArray(raw) ? raw.map((item) => ({
    title: cleanText(item?.title || item?.label || item?.name),
    body: cleanText(item?.body || item?.description || item?.text || item?.note),
  })).filter((item) => item.title || item.body) : [];
}

function metricsFrom(slide) {
  const raw = contentOf(slide).metrics || [];
  return Array.isArray(raw) ? raw.map((item) => ({
    label: cleanText(item?.label || item?.title),
    value: cleanText(item?.value),
    note: cleanText(item?.note || item?.description),
  })).filter((item) => item.label || item.value || item.note) : [];
}

function eventsFrom(slide) {
  const raw = contentOf(slide).events || [];
  return Array.isArray(raw) ? raw.map((item) => ({
    date: cleanText(item?.date || item?.time || item?.phase),
    title: cleanText(item?.title || item?.label),
    body: cleanText(item?.body || item?.description || item?.text),
  })).filter((item) => item.date || item.title || item.body) : [];
}

function rowsFrom(slide) {
  const c = contentOf(slide);
  const rawRows = c.rows || [];
  return Array.isArray(rawRows)
    ? rawRows
      .map((row) => cleanLines(Array.isArray(row) ? row : Object.values(row || {})))
      .filter((row) => row.length)
    : [];
}

function bodyLines(slide) {
  const c = contentOf(slide);
  return cleanLines([
    c.left,
    c.right,
    c.body,
    c.description,
    c.text,
    c.subtitle,
    ...(Array.isArray(c.points) ? c.points : []),
    ...(Array.isArray(c.next_steps) ? c.next_steps : []),
    ...(Array.isArray(c.outcomes) ? c.outcomes : []),
  ]);
}

function fallbackLines(slide) {
  const lines = bodyLines(slide);
  if (lines.length) return lines;
  const cards = cardsFrom(slide).map((card) => cleanLines([card.title, card.body]).join(': ')).filter(Boolean);
  if (cards.length) return cards;
  const metrics = metricsFrom(slide).map((item) => cleanLines([item.label, item.value, item.note]).join(' · ')).filter(Boolean);
  if (metrics.length) return metrics;
  const events = eventsFrom(slide).map((event) => cleanLines([event.date, event.title, event.body]).join(' · ')).filter(Boolean);
  if (events.length) return events;
  const rows = rowsFrom(slide).map((row) => cleanLines(row).join(' · ')).filter(Boolean);
  if (rows.length) return rows;
  const speakerNotes = cleanText(slide.speaker_notes);
  return speakerNotes ? [speakerNotes] : [];
}

function findAssetPath(deck, slide, assetRoot) {
  const c = contentOf(slide);
  const key = slide.asset_key || slide.assetKey || slide.image || c.asset_key || c.assetKey || c.image;
  if (!key) return '';
  const assets = deck.assets && typeof deck.assets === 'object' ? deck.assets : {};
  const candidate = assets[key] || key;
  const absolute = path.isAbsolute(candidate) ? candidate : path.resolve(assetRoot || process.cwd(), candidate);
  return fs.existsSync(absolute) ? absolute : '';
}

function svgDataUri(file) {
  if (!file || !fs.existsSync(file)) return '';
  const svg = fs.readFileSync(file, 'utf8');
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
}

function addHeroImage(slide, deck, spec, assetRoot, x, y, w, h) {
  const file = findAssetPath(deck, spec, assetRoot);
  if (!file) return false;
  const ext = path.extname(file).toLowerCase();
  if (ext === '.svg') {
    slide.addImage({ data: svgDataUri(file), x, y, w, h });
    return true;
  }
  slide.addImage({ path: file, x, y, w, h });
  return true;
}

function renderCover(pptxSlide, deck, spec, theme, index, total, assetRoot) {
  const c = contentOf(spec);
  pptxSlide.background = { color: theme.coverBg };
  pptxSlide.addShape('rect', { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H, fill: { color: theme.coverBg }, line: { transparency: 100 } });
  addHeroImage(pptxSlide, deck, spec, assetRoot, 7.0, 0.55, 5.55, 3.55);
  addText(pptxSlide, c.kicker || spec.kicker || 'Generated presentation', { x: 0.75, y: 0.85, w: 5.6, h: 0.32, fontSize: 11, color: theme.accent, bold: true, charSpace: 1.1 }, theme);
  addText(pptxSlide, budgetText(spec.title || deck.title, { maxWeight: 52, maxLineWeight: 28, maxLines: 2 }), { x: 0.75, y: 1.55, w: 6.1, h: 1.35, fontSize: 34, bold: true, color: theme.coverTitle, valign: 'top' }, theme);
  addText(pptxSlide, budgetText(c.subtitle || deck.subtitle, { maxWeight: 72, maxLineWeight: 36, maxLines: 2 }), { x: 0.78, y: 3.06, w: 5.9, h: 0.75, fontSize: 15, color: theme.coverSubtitle, valign: 'top' }, theme);
  pptxSlide.addShape('line', { x: 0.78, y: 4.22, w: 3.8, h: 0, line: { color: theme.accent, width: 2 } });
  addText(pptxSlide, c.owner || c.audience || '', { x: 0.78, y: 4.52, w: 4.8, h: 0.55, fontSize: 11, color: theme.coverMuted }, theme);
  addFooter(pptxSlide, index, total, theme, theme.coverMuted);
}

function renderCards(slide, spec, theme, index, total) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const cards = cardsFrom(spec);
  if (cards.length > 4 || cards.some((card) => cardOverBudget(card, 28, 84))) {
    renderCardsAsTable(slide, cards, theme);
    addFooter(slide, index, total, theme);
    return;
  }
  const positions = cards.length <= 2
    ? [[1.2, 1.65], [7.0, 1.65]]
    : [[0.75, 1.45], [4.65, 1.45], [8.55, 1.45], [0.75, 3.85]];
  const surfaceText = readableOn(theme.surface, theme.text);
  cards.slice(0, 4).forEach((card, i) => {
    const [x, y] = positions[i];
    const w = cards.length <= 2 ? 5.15 : 3.35;
    slide.addShape('roundRect', { x, y, w, h: 1.85, rectRadius: 0.06, fill: { color: theme.surface }, line: { color: i % 2 ? theme.accent2 : theme.accent, transparency: 10 } });
    addText(slide, budgetText(card.title, { maxWeight: 28, maxLineWeight: 18, maxLines: 2 }), { x: x + 0.18, y: y + 0.18, w: w - 0.36, h: 0.56, fontSize: 12.2, bold: true, color: i % 2 ? theme.accent2 : theme.accent, valign: 'top' }, theme);
    addText(slide, budgetText(card.body, { maxWeight: 84, maxLineWeight: cards.length <= 2 ? 44 : 24, maxLines: 3 }), { x: x + 0.18, y: y + 0.86, w: w - 0.36, h: 0.76, fontSize: 9.8, color: surfaceText, valign: 'top' }, theme);
  });
  addFooter(slide, index, total, theme);
}

function renderCardsAsTable(slide, cards, theme) {
  const rows = cards.slice(0, 6).map((card) => [
    budgetText(card.title, { maxWeight: 26, maxLineWeight: 18, maxLines: 2 }),
    budgetText(card.body, { maxWeight: 92, maxLineWeight: 56, maxLines: 2 }),
  ]);
  renderSimpleTable(slide, ['Item', 'Detail'], rows, theme, { y: 1.35, firstColW: 3.25 });
}

function renderMetrics(slide, deck, spec, theme, index, total, assetRoot) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  addHeroImage(slide, deck, spec, assetRoot, 8.2, 1.1, 3.8, 2.55);
  metricsFrom(spec).slice(0, 4).forEach((item, i) => {
    const x = 0.8 + i * 2.05;
    slide.addShape('roundRect', { x, y: 1.55, w: 1.7, h: 1.48, rectRadius: 0.05, fill: { color: theme.surface }, line: { color: 'E2E8F0' } });
    addText(slide, item.value, { x: x + 0.12, y: 1.75, w: 1.46, h: 0.42, fontSize: 24, bold: true, color: theme.accent, align: 'center' }, theme);
    addText(slide, item.label, { x: x + 0.1, y: 2.24, w: 1.5, h: 0.26, fontSize: 9.5, bold: true, color: theme.text, align: 'center' }, theme);
    addText(slide, item.note, { x: x + 0.1, y: 2.54, w: 1.5, h: 0.28, fontSize: 8.5, color: theme.muted, align: 'center' }, theme);
  });
  renderTextPanel(slide, '说明', fallbackLines(spec).join('\n') || spec.subtitle || '', theme, 0.85, 4.45, 11.6, 1.15);
  addFooter(slide, index, total, theme);
}

function renderFlow(slide, spec, theme, index, total) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const steps = cardsFrom(spec);
  if (steps.length > 4 || steps.some((step) => cardOverBudget(step, 24, 52))) {
    renderFlowAsTable(slide, steps, theme);
    addFooter(slide, index, total, theme);
    return;
  }
  const count = Math.max(1, Math.min(steps.length, 5));
  const gap = 0.23;
  const w = Math.min(2.15, (11.7 - (count - 1) * gap) / count);
  steps.slice(0, count).forEach((step, i) => {
    const x = 0.78 + i * (w + gap);
    const active = i === Number(spec.visual?.highlightIndex ?? -1);
    const fill = active ? theme.accent : theme.surface;
    const color = active ? 'FFFFFF' : theme.text;
    slide.addShape('roundRect', { x, y: 2.0, w, h: 1.45, rectRadius: 0.06, fill: { color: fill }, line: { color: active ? theme.accent : 'CBD5E1' } });
    addText(slide, budgetText(step.title, { maxWeight: 24, maxLineWeight: 14, maxLines: 2 }), { x: x + 0.1, y: 2.2, w: w - 0.2, h: 0.48, fontSize: 10.4, bold: true, color, align: 'center', valign: 'top' }, theme);
    addText(slide, budgetText(step.body, { maxWeight: 52, maxLineWeight: 18, maxLines: 2 }), { x: x + 0.12, y: 2.82, w: w - 0.24, h: 0.42, fontSize: 8.3, color, align: 'center', valign: 'top' }, theme);
    if (i < count - 1) addText(slide, '->', { x: x + w + 0.02, y: 2.5, w: gap + 0.12, h: 0.3, fontSize: 13, color: theme.accent, align: 'center' }, theme);
  });
  renderTextPanel(slide, 'Key notes', fallbackLines(spec).slice(0, 3).join('\n'), theme, 0.85, 4.35, 11.6, 1.1);
  addFooter(slide, index, total, theme);
}

function renderFlowAsTable(slide, steps, theme) {
  const rows = steps.slice(0, 6).map((step, i) => [
    `${i + 1}. ${budgetText(step.title || `Step ${i + 1}`, { maxWeight: 28, maxLineWeight: 20, maxLines: 2 })}`,
    budgetText(step.body, { maxWeight: 88, maxLineWeight: 54, maxLines: 2 }),
  ]);
  renderSimpleTable(slide, ['Step', 'Focus'], rows, theme, { y: 1.35, firstColW: 3.55 });
}

function renderTable(slide, spec, theme, index, total) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const columns = contentOf(spec).columns || [];
  const rows = rowsFrom(spec);
  renderSimpleTable(slide, columns, rows, theme, { y: 1.35 });
  addFooter(slide, index, total, theme);
}

function renderSimpleTable(slide, columns, rows, theme, options = {}) {
  const allRows = [columns, ...rows].filter((row) => Array.isArray(row) && row.length).slice(0, 7);
  if (!allRows.length) return;
  const colCount = Math.max(1, Math.min(allRows[0].length || 2, 4));
  const startX = 0.85;
  const totalW = 11.4;
  const firstColW = Math.min(options.firstColW || (totalW / colCount), totalW - 2.0);
  const otherW = colCount > 1 ? (totalW - firstColW) / (colCount - 1) : totalW;
  let y = options.y || 1.35;
  allRows.forEach((row, r) => {
    const fill = r === 0 ? theme.accent : theme.surface;
    const textColor = r === 0 ? readableOn(theme.accent, 'FFFFFF') : theme.text;
    let x = startX;
    for (let c = 0; c < colCount; c += 1) {
      const w = c === 0 && colCount > 1 ? firstColW : otherW;
      const maxLineWeight = c === 0 ? 26 : Math.max(32, Math.floor(w * 15));
      slide.addShape('roundRect', { x, y, w: w - 0.05, h: 0.68, rectRadius: 0.03, fill: { color: fill }, line: { color: 'CBD5E1' } });
      addText(slide, budgetText(row[c] || '', { maxWeight: maxLineWeight * 2, maxLineWeight, maxLines: 2 }), { x: x + 0.08, y: y + 0.1, w: w - 0.22, h: 0.44, fontSize: r === 0 ? 10.3 : 8.8, bold: r === 0, color: textColor, valign: 'top' }, theme);
      x += w;
    }
    y += 0.76;
  });
}

function renderTimeline(slide, spec, theme, index, total) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const events = eventsFrom(spec).slice(0, 5);
  events.forEach((event, i) => {
    const x = 0.8 + i * 2.35;
    slide.addShape('ellipse', { x, y: 2.0, w: 0.5, h: 0.5, fill: { color: i % 2 ? theme.accent2 : theme.accent }, line: { transparency: 100 } });
    if (i < events.length - 1) slide.addShape('line', { x: x + 0.5, y: 2.25, w: 1.85, h: 0, line: { color: 'CBD5E1', width: 2 } });
    addText(slide, event.date, { x: x - 0.1, y: 1.55, w: 1.2, h: 0.25, fontSize: 9, color: theme.muted, bold: true }, theme);
    addText(slide, budgetText(event.title, { maxWeight: 24, maxLineWeight: 16, maxLines: 2 }), { x: x - 0.05, y: 2.75, w: 2.05, h: 0.44, fontSize: 10.4, color: theme.text, bold: true, valign: 'top' }, theme);
    addText(slide, budgetText(event.body, { maxWeight: 58, maxLineWeight: 18, maxLines: 3 }), { x: x - 0.05, y: 3.26, w: 2.05, h: 0.72, fontSize: 8.5, color: theme.text, valign: 'top' }, theme);
  });
  addFooter(slide, index, total, theme);
}

function renderTextPanel(slide, title, body, theme, x, y, w, h) {
  if (!body && !title) return;
  slide.addShape('roundRect', { x, y, w, h, rectRadius: 0.05, fill: { color: theme.surface }, line: { color: 'CBD5E1' } });
  addText(slide, title, { x: x + 0.18, y: y + 0.12, w: w - 0.36, h: 0.25, fontSize: 11, bold: true, color: theme.accent }, theme);
  addText(slide, budgetText(body, { maxWeight: Math.floor(w * 34), maxLineWeight: Math.floor(w * 18), maxLines: Math.max(1, Math.floor((h - 0.52) / 0.22)) }), { x: x + 0.18, y: y + 0.42, w: w - 0.36, h: h - 0.52, fontSize: 9.5, color: theme.text, valign: 'top' }, theme);
}

function renderSummary(slide, spec, theme, index, total) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const lines = fallbackLines(spec);
  const left = lines.slice(0, Math.ceil(lines.length / 2));
  const right = lines.slice(Math.ceil(lines.length / 2));
  renderTextPanel(slide, '下一步动作', left.map((line) => `• ${line}`).join('\n'), theme, 0.85, 1.45, 5.55, 4.6);
  renderTextPanel(slide, '预期结果', right.map((line) => `• ${line}`).join('\n'), theme, 6.95, 1.45, 5.55, 4.6);
  addFooter(slide, index, total, theme);
}

function renderSplit(slide, deck, spec, theme, index, total, assetRoot) {
  slide.background = { color: theme.background };
  addTitle(slide, spec.title, theme);
  const hasImage = addHeroImage(slide, deck, spec, assetRoot, 7.25, 1.25, 4.75, 3.1);
  const lines = fallbackLines(spec);
  lines.slice(0, 6).forEach((line, i) => {
    const y = 1.35 + i * 0.72;
    slide.addShape('ellipse', { x: 0.8, y: y + 0.04, w: 0.26, h: 0.26, fill: { color: i % 2 ? theme.accent2 : theme.accent }, line: { transparency: 100 } });
    addText(slide, budgetText(line, { maxWeight: hasImage ? 46 : 88, maxLineWeight: hasImage ? 46 : 88, maxLines: 1 }), { x: 1.25, y, w: hasImage ? 5.4 : 10.9, h: 0.48, fontSize: 13.2, color: theme.text }, theme);
  });
  if (!lines.length) renderTextPanel(slide, '', contentOf(spec).subtitle || spec.subtitle || '', theme, 0.85, 1.45, hasImage ? 5.4 : 11.4, 4.3);
  addFooter(slide, index, total, theme);
}

function renderDeck(deck, output, assetRoot) {
  const pptx = new pptxgen();
  const theme = themeOf(deck);
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = deck.author || 'im-office-agent';
  pptx.company = 'im-office-agent';
  pptx.subject = deck.subtitle || '';
  pptx.title = deck.title || 'Generated Presentation';
  pptx.theme = { headFontFace: theme.fontFace, bodyFontFace: theme.fontFace, lang: 'zh-CN' };
  const slides = Array.isArray(deck.slides) ? deck.slides.slice(0, 12) : [];
  slides.forEach((spec, i) => {
    const slide = pptx.addSlide();
    const layout = normalizeLayout(spec.layout);
    if (layout === 'cover') renderCover(slide, deck, spec, theme, i + 1, slides.length, assetRoot);
    else if (layout === 'metrics') renderMetrics(slide, deck, spec, theme, i + 1, slides.length, assetRoot);
    else if (layout === 'cards') renderCards(slide, spec, theme, i + 1, slides.length);
    else if (layout === 'flow') renderFlow(slide, spec, theme, i + 1, slides.length);
    else if (layout === 'timeline') renderTimeline(slide, spec, theme, i + 1, slides.length);
    else if (layout === 'table') renderTable(slide, spec, theme, i + 1, slides.length);
    else if (layout === 'split') renderSplit(slide, deck, spec, theme, i + 1, slides.length, assetRoot);
    else renderSummary(slide, spec, theme, i + 1, slides.length);
  });
  return pptx.writeFile({ fileName: output });
}

const input = argValue('--input');
const output = argValue('--output');
const assetRoot = argValue('--asset-root', path.resolve(__dirname, '..', '..'));
if (!input || !output) {
  console.error(JSON.stringify({ ok: false, error: { code: 'ARGUMENTS_MISSING', message: '--input and --output are required' } }));
  process.exit(2);
}

try {
  const deck = JSON.parse(await fsp.readFile(input, 'utf8'));
  await fsp.mkdir(path.dirname(output), { recursive: true });
  await renderDeck(deck, output, assetRoot);
  const stat = await fsp.stat(output);
  console.log(JSON.stringify({ ok: true, output, bytes: stat.size, slide_count: Array.isArray(deck.slides) ? deck.slides.length : 0 }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: { code: 'PPTX_RENDER_FAILED', message: String(error?.message || error) } }, null, 2));
  process.exit(1);
}
