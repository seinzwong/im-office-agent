import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');
const DEFAULT_IR = path.join(ROOT, 'data', 'ir.json');
const DEFAULT_OUT = path.join(ROOT, 'out', 'default');

const SUPPORTED_KINDS = new Set(['cover', 'split', 'flow', 'metrics', 'cards', 'table', 'timeline', 'image']);

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i >= 0 && process.argv[i + 1]) return process.argv[i + 1];
  return fallback;
}

function stripHash(hex = '') {
  return String(hex).replace('#', '').toUpperCase();
}

function xmlEscape(s = '') {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function mdEscape(s = '') {
  return String(s).replaceAll('|', '\\|');
}

function toPosix(p) {
  return p.split(path.sep).join('/');
}

function relToRoot(p) {
  return toPosix(path.relative(ROOT, p));
}

function themeOf(ir) {
  return {
    name: ir.theme?.name || 'Default',
    accent: ir.theme?.accent || '#2F6BFF',
    accent2: ir.theme?.accent2 || '#7C3AED',
    background: ir.theme?.background || '#F8FAFC',
    surface: ir.theme?.surface || '#FFFFFF',
    text: ir.theme?.text || '#0F172A',
    muted: ir.theme?.muted || '#64748B',
    success: ir.theme?.success || '#16A34A',
    warning: ir.theme?.warning || '#F97316',
    fontFace: ir.theme?.fontFace || 'Aptos'
  };
}

async function loadIR(irPath) {
  const absolute = path.resolve(ROOT, irPath);
  const raw = await fs.readFile(absolute, 'utf8');
  return JSON.parse(raw);
}

async function write(outDir, name, content) {
  await fs.mkdir(outDir, { recursive: true });
  const file = path.join(outDir, name);
  await fs.writeFile(file, content, 'utf8');
  console.log(`wrote ${relToRoot(file)}`);
}

async function copyAsset(outDir, ir, assetKey) {
  const assetRel = ir.assets?.[assetKey];
  if (!assetRel) return null;
  const from = path.resolve(ROOT, assetRel);
  if (!fsSync.existsSync(from)) return null;
  const destDir = path.join(outDir, 'assets');
  await fs.mkdir(destDir, { recursive: true });
  const dest = path.join(destDir, path.basename(from));
  await fs.copyFile(from, dest);
  return dest;
}

function assetPath(ir, assetKey) {
  const assetRel = ir.assets?.[assetKey];
  if (!assetRel) return null;
  const absolute = path.resolve(ROOT, assetRel);
  return fsSync.existsSync(absolute) ? absolute : null;
}

function svgDataUri(svgFile) {
  if (!svgFile) return null;
  const svg = fsSync.readFileSync(svgFile, 'utf8');
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
}

function validateIR(ir) {
  const errors = [];
  if (ir.schemaVersion !== '0.2.0') errors.push('schemaVersion must be 0.2.0');
  if (!ir.meta?.title) errors.push('meta.title is required');
  if (!Array.isArray(ir.blocks)) errors.push('blocks must be an array');

  const ids = new Set();
  for (const [idx, block] of (ir.blocks || []).entries()) {
    if (!block.id) errors.push(`blocks[${idx}].id is required`);
    if (ids.has(block.id)) errors.push(`duplicate block id: ${block.id}`);
    ids.add(block.id);
    if (!SUPPORTED_KINDS.has(block.kind)) errors.push(`unsupported block kind: ${block.kind}`);

    if (block.kind === 'flow') {
      const nodeIds = new Set((block.nodes || []).map(n => n.id));
      for (const [from, to] of block.edges || []) {
        if (!nodeIds.has(from)) errors.push(`flow ${block.id}: edge from missing node ${from}`);
        if (!nodeIds.has(to)) errors.push(`flow ${block.id}: edge to missing node ${to}`);
      }
    }
    if (block.kind === 'table') {
      if (!Array.isArray(block.columns) || !Array.isArray(block.rows)) errors.push(`table ${block.id}: columns and rows are required`);
    }
    if (block.kind === 'metrics' && !Array.isArray(block.items)) errors.push(`metrics ${block.id}: items are required`);
    if (block.kind === 'cards' && !Array.isArray(block.cards)) errors.push(`cards ${block.id}: cards are required`);
  }
  return errors;
}

function renderMermaid(flowBlock) {
  const lines = ['flowchart LR'];
  for (const node of flowBlock.nodes || []) lines.push(`  ${node.id}["${node.label}"]`);
  for (const [from, to] of flowBlock.edges || []) lines.push(`  ${from} --> ${to}`);
  return lines.join('\n');
}

function blockToMarkdown(block, ir) {
  const lines = [`## ${block.title}`, ''];
  if (block.kind === 'cover') {
    lines.push(`> ${block.subtitle || ir.meta.subtitle}`);
    if (block.image) lines.push('', `![${block.title}](assets/${path.basename(ir.assets?.[block.image] || '')})`);
  }
  if (block.kind === 'split') {
    if (block.image) lines.push(`![${block.title}](assets/${path.basename(ir.assets?.[block.image] || '')})`, '');
    for (const point of block.points || []) lines.push(`- ${point}`);
  }
  if (block.kind === 'flow') {
    if (block.caption) lines.push(block.caption, '');
    lines.push('```mermaid', renderMermaid(block), '```');
  }
  if (block.kind === 'metrics') {
    if (block.image) lines.push(`![${block.title}](assets/${path.basename(ir.assets?.[block.image] || '')})`, '');
    lines.push('| 指标 | 数值 | 说明 |');
    lines.push('| --- | --- | --- |');
    for (const m of block.items || []) lines.push(`| ${mdEscape(m.label)} | ${mdEscape(m.value)} | ${mdEscape(m.note)} |`);
  }
  if (block.kind === 'cards') {
    for (const card of block.cards || []) {
      lines.push(`### ${card.title}`);
      lines.push(card.body || '');
      lines.push('');
    }
  }
  if (block.kind === 'table') {
    lines.push(`| ${block.columns.map(mdEscape).join(' | ')} |`);
    lines.push(`| ${block.columns.map(() => '---').join(' | ')} |`);
    for (const row of block.rows || []) lines.push(`| ${row.map(mdEscape).join(' | ')} |`);
  }
  if (block.kind === 'timeline') {
    for (const event of block.events || []) {
      lines.push(`- **${event.date} · ${event.title}**：${event.body}`);
    }
  }
  if (block.kind === 'image') {
    if (block.image) lines.push(`![${block.title}](assets/${path.basename(ir.assets?.[block.image] || '')})`);
    if (block.caption) lines.push('', block.caption);
  }
  lines.push('');
  return lines.join('\n');
}

async function renderDocMarkdown(ir, outDir) {
  const usedAssetKeys = new Set(ir.blocks.filter(b => b.image).map(b => b.image));
  for (const key of usedAssetKeys) await copyAsset(outDir, ir, key);
  return [
    `<title>${ir.meta.title}</title>`,
    '',
    `# ${ir.meta.title}`,
    '',
    `> ${ir.meta.subtitle}`,
    '',
    `- Owner: ${ir.meta.owner}`,
    `- Date: ${ir.meta.date}`,
    `- Audience: ${ir.meta.audience || 'N/A'}`,
    '',
    ...ir.blocks.map(block => blockToMarkdown(block, ir))
  ].join('\n');
}

function summarizeBlock(block) {
  if (block.kind === 'cover') return block.subtitle || '';
  if (block.kind === 'split') return (block.points || []).map(x => `• ${x}`).join('\n');
  if (block.kind === 'flow') return (block.nodes || []).map(x => `• ${x.label}`).join('\n');
  if (block.kind === 'metrics') return (block.items || []).map(x => `${x.label}: ${x.value} (${x.note})`).join('\n');
  if (block.kind === 'cards') return (block.cards || []).map(x => `• ${x.title}: ${x.body}`).join('\n');
  if (block.kind === 'table') return (block.rows || []).map(row => `${row[0]}：${row[1]}`).join('\n');
  if (block.kind === 'timeline') return (block.events || []).map(e => `${e.date} · ${e.title}: ${e.body}`).join('\n');
  return block.caption || '';
}

function renderWhiteboardDSL(ir) {
  const t = themeOf(ir);
  const root = {
    version: 2,
    metadata: { title: ir.meta.title, generatedFrom: 'Industrial IR Demo' },
    nodes: [
      {
        id: 'root',
        type: 'frame',
        x: 0,
        y: 0,
        width: 1440,
        height: 'fit-content',
        layout: 'vertical',
        gap: 18,
        padding: 32,
        fillColor: t.background,
        borderColor: '#CBD5E1',
        borderWidth: 1,
        borderRadius: 24,
        children: [
          {
            id: 'title',
            type: 'text',
            width: 'fill-container',
            height: 'fit-content',
            text: ir.meta.title,
            fontSize: 34,
            textColor: t.text,
            textAlign: 'left'
          },
          {
            id: 'subtitle',
            type: 'text',
            width: 'fill-container',
            height: 'fit-content',
            text: ir.meta.subtitle,
            fontSize: 17,
            textColor: t.muted,
            textAlign: 'left'
          },
          ...ir.blocks.map((block, i) => ({
            id: `block_${block.id}`,
            type: 'frame',
            width: 'fill-container',
            height: 'fit-content',
            layout: 'vertical',
            gap: 8,
            padding: 20,
            fillColor: i % 2 === 0 ? t.surface : '#EEF4FF',
            borderColor: i === 0 ? t.accent : '#D0D7E2',
            borderWidth: 1,
            borderRadius: 16,
            children: [
              { type: 'text', width: 'fill-container', height: 'fit-content', text: block.title, fontSize: 21, textColor: t.accent, textAlign: 'left' },
              { type: 'text', width: 'fill-container', height: 'fit-content', text: summarizeBlock(block), fontSize: 14, textColor: t.text, textAlign: 'left' }
            ]
          }))
        ]
      }
    ]
  };
  return JSON.stringify(root, null, 2);
}

function renderWhiteboardMermaid(ir) {
  const flow = ir.blocks.find(b => b.kind === 'flow');
  return flow ? renderMermaid(flow) : 'flowchart LR\n  no_flow["No flow block"]';
}

function shapeText(x, y, w, h, text, opts = {}) {
  const textType = opts.textType || 'body';
  const fontSize = opts.fontSize || 16;
  const color = opts.color || 'rgb(30,41,59)';
  return `<shape type="text" topLeftX="${x}" topLeftY="${y}" width="${w}" height="${h}"><content textType="${textType}"><p><span style="font-size:${fontSize}px;color:${color}">${xmlEscape(text)}</span></p></content></shape>`;
}

function rect(x, y, w, h, fill, border = 'rgb(203,213,225)') {
  return `<shape type="rect" topLeftX="${x}" topLeftY="${y}" width="${w}" height="${h}"><style><fill><fillColor color="${fill}"/></fill><line><lineFill color="${border}"/></line></style><content><p></p></content></shape>`;
}

function renderSlide(style, data) {
  return `<slide xmlns="http://www.larkoffice.com/sml/2.0"><style><fill><fillColor color="${style}"/></fill></style><data>${data.join('')}</data></slide>`;
}

function renderSlidesXML(ir) {
  const t = themeOf(ir);
  const slides = [];
  for (const block of ir.blocks) {
    const shapes = [shapeText(48, 36, 860, 50, block.title, { textType: 'title', fontSize: 30, color: `rgb(${parseInt(stripHash(t.text).slice(0,2),16)},${parseInt(stripHash(t.text).slice(2,4),16)},${parseInt(stripHash(t.text).slice(4,6),16)})` })];
    if (block.kind === 'cover') {
      shapes.push(shapeText(60, 130, 760, 90, block.subtitle || ir.meta.subtitle, { fontSize: 20, color: 'rgb(255,255,255)' }));
      shapes.push(shapeText(60, 440, 760, 40, `${ir.meta.owner} · ${ir.meta.date}`, { fontSize: 14, color: 'rgb(226,232,240)' }));
      slides.push(renderSlide(`linear-gradient(135deg,rgba(15,23,42,1) 0%,rgba(47,107,255,1) 100%)`, shapes));
      continue;
    }
    const summary = summarizeBlock(block).slice(0, 720);
    shapes.push(rect(64, 120, 850, 360, 'rgb(255,255,255)'));
    shapes.push(shapeText(88, 150, 790, 310, summary, { fontSize: 16, color: 'rgb(51,65,85)' }));
    slides.push(renderSlide('rgb(248,250,252)', shapes));
  }
  return JSON.stringify(slides, null, 2);
}

function addText(slide, text, opts, theme) {
  slide.addText(text || '', {
    fontFace: theme.fontFace,
    margin: 0.08,
    breakLine: false,
    ...opts
  });
}

function addTitle(slide, text, theme, y = 0.35) {
  addText(slide, text, { x: 0.55, y, w: 12.0, h: 0.45, fontSize: 25, bold: true, color: stripHash(theme.text) }, theme);
}

function addFooter(slide, ir, theme) {
  addText(slide, `${ir.meta.owner} · ${ir.meta.date} · ${theme.name}`, { x: 0.55, y: 7.12, w: 7.5, h: 0.22, fontSize: 8.5, color: stripHash(theme.muted) }, theme);
}

function addHeroImage(slide, ir, key, x, y, w, h) {
  const p = assetPath(ir, key);
  const data = p ? svgDataUri(p) : null;
  if (data) slide.addImage({ data, x, y, w, h });
}

function addCard(slide, x, y, w, h, title, body, theme, accent = true) {
  slide.addShape('roundRect', { x, y, w, h, rectRadius: 0.08, fill: { color: stripHash(theme.surface) }, line: { color: accent ? stripHash(theme.accent) : 'CBD5E1', transparency: accent ? 15 : 0 } });
  addText(slide, title, { x: x + 0.18, y: y + 0.16, w: w - 0.36, h: 0.32, fontSize: 13.5, bold: true, color: stripHash(accent ? theme.accent : theme.text) }, theme);
  addText(slide, body, { x: x + 0.18, y: y + 0.55, w: w - 0.36, h: h - 0.68, fontSize: 10.5, color: stripHash(theme.text), valign: 'top', fit: 'shrink' }, theme);
}

function renderCoverPptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.text) };
  slide.addShape('rect', { x: 0, y: 0, w: 13.333, h: 7.5, fill: { color: stripHash(theme.text) }, line: { transparency: 100 } });
  addHeroImage(slide, ir, block.image, 6.85, 0.55, 5.85, 3.85);
  addText(slide, block.kicker || 'Generated from IR', { x: 0.75, y: 0.9, w: 5.4, h: 0.3, fontSize: 11, color: stripHash(theme.accent), bold: true, charSpace: 1.2 }, theme);
  addText(slide, block.title || ir.meta.title, { x: 0.75, y: 1.55, w: 5.9, h: 1.35, fontSize: 34, bold: true, color: 'FFFFFF', fit: 'shrink' }, theme);
  addText(slide, block.subtitle || ir.meta.subtitle, { x: 0.78, y: 3.05, w: 5.65, h: 0.75, fontSize: 15, color: 'E2E8F0', fit: 'shrink' }, theme);
  slide.addShape('line', { x: 0.78, y: 4.25, w: 3.8, h: 0, line: { color: stripHash(theme.accent), width: 2 } });
  addText(slide, `${ir.meta.owner}\n${ir.meta.date}`, { x: 0.78, y: 4.55, w: 4.5, h: 0.55, fontSize: 11, color: 'CBD5E1' }, theme);
}

function renderSplitPptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  addHeroImage(slide, ir, block.image, 7.15, 1.2, 5.2, 3.35);
  (block.points || []).forEach((point, i) => {
    const y = 1.2 + i * 1.15;
    slide.addShape('ellipse', { x: 0.78, y: y + 0.04, w: 0.34, h: 0.34, fill: { color: stripHash(theme.accent) }, line: { transparency: 100 } });
    addText(slide, point, { x: 1.28, y, w: 5.5, h: 0.55, fontSize: 15, color: stripHash(theme.text), fit: 'shrink' }, theme);
  });
  addFooter(slide, ir, theme);
}

function renderFlowPptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  if (block.caption) addText(slide, block.caption, { x: 0.58, y: 0.92, w: 11.4, h: 0.35, fontSize: 11.5, color: stripHash(theme.muted) }, theme);
  const nodes = block.nodes || [];
  const gap = 0.28;
  const w = Math.min(1.75, (11.8 - (nodes.length - 1) * gap) / Math.max(nodes.length, 1));
  const startX = 0.72;
  const y = 2.15;
  nodes.forEach((node, i) => {
    const x = startX + i * (w + gap);
    slide.addShape('roundRect', { x, y, w, h: 1.05, rectRadius: 0.08, fill: { color: i === Math.floor(nodes.length / 2) ? stripHash(theme.accent) : stripHash(theme.surface) }, line: { color: 'CBD5E1' } });
    addText(slide, node.label, { x: x + 0.08, y: y + 0.24, w: w - 0.16, h: 0.48, fontSize: 9.8, bold: i === Math.floor(nodes.length / 2), color: i === Math.floor(nodes.length / 2) ? 'FFFFFF' : stripHash(theme.text), align: 'center', valign: 'mid', fit: 'shrink' }, theme);
    if (i < nodes.length - 1) addText(slide, '→', { x: x + w + 0.02, y: y + 0.35, w: gap + 0.2, h: 0.3, fontSize: 17, color: stripHash(theme.accent), align: 'center' }, theme);
  });
  addCard(slide, 0.85, 4.25, 11.6, 1.15, 'IR 原则', 'Agent 只产出结构化 patch；校验通过后，所有端侧产物由 Adapter 重新生成，避免多端漂移。', theme);
  addFooter(slide, ir, theme);
}

function renderMetricsPptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  addHeroImage(slide, ir, block.image, 7.3, 1.15, 4.9, 3.05);
  (block.items || []).forEach((item, i) => {
    const x = 0.8 + i * 2.0;
    slide.addShape('roundRect', { x, y: 1.45, w: 1.65, h: 1.45, rectRadius: 0.08, fill: { color: stripHash(theme.surface) }, line: { color: 'E2E8F0' } });
    addText(slide, item.value, { x: x + 0.15, y: 1.65, w: 1.35, h: 0.42, fontSize: 24, bold: true, color: stripHash(theme.accent), align: 'center' }, theme);
    addText(slide, item.label, { x: x + 0.1, y: 2.14, w: 1.45, h: 0.25, fontSize: 9.8, bold: true, color: stripHash(theme.text), align: 'center' }, theme);
    addText(slide, item.note || '', { x: x + 0.1, y: 2.43, w: 1.45, h: 0.25, fontSize: 8.5, color: stripHash(theme.muted), align: 'center' }, theme);
  });
  addCard(slide, 0.85, 4.55, 11.35, 1.1, '为什么这些指标可信？', '它们不是手工散落在不同文档中的数字，而是来自 IR 的 metrics block。文档、画板和 PPT 都引用同一组结构化数据。', theme, false);
  addFooter(slide, ir, theme);
}

function renderCardsPptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  const cards = block.cards || [];
  const cols = cards.length <= 4 ? 2 : 3;
  const cardW = cols === 2 ? 5.55 : 3.55;
  const cardH = 1.18;
  cards.forEach((card, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const x = 0.75 + col * (cardW + 0.35);
    const y = 1.25 + row * (cardH + 0.35);
    addCard(slide, x, y, cardW, cardH, card.title, card.body, theme, i % 2 === 0);
  });
  addFooter(slide, ir, theme);
}

function renderTablePptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  const rows = block.rows || [];
  rows.slice(0, 5).forEach((row, i) => {
    const y = 1.15 + i * 1.03;
    slide.addShape('roundRect', { x: 0.75, y, w: 4.2, h: 0.78, rectRadius: 0.06, fill: { color: 'EEF4FF' }, line: { color: 'CBD5E1' } });
    slide.addShape('roundRect', { x: 5.12, y, w: 7.45, h: 0.78, rectRadius: 0.06, fill: { color: stripHash(theme.surface) }, line: { color: 'CBD5E1' } });
    addText(slide, row[0], { x: 0.94, y: y + 0.16, w: 3.8, h: 0.36, fontSize: 11.2, bold: true, color: stripHash(theme.accent), fit: 'shrink' }, theme);
    addText(slide, row[1], { x: 5.3, y: y + 0.13, w: 7.0, h: 0.42, fontSize: 10.4, color: stripHash(theme.text), fit: 'shrink' }, theme);
  });
  addFooter(slide, ir, theme);
}

