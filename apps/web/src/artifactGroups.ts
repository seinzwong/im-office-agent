import type { ArtifactRow } from "./api";

/**
 * 飞书云盘 `drive files list` 常见 type；与开放平台枚举对齐，可随 API 增补。
 * 来源区：可交付输入的云文件（不含下方单独展示的幻灯片 / 画板产出）。
 */
const FEISHU_SOURCE_TYPES = new Set([
  "doc",
  "docx",
  "sheet",
  "bitable",
  "wiki",
  "mindnote",
  "file",
]);

export function isSourceDocument(a: ArtifactRow): boolean {
  const t = (a.type || "").toLowerCase();
  if (FEISHU_SOURCE_TYPES.has(t)) {
    return true;
  }
  const token = (a.file_token || "").trim();
  if (!token || t === "folder") {
    return false;
  }
  /** 未列出的新枚举：有云文件 token 即视为可选来源 */
  return true;
}

export function isSlidesOutput(a: ArtifactRow): boolean {
  return (a.type || "").toLowerCase() === "slides";
}

/**
 * 画板产物：以飞书枚举为准；MVP 对 `file` + 名称含「画板/白板/脑图」等作兜底，并预留
 * `whiteboard` / `board` 等 type。
 */
export function isWhiteboardOutput(a: ArtifactRow): boolean {
  const t = (a.type || "").toLowerCase();
  if (t === "whiteboard" || t === "board") {
    return true;
  }
  if (t === "file" && /画板|whiteboard|白板|脑图/i.test(a.name)) {
    return true;
  }
  return false;
}

export function groupArtifacts(items: ArtifactRow[]) {
  const sourceDocs: ArtifactRow[] = [];
  const whiteboardOutputs: ArtifactRow[] = [];
  const slidesOutputs: ArtifactRow[] = [];
  for (const a of items) {
    /** 先收产出区，避免与「幻灯片 / 画板」同 type 的条目误进来源区 */
    if (isWhiteboardOutput(a)) {
      whiteboardOutputs.push(a);
    } else if (isSlidesOutput(a)) {
      slidesOutputs.push(a);
    } else if (isSourceDocument(a)) {
      sourceDocs.push(a);
    }
  }
  return { sourceDocs, whiteboardOutputs, slidesOutputs };
}

function _outSortKey(t: ArtifactRow["updated_time"]): number {
  if (t == null) {
    return 0;
  }
  if (typeof t === "number") {
    return t > 1_000_000_000_000 ? Math.floor(t / 1000) : t;
  }
  if (typeof t === "string" && /^\d+$/.test(t)) {
    return parseInt(t, 10);
  }
  return 0;
}

/**
 * 所有产出物（画板、幻灯片等）合并为一张表，按「最近更新」新到旧。
 */
export function listOutputArtifacts(items: ArtifactRow[]): ArtifactRow[] {
  const g = groupArtifacts(items);
  const all = [...g.whiteboardOutputs, ...g.slidesOutputs];
  return all.sort(
    (a, b) => _outSortKey(b.updated_time) - _outSortKey(a.updated_time)
  );
}
