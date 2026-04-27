const sameOrigin = "";

export async function postDevLogin(): Promise<void> {
  const r = await fetch(`${sameOrigin}/api/v1/auth/dev`, {
    method: "POST",
    credentials: "include",
  });
  if (!r.ok) throw new Error(`auth: ${r.status}`);
}

export async function fetchMe(): Promise<{ user_open_id: string }> {
  const r = await fetch(`${sameOrigin}/api/v1/me`, { credentials: "include" });
  if (!r.ok) throw new Error(`me: ${r.status}`);
  return r.json() as Promise<{ user_open_id: string }>;
}

export type ArtifactsResponse = {
  artifacts: ArtifactRow[];
};

export type ArtifactRow = {
  file_token: string;
  name: string;
  type: string;
  url: string;
  updated_time?: string | number | null;
};

export async function fetchArtifacts(): Promise<ArtifactsResponse> {
  const r = await fetch(`${sameOrigin}/api/v1/artifacts`, { credentials: "include" });
  if (!r.ok) throw new Error(`artifacts: ${r.status}`);
  const j = (await r.json()) as Partial<ArtifactsResponse> & { artifacts?: ArtifactRow[] };
  return { artifacts: j.artifacts ?? [] };
}

export async function startDeliver(
  fileTokens: string[],
  deliverables: { whiteboard: boolean; slides: boolean }
): Promise<{ status: string; message: string }> {
  const r = await fetch(`${sameOrigin}/api/v1/deliver`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_tokens: fileTokens, deliverables }),
  });
  if (r.status !== 202) throw new Error(`deliver: ${r.status}`);
  return r.json() as Promise<{ status: string; message: string }>;
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