function renderTimelinePptx(pptx, ir, block, theme) {
  const slide = pptx.addSlide();
  slide.background = { color: stripHash(theme.background) };
  addTitle(slide, block.title, theme);
  const events = block.events || [];
  events.forEach((event, i) => {
    const x = 0.9 + i * 3.0;
    slide.addShape('ellipse', { x, y: 2.0, w: 0.5, h: 0.5, fill: { color: stripHash(theme.accent) }, line: { transparency: 100 } });
    if (i < events.length - 1) slide.addShape('line', { x: x + 0.5, y: 2.25, w: 2.52, h: 0, line: { color: 'CBD5E1', width: 2 } });
    addText(slide, event.date, { x: x - 0.1, y: 1.55, w: 1.0, h: 0.25, fontSize: 9.8, color: stripHash(theme.muted), bold: true }, theme);
    addText(slide, event.title, { x: x - 0.1, y: 2.75, w: 2.5, h: 0.3, fontSize: 13, color: stripHash(theme.text), bold: true }, theme);
    addText(slide, event.body, { x: x - 0.1, y: 3.15, w: 2.5, h: 0.85, fontSize: 9.8, color: stripHash(theme.text), fit: 'shrink' }, theme);
  });
  addFooter(slide, ir, theme);
}

async function renderPptx(ir, outDir) {
  const { default: pptxgen } = await import('pptxgenjs');
  const pptx = new pptxgen();
  const theme = themeOf(ir);
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = ir.meta.owner;
  pptx.company = 'IR Industrial Adapter Demo';
  pptx.subject = ir.meta.subtitle;
  pptx.title = ir.meta.title;
  pptx.theme = {
    headFontFace: theme.fontFace,
    bodyFontFace: theme.fontFace,
    lang: 'zh-CN'
  };
  for (const block of ir.blocks) {
    if (block.kind === 'cover') renderCoverPptx(pptx, ir, block, theme);
    else if (block.kind === 'split' || block.kind === 'image') renderSplitPptx(pptx, ir, block, theme);
    else if (block.kind === 'flow') renderFlowPptx(pptx, ir, block, theme);
    else if (block.kind === 'metrics') renderMetricsPptx(pptx, ir, block, theme);
    else if (block.kind === 'cards') renderCardsPptx(pptx, ir, block, theme);
    else if (block.kind === 'table') renderTablePptx(pptx, ir, block, theme);
    else if (block.kind === 'timeline') renderTimelinePptx(pptx, ir, block, theme);
  }
  await fs.mkdir(outDir, { recursive: true });
  const file = path.join(outDir, 'deck.pptx');
  await pptx.writeFile({ fileName: file });
  console.log(`wrote ${relToRoot(file)}`);
}

