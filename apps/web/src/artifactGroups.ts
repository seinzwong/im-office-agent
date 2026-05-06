import type { ArtifactRow } from "./api";

export type ArtifactKind = "source" | "ppt" | "board" | "other";

const FEISHU_SOURCE_TYPES = new Set([
  "doc",
  "docx",
  "sheet",
  "bitable",
  "wiki",
  "mindnote",
  "file",
]);

const PPT_NAME_RE = /(?:\.pptx?$|ppt|演示|幻灯片|slide|presentation)/i;
const BOARD_NAME_RE = /(?:画板|白板|脑图|board|whiteboard)/i;

export function normalizeArtifactKind(a: ArtifactRow): ArtifactKind {
  const t = normalizeType(a.type);
  const name = a.name || "";
  if (t === "slides" || t === "ppt" || t === "presentation") return "ppt";
  if (t === "whiteboard" || t === "board") return "board";
  if (t === "file" && PPT_NAME_RE.test(name)) return "ppt";
  if (t === "file" && BOARD_NAME_RE.test(name)) return "board";
  if (FEISHU_SOURCE_TYPES.has(t)) return "source";
  if (Boolean((a.file_token || "").trim()) && t !== "folder") return "source";
  return "other";
}

export function artifactDisplayType(a: ArtifactRow): string {
  const kind = normalizeArtifactKind(a);
  if (kind === "ppt") return "PPT";
  if (kind === "board") return "画板";
  return a.type || "file";
}

export function isSlidesOutput(a: ArtifactRow): boolean {
  return normalizeArtifactKind(a) === "ppt";
}

export function isWhiteboardOutput(a: ArtifactRow): boolean {
  return normalizeArtifactKind(a) === "board";
}

export function isSourceDocument(a: ArtifactRow): boolean {
  return normalizeArtifactKind(a) === "source";
}

export function groupArtifacts(items: ArtifactRow[]) {
  const sourceDocs: ArtifactRow[] = [];
  const whiteboardOutputs: ArtifactRow[] = [];
  const slidesOutputs: ArtifactRow[] = [];

  for (const a of items) {
    const kind = normalizeArtifactKind(a);
    if (kind === "board") {
      whiteboardOutputs.push(a);
    } else if (kind === "ppt") {
      slidesOutputs.push(a);
    } else if (kind === "source") {
      sourceDocs.push(a);
    }
  }

  return { sourceDocs, whiteboardOutputs, slidesOutputs };
}

export function listOutputArtifacts(items: ArtifactRow[]): ArtifactRow[] {
  const g = groupArtifacts(items);
  return [...g.whiteboardOutputs, ...g.slidesOutputs].sort(
    (a, b) => sortKey(b.updated_time) - sortKey(a.updated_time)
  );
}

function normalizeType(type: string): string {
  return (type || "").trim().toLowerCase();
}

function sortKey(t: ArtifactRow["updated_time"]): number {
  if (t == null) return 0;
  if (typeof t === "number") return t > 1_000_000_000_000 ? Math.floor(t / 1000) : t;
  if (/^\d+$/.test(t)) return parseInt(t, 10);
  const parsed = Date.parse(t);
  return Number.isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
}
