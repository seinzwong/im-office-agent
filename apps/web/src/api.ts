const sameOrigin = "";

export type ArtifactRow = {
  file_token: string;
  name: string;
  type: string;
  url: string;
  updated_time?: string | number | null;
};

export type ArtifactsResponse = {
  artifacts: ArtifactRow[];
};

export type MeResponse = {
  user_open_id: string;
  authenticated: boolean;
};

export type Deliverables = {
  whiteboard: boolean;
  slides: boolean;
};

export type GenerateStatus = "accepted" | "success" | "partial_success" | "error";

export type GenerateTargetResult = {
  target: string;
  ok: boolean;
  message?: string;
  url?: string;
};

export type GenerateResult = {
  status: GenerateStatus;
  message: string;
  stage?: string;
  warnings: string[];
  targets: GenerateTargetResult[];
  raw: unknown;
};

type GenerateResultContext = {
  requestedTargets: string[];
};

export async function fetchMe(): Promise<MeResponse> {
  const r = await fetch(`${sameOrigin}/api/v1/me`, { credentials: "include" });
  if (!r.ok) throw new Error(`me: ${r.status}`);
  return r.json() as Promise<MeResponse>;
}

export function startOAuthLogin(): void {
  window.location.assign(`${sameOrigin}/api/v1/auth/login`);
}

export async function fetchArtifacts(): Promise<ArtifactsResponse> {
  const r = await fetch(`${sameOrigin}/api/v1/artifacts`, { credentials: "include" });
  if (!r.ok) throw new Error(`artifacts: ${r.status}`);
  const j = (await r.json()) as Partial<ArtifactsResponse>;
  return { artifacts: Array.isArray(j.artifacts) ? j.artifacts : [] };
}

export async function startDeliver(
  fileTokens: string[],
  deliverables: Deliverables
): Promise<GenerateResult> {
  const requestedTargets = Object.entries(deliverables)
    .filter(([, enabled]) => enabled)
    .map(([target]) => target);

  const r = await fetch(`${sameOrigin}/api/v1/deliver`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_tokens: fileTokens, deliverables }),
  });

  let body: unknown = null;
  try {
    body = await r.json();
  } catch {
    body = null;
  }

  if (r.status !== 202 && !r.ok) {
    const message = readString(body, "message") || readString(body, "error") || `deliver: ${r.status}`;
    throw new Error(message);
  }

  return normalizeGenerateResult(body, { requestedTargets });
}

export function normalizeGenerateResult(
  response: unknown,
  context: GenerateResultContext
): GenerateResult {
  const obj = asRecord(response);
  const publishResult = asRecord(obj?.publish_result);
  const targetRows = readTargets(publishResult?.targets ?? obj?.targets);
  const warnings = readStringList(obj?.warnings ?? publishResult?.warnings);
  const error = readString(obj, "error") || readString(publishResult, "error");
  const stage = readString(obj, "stage") || readString(publishResult, "stage");
  const explicitStatus = (readString(obj, "status") || readString(publishResult, "status")).toLowerCase();

  let status: GenerateStatus = "accepted";
  if (error || explicitStatus === "error" || explicitStatus === "failed") {
    status = "error";
  } else if (targetRows.length > 0) {
    const okCount = targetRows.filter((target) => target.ok).length;
    if (okCount === targetRows.length) status = "success";
    else if (okCount > 0) status = "partial_success";
    else status = "error";
  } else if (explicitStatus === "success" || explicitStatus === "ok") {
    status = "success";
  } else if (explicitStatus === "partial_success") {
    status = "partial_success";
  }

  const fallbackTargetText =
    context.requestedTargets.length > 0 ? context.requestedTargets.join(" / ") : "任务";
  const message =
    readString(obj, "message") ||
    readString(publishResult, "message") ||
    error ||
    (status === "accepted" ? `生成任务已提交：${fallbackTargetText}` : "生成任务已完成");

  return {
    status,
    message,
    stage,
    warnings,
    targets: targetRows,
    raw: response,
  };
}

export async function triggerDevSummary(): Promise<void> {
  const r = await fetch(`${sameOrigin}/api/v1/dev/trigger-summary`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!r.ok) throw new Error(`dev trigger: ${r.status}`);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readString(source: unknown, key: string): string {
  const obj = asRecord(source);
  const value = obj?.[key];
  return typeof value === "string" ? value : "";
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function readTargets(value: unknown): GenerateTargetResult[] {
  if (!Array.isArray(value)) return [];
  return value.map(readTarget).filter((target): target is GenerateTargetResult => target !== null);
}

function readTarget(value: unknown): GenerateTargetResult | null {
  const obj = asRecord(value);
  if (!obj) return null;
  const target = String(obj.target ?? obj.type ?? obj.name ?? "");
  if (!target) return null;
  const okValue = obj.ok ?? obj.success ?? obj.status;
  const ok = okValue === true || okValue === "success" || okValue === "ok" || okValue === "done";
  return {
    target,
    ok,
    message: readString(obj, "message") || readString(obj, "error") || undefined,
    url: readString(obj, "url") || undefined,
  };
}