async function build(ir, outDir) {
  const errors = validateIR(ir);
  if (errors.length) {
    console.error(errors.map(e => `- ${e}`).join('\n'));
    process.exit(1);
  }
  await write(outDir, 'doc.md', await renderDocMarkdown(ir, outDir));
  await write(outDir, 'whiteboard.mmd', renderWhiteboardMermaid(ir));
  await write(outDir, 'whiteboard.dsl.json', renderWhiteboardDSL(ir));
  await write(outDir, 'slides.json', renderSlidesXML(ir));
  await write(outDir, 'ir.normalized.json', JSON.stringify(ir, null, 2));
  await write(outDir, 'manifest.json', JSON.stringify({
    generatedAt: new Date().toISOString(),
    source: 'data/ir.json or --ir path',
    outputs: ['doc.md', 'whiteboard.mmd', 'whiteboard.dsl.json', 'slides.json', 'deck.pptx'],
    adapterContract: 'Agent may change IR within schemaVersion 0.2.0 without editing adapter code.'
  }, null, 2));
}

const cmd = process.argv[2] || 'all';
const irPath = arg('ir', DEFAULT_IR);
const outDir = path.resolve(ROOT, arg('out', DEFAULT_OUT));
const ir = await loadIR(irPath);

if (cmd === 'validate') {
  const errors = validateIR(ir);
  if (errors.length) {
    console.error(errors.map(e => `- ${e}`).join('\n'));
    process.exit(1);
  }
  console.log('IR valid');
} else if (cmd === 'build') {
  await build(ir, outDir);
} else if (cmd === 'pptx') {
  await renderPptx(ir, outDir);
} else if (cmd === 'all') {
  await build(ir, outDir);
  await renderPptx(ir, outDir);
} else {
  console.error('Usage: node src/index.mjs [validate|build|pptx|all] [--ir data/ir.json] [--out out/default]');
  process.exit(1);
}
