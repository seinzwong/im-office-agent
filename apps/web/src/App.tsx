import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { ArtifactRow, GenerateResult } from "./api";
import * as api from "./api";
import { artifactDisplayType, groupArtifacts, listOutputArtifacts, normalizeArtifactKind } from "./artifactGroups";

type RunState = "idle" | "generating" | "syncing" | "success" | "partial_success" | "error";
type MessageKind = "info" | "success" | "warning" | "error";
type DeliverTarget = "board" | "ppt";
type IconName =
  | "automation"
  | "board"
  | "check"
  | "cloud"
  | "file"
  | "flow"
  | "messages"
  | "ppt"
  | "refresh"
  | "sparkles"
  | "users";

type Message = {
  kind: MessageKind;
  text: string;
};

type PendingRun = {
  previousTokens: Set<string>;
  requestedTargets: DeliverTarget[];
};

const PROGRESS_STEPS = ["提交任务", "生成内容", "同步云盘", "发现新产物"];
const REFRESH_DELAYS = [1000, 3000, 5000, 8000, 12000, 20000, 30000, 45000, 60000, 75000, 90000];

function fmtTime(t: ArtifactRow["updated_time"]): string {
  if (t == null) return "-";
  if (typeof t === "string") {
    if (/^\d+$/.test(t)) return formatDate(parseInt(t, 10) * 1000);
    return t;
  }
  return formatDate(t > 1_000_000_000_000 ? t : t * 1000);
}

function formatDate(value: number): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function artifactUrlState(d: ArtifactRow): "real" | "placeholder" | "none" {
  if (!d.url) return "none";
  return d.url.startsWith("https://dev-placeholder.invalid") ? "placeholder" : "real";
}

export function App() {
  const [me, setMe] = useState<string | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [items, setItems] = useState<ArtifactRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [whiteboard, setWhiteboard] = useState(true);
  const [slides, setSlides] = useState(true);
  const [runState, setRunState] = useState<RunState>("idle");
  const [generateResult, setGenerateResult] = useState<GenerateResult | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [highlightTokens, setHighlightTokens] = useState<Set<string>>(new Set());
  const timers = useRef<number[]>([]);
  const pendingRun = useRef<PendingRun | null>(null);

  const { sourceDocs } = useMemo(() => groupArtifacts(items), [items]);
  const outputArtifacts = useMemo(() => listOutputArtifacts(items), [items]);

  const loadArtifacts = useCallback(async (options?: { quiet?: boolean; previousTokens?: Set<string> }) => {
    if (options?.quiet) setRefreshing(true);
    else setListLoading(true);

    try {
      const a = await api.fetchArtifacts();
      setItems(a.artifacts);
      const pending = pendingRun.current;
      if (pending) {
        const addedOutputs = findNewOutputArtifacts(a.artifacts, pending.previousTokens, pending.requestedTargets);
        if (addedOutputs.length > 0) {
          clearRefreshTimers(timers.current);
          pendingRun.current = null;
          setHighlightTokens(new Set(addedOutputs.map((item) => item.file_token)));
          setRunState("success");
          setMessage({ kind: "success", text: `已生成 ${addedOutputs.length} 个新产物。` });
        }
      }
      if (options?.previousTokens) {
        const nextTokens = new Set(a.artifacts.map((item) => item.file_token));
        const added = [...nextTokens].filter((token) => !options.previousTokens?.has(token));
        setHighlightTokens(new Set(added));
      }
      return a.artifacts;
    } finally {
      if (options?.quiet) setRefreshing(false);
      else setListLoading(false);
    }
  }, []);

  const bootstrap = useCallback(async () => {
    setMessage(null);
    try {
      const m = await api.fetchMe();
      if (!m.authenticated) {
        api.startOAuthLogin();
        return;
      }
      setMe(m.user_open_id);
      await loadArtifacts();
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
      setListLoading(false);
    }
  }, [loadArtifacts]);

  useEffect(() => {
    void bootstrap();
    return () => clearRefreshTimers(timers.current);
  }, [bootstrap]);

  const toggle = useCallback((fileToken: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fileToken)) next.delete(fileToken);
      else next.add(fileToken);
      return next;
    });
  }, []);

  async function onDeliver() {
    if (!selected.size) {
      setRunState("error");
      setMessage({ kind: "error", text: "请至少选择一个来源文档。" });
      return;
    }
    if (!whiteboard && !slides) {
      setRunState("error");
      setMessage({ kind: "error", text: "请至少选择一种交付物类型。" });
      return;
    }

    clearRefreshTimers(timers.current);
    const previousTokens = new Set(items.map((item) => item.file_token));
    const requestedTargets = requestedDeliverTargets(whiteboard, slides);
    pendingRun.current = { previousTokens, requestedTargets };
    setMessage(null);
    setGenerateResult(null);
    setHighlightTokens(new Set());
    setRunState("generating");

    try {
      const result = await api.startDeliver([...selected], { whiteboard, slides });
      setGenerateResult(result);
      if (result.status === "accepted") {
        setRunState("syncing");
        setMessage({ kind: "info", text: "任务已提交，正在生成并同步到飞书云盘。" });
        scheduleRefreshes(previousTokens, requestedTargets);
        return;
      }
      pendingRun.current = null;
      setRunState(result.status);
      setMessage({
        kind: result.status === "partial_success" ? "warning" : result.status === "error" ? "error" : "success",
        text: result.message,
      });
    } catch (e) {
      pendingRun.current = null;
      setRunState("error");
      setMessage({ kind: "error", text: String(e) });
    }
  }

  function scheduleRefreshes(previousTokens: Set<string>, requestedTargets: DeliverTarget[]) {
    clearRefreshTimers(timers.current);
    timers.current = REFRESH_DELAYS.map((delay, index) =>
      window.setTimeout(() => {
        void loadArtifacts({ quiet: true }).then((artifacts) => {
          const addedOutputs = findNewOutputArtifacts(artifacts, previousTokens, requestedTargets);
          if (addedOutputs.length > 0) {
            clearRefreshTimers(timers.current);
            pendingRun.current = null;
            setHighlightTokens(new Set(addedOutputs.map((item) => item.file_token)));
            setRunState("success");
            setMessage({ kind: "success", text: `已生成 ${addedOutputs.length} 个新产物。` });
            return;
          }
          if (index === REFRESH_DELAYS.length - 1) {
            pendingRun.current = null;
            setRunState("idle");
            setMessage({ kind: "info", text: "已持续等待约 1 分 30 秒，产物可能仍在飞书侧同步，请稍后刷新。" });
          }
        }).catch((e) => {
          setMessage({ kind: "warning", text: `产物列表刷新失败，已保留当前列表。${String(e)}` });
        });
      }, delay)
    );
  }

  async function onManualRefresh() {
    setMessage(null);
    try {
      await loadArtifacts();
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
    }
  }

  async function onDevSummary() {
    setMessage(null);
    try {
      await api.triggerDevSummary();
      setMessage({ kind: "info", text: "已触发开发总结生成，请稍后刷新查看新文档。" });
      await loadArtifacts({ quiet: true });
    } catch (e) {
      setMessage({ kind: "error", text: String(e) });
    }
  }

  return (
    <main className="app-shell">
      <AppHeader
        userOpenId={me}
        refreshing={refreshing || listLoading}
        onRefresh={() => void onManualRefresh()}
        onDevSummary={() => void onDevSummary()}
      />

      {message && <MessageBar message={message} />}

      <div className="workspace">
        <SourceDocumentCard docs={sourceDocs} loading={listLoading} selected={selected} onToggle={toggle} />
        <DeliveryOptionsCard
          whiteboard={whiteboard}
          slides={slides}
          selectedCount={selected.size}
          runState={runState}
          onWhiteboardChange={setWhiteboard}
          onSlidesChange={setSlides}
          onDeliver={() => void onDeliver()}
        />
      </div>

      <GenerationProgress state={runState} result={generateResult} selectedCount={selected.size} hasDeliverable={whiteboard || slides} />

      <ArtifactList artifacts={outputArtifacts} loading={listLoading} refreshing={refreshing} highlightTokens={highlightTokens} />
    </main>
  );
}

function AppHeader(props: {
  userOpenId: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  onDevSummary: () => void;
}) {
  return (
    <header className="app-header">
      <div className="brand-block">
        <ProductMark />
        <div>
          <div className="brand-eyebrow">
            <StatusBadge status="飞书工作流驱动" icon="cloud" />
          </div>
          <h1>LarkFlow</h1>
          <p className="brand-slogan">让团队协作内容，自动流转为高质量交付物。</p>
          <p className="brand-support">选择飞书云文档，一键生成结构化画板与演示文稿。</p>
        </div>
      </div>
      <div className="header-actions">
        <StatusBadge status={props.userOpenId ? "已登录" : "登录中"} detail={props.userOpenId ?? undefined} icon="users" />
        <button type="button" className="icon-button" onClick={props.onRefresh} title="刷新列表" aria-label="刷新列表">
          <Icon name="refresh" spinning={props.refreshing} />
        </button>
        <button type="button" className="icon-button" onClick={props.onDevSummary} title="生成开发总结" aria-label="生成开发总结">
          <Icon name="sparkles" />
        </button>
      </div>
    </header>
  );
}

function SourceDocumentCard(props: {
  docs: ArtifactRow[];
  loading: boolean;
  selected: Set<string>;
  onToggle: (fileToken: string) => void;
}) {
  return (
    <section className="panel source-panel" aria-label="来源文档">
      <div className="panel-head">
        <div className="section-title">
          <IconFrame icon="file" />
          <div>
            <h2>来源文档</h2>
            <p>选择飞书云文档作为生成输入。</p>
          </div>
        </div>
        <StatusBadge status={`已选 ${props.selected.size} 个`} />
      </div>

      {props.loading ? (
        <SkeletonList rows={4} />
      ) : props.docs.length === 0 ? (
        <EmptyState icon="cloud" text="暂无可作为输入的来源文档。" />
      ) : (
        <div className="artifact-list" role="list">
          {props.docs.map((doc, index) => (
            <SourceDocumentRow
              doc={doc}
              checked={props.selected.has(doc.file_token)}
              index={index}
              key={doc.file_token}
              onToggle={props.onToggle}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function DeliveryOptionsCard(props: {
  whiteboard: boolean;
  slides: boolean;
  selectedCount: number;
  runState: RunState;
  onWhiteboardChange: (enabled: boolean) => void;
  onSlidesChange: (enabled: boolean) => void;
  onDeliver: () => void;
}) {
  const generating = props.runState === "generating" || props.runState === "syncing";
  const canGenerate = props.selectedCount > 0 && (props.whiteboard || props.slides) && !generating;

  return (
    <section className="panel delivery-panel" aria-label="交付物">
      <div className="panel-head compact">
        <div className="section-title">
          <IconFrame icon="automation" />
          <div>
            <h2>交付物</h2>
            <p>生成适合协作流转的画板与演示文稿。</p>
          </div>
        </div>
      </div>
      <div className="option-stack">
        <ToggleRow
          icon="board"
          title="画板"
          description="创建用于团队对齐的结构化白板。"
          checked={props.whiteboard}
          disabled={generating}
          onChange={props.onWhiteboardChange}
        />
        <ToggleRow
          icon="ppt"
          title="PPT"
          description="创建可直接汇报的演示文稿。"
          checked={props.slides}
          disabled={generating}
          onChange={props.onSlidesChange}
        />
      </div>
      <button type="button" className="primary-action" disabled={!canGenerate} onClick={props.onDeliver}>
        {generating ? (
          <>
            <span className="spinner light" aria-hidden />
            正在生成交付物...
          </>
        ) : (
          <>
            <Icon name="sparkles" />
            生成交付物
          </>
        )}
      </button>
      <p className="hint-text">
        {props.selectedCount > 0
          ? `已选择 ${props.selectedCount} 个来源文档。`
          : "选择来源文档后即可生成。"}
      </p>
    </section>
  );
}

function GenerationProgress(props: { state: RunState; result: GenerateResult | null; selectedCount: number; hasDeliverable: boolean }) {
  if (props.state === "idle") {
    const ready = props.selectedCount > 0 && props.hasDeliverable;
    return (
      <section className="progress-panel state-idle">
        <div className="progress-title">
          <IconFrame icon={ready ? "check" : "messages"} />
          <div>
            <StatusBadge status={ready ? "准备生成" : "待选择"} />
            <p>{ready ? "已满足生成条件，可以开始生成。" : "请选择来源文档和交付物类型。"}</p>
          </div>
        </div>
      </section>
    );
  }

  const activeStep = progressStepCount(props.state);
  return (
    <section className={`progress-panel state-${props.state}`}>
      <div className="progress-title">
        <IconFrame icon={props.state === "success" || props.state === "partial_success" ? "check" : "flow"} busy={props.state === "generating" || props.state === "syncing"} />
        <div>
          <StatusBadge status={progressLabel(props.state)} />
          <p>{progressDescription(props.state, props.result)}</p>
        </div>
      </div>
      <div className="step-strip">
        {PROGRESS_STEPS.map((step, index) => (
          <span className={index < activeStep ? "step is-done" : "step"} key={step}>
            {index < activeStep && <Icon name="check" />}
            {step}
          </span>
        ))}
      </div>
      {props.result?.warnings.map((warning) => (
        <p className="warning-line" key={warning}>
          {warning}
        </p>
      ))}
    </section>
  );
}

function ArtifactList(props: {
  artifacts: ArtifactRow[];
  loading: boolean;
  refreshing: boolean;
  highlightTokens: Set<string>;
}) {
  return (
    <section className="panel output-panel" aria-label="生成结果">
      <div className="panel-head">
        <div className="section-title">
          <IconFrame icon="flow" />
          <div>
            <h2>生成结果</h2>
            <p>最近生成的画板与演示文稿。</p>
          </div>
        </div>
        <StatusBadge status={props.refreshing ? "刷新中" : `${props.artifacts.length} 个产物`} />
      </div>

      {props.loading ? (
        <SkeletonList rows={3} />
      ) : props.artifacts.length === 0 ? (
        <EmptyState icon="board" text="暂无生成的画板或演示文稿。" />
      ) : (
        <div className="artifact-list readonly" role="list">
          {props.artifacts.map((artifact, index) => (
            <ArtifactRowView
              artifact={artifact}
              highlighted={props.highlightTokens.has(artifact.file_token)}
              index={index}
              key={artifact.file_token}
            />
          ))}
        </div>
      )}
    </section>
  );
}

const SourceDocumentRow = memo(function SourceDocumentRow({
  doc,
  checked,
  index,
  onToggle,
}: {
  doc: ArtifactRow;
  checked: boolean;
  index: number;
  onToggle: (fileToken: string) => void;
}) {
  return (
    <label className={`artifact-row selectable ${checked ? "is-selected" : ""}`} role="listitem" style={{ "--row-index": index } as CSSProperties}>
      <span className="checkbox-shell">
        <input type="checkbox" checked={checked} onChange={() => onToggle(doc.file_token)} aria-label={`选择 ${doc.name}`} />
        <span className="custom-check"><Icon name="check" /></span>
      </span>
      <ArtifactIdentity artifact={doc} />
      <TypeBadge artifact={doc} />
      <span className="artifact-time">{fmtTime(doc.updated_time)}</span>
      <ArtifactLink artifact={doc} />
    </label>
  );
});

const ArtifactRowView = memo(function ArtifactRowView({
  artifact,
  highlighted,
  index,
}: {
  artifact: ArtifactRow;
  highlighted: boolean;
  index: number;
}) {
  return (
    <div className={`artifact-row ${highlighted ? "is-new" : ""}`} role="listitem" style={{ "--row-index": index } as CSSProperties}>
      <ArtifactIdentity artifact={artifact} />
      <TypeBadge artifact={artifact} />
      <span className="artifact-time">{fmtTime(artifact.updated_time)}</span>
      <ArtifactLink artifact={artifact} />
    </div>
  );
});

function ArtifactIdentity({ artifact }: { artifact: ArtifactRow }) {
  return (
    <span className="artifact-main" title={artifact.name}>
      <span className="artifact-name">
        <ArtifactTypeIcon artifact={artifact} />
        {artifact.name}
      </span>
      <code title="file_token">{artifact.file_token}</code>
    </span>
  );
}

function ArtifactLink({ artifact }: { artifact: ArtifactRow }) {
  const state = artifactUrlState(artifact);
  if (state === "real") {
    return (
      <a className="open-link" href={artifact.url} target="_blank" rel="noreferrer">
        打开
      </a>
    );
  }
  if (state === "placeholder") {
    return (
      <span className="placeholder-link" title="开发占位链接">
        占位链接
      </span>
    );
  }
  return <span className="placeholder-link">在飞书内打开</span>;
}

function ToggleRow(props: {
  icon: IconName;
  title: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <label className={`toggle-row ${props.checked ? "is-on" : ""}`}>
      <input
        type="checkbox"
        checked={props.checked}
        disabled={props.disabled}
        onChange={(event) => props.onChange(event.currentTarget.checked)}
      />
      <span className="toggle-copy">
        <span className="toggle-icon"><Icon name={props.icon} /></span>
        <span>
          <strong>{props.title}</strong>
          <small>{props.description}</small>
        </span>
      </span>
      <span className="toggle-check" aria-hidden><Icon name="check" /></span>
    </label>
  );
}

function StatusBadge({ status, detail, icon }: { status: string; detail?: string; icon?: IconName }) {
  return (
    <span className="status-badge" title={detail}>
      {icon && <Icon name={icon} />}
      {detail ? `${status} - ${detail}` : status}
    </span>
  );
}

function TypeBadge({ artifact }: { artifact: ArtifactRow }) {
  const displayType = artifactDisplayType(artifact);
  const kind = normalizeArtifactKind(artifact);
  const normalized = kind === "ppt" ? "ppt" : kind === "board" ? "board" : displayType.toLowerCase();
  return <span className={`type-badge t-${normalized}`}>{displayType}</span>;
}

function ArtifactTypeIcon({ artifact }: { artifact: ArtifactRow }) {
  const kind = normalizeArtifactKind(artifact);
  if (kind === "ppt") return <Icon name="ppt" />;
  if (kind === "board") return <Icon name="board" />;
  return <Icon name="file" />;
}

function MessageBar({ message }: { message: Message }) {
  return <div className={`message-bar ${message.kind}`}>{linkifyText(message.text)}</div>;
}

function linkifyText(text: string) {
  const urlPattern = /(https?:\/\/[^\s]+)/g;
  const parts = text.split(urlPattern);
  return parts.map((part, index) => {
    if (!/^https?:\/\//.test(part)) return <span key={`${part}-${index}`}>{part}</span>;
    return (
      <a key={`${part}-${index}`} href={part} target="_blank" rel="noreferrer">
        {part}
      </a>
    );
  });
}

function EmptyState({ icon, text }: { icon: IconName; text: string }) {
  return (
    <div className="empty-state">
      <IconFrame icon={icon} />
      <span>{text}</span>
    </div>
  );
}

function SkeletonList({ rows }: { rows: number }) {
  return (
    <div className="skeleton-list" aria-label="加载中">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-row" key={index}>
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

function progressLabel(state: RunState): string {
  if (state === "generating") return "生成中";
  if (state === "syncing") return "正在等待新产物同步";
  if (state === "partial_success") return "部分完成";
  if (state === "error") return "需要处理";
  return "已生成";
}

function progressDescription(state: RunState, result: GenerateResult | null): string {
  if (state === "generating") return "LarkFlow 正在整理内容并提交生成任务...";
  if (state === "syncing") return "任务已提交，正在等待新产物同步到飞书云盘。";
  if (state === "success") return "已发现新产物，列表已刷新。";
  if (state === "partial_success") return result?.stage || "部分交付物已生成，请查看提示信息。";
  if (state === "error") return result?.stage || "生成过程中出现问题，请根据提示处理。";
  return "生成请求已处理。";
}

function progressStepCount(state: RunState): number {
  if (state === "generating") return 1;
  if (state === "syncing") return 3;
  if (state === "success" || state === "partial_success") return PROGRESS_STEPS.length;
  if (state === "error") return 1;
  return 0;
}

function clearRefreshTimers(timerIds: number[]) {
  for (const id of timerIds) window.clearTimeout(id);
  timerIds.length = 0;
}

function requestedDeliverTargets(whiteboard: boolean, slides: boolean): DeliverTarget[] {
  const targets: DeliverTarget[] = [];
  if (whiteboard) targets.push("board");
  if (slides) targets.push("ppt");
  return targets;
}

function findNewOutputArtifacts(
  artifacts: ArtifactRow[],
  previousTokens: Set<string>,
  requestedTargets: DeliverTarget[]
): ArtifactRow[] {
  const targetSet = new Set(requestedTargets);
  return artifacts.filter((artifact) => {
    if (previousTokens.has(artifact.file_token)) return false;
    const kind = normalizeArtifactKind(artifact);
    return (kind === "board" && targetSet.has("board")) || (kind === "ppt" && targetSet.has("ppt"));
  });
}

function ProductMark() {
  return (
    <span className="product-mark" aria-hidden>
      <svg viewBox="0 0 48 48" fill="none">
        <path d="M15 16h8c4.5 0 7 2.4 7 6s-2.5 6-7 6h-6" stroke="white" strokeWidth="4" strokeLinecap="round" />
        <path d="M25 10h6.5C37.3 10 41 13.7 41 19.2S37.3 28 31.5 28H30" stroke="white" strokeOpacity=".72" strokeWidth="4" strokeLinecap="round" />
        <circle cx="14" cy="16" r="4" fill="white" />
        <circle cx="17" cy="28" r="4" fill="white" />
        <circle cx="32" cy="10" r="3.2" fill="white" fillOpacity=".9" />
      </svg>
    </span>
  );
}

function IconFrame({ icon, busy }: { icon: IconName; busy?: boolean }) {
  return (
    <span className={`icon-frame ${busy ? "is-busy" : ""}`}>
      <Icon name={icon} />
    </span>
  );
}

function Icon({ name, spinning }: { name: IconName; spinning?: boolean }) {
  const common = {
    className: `icon-sprite ${spinning ? "is-spinning" : ""}`,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "automation":
      return <svg {...common}><path d="M12 3v3" /><path d="M12 18v3" /><path d="m4.9 4.9 2.1 2.1" /><path d="m17 17 2.1 2.1" /><path d="M3 12h3" /><path d="M18 12h3" /><path d="m4.9 19.1 2.1-2.1" /><path d="m17 7 2.1-2.1" /><circle cx="12" cy="12" r="4" /></svg>;
    case "board":
      return <svg {...common}><rect x="3" y="4" width="18" height="14" rx="2" /><path d="M8 20h8" /><path d="M12 18v2" /><path d="M7 9h4" /><path d="M7 13h7" /><path d="M16 9h1" /></svg>;
    case "check":
      return <svg {...common}><path d="m5 12 4 4L19 6" /></svg>;
    case "cloud":
      return <svg {...common}><path d="M17.5 19H8a5 5 0 1 1 1.1-9.9A6 6 0 0 1 20 12.7 3.5 3.5 0 0 1 17.5 19Z" /></svg>;
    case "file":
      return <svg {...common}><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z" /><path d="M14 2v5h5" /><path d="M9 13h6" /><path d="M9 17h4" /></svg>;
    case "flow":
      return <svg {...common}><circle cx="6" cy="7" r="3" /><circle cx="18" cy="17" r="3" /><path d="M9 7h4a5 5 0 0 1 5 5v2" /><path d="M6 10v7h9" /></svg>;
    case "messages":
      return <svg {...common}><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" /><path d="M8 9h8" /><path d="M8 13h5" /></svg>;
    case "ppt":
      return <svg {...common}><path d="M4 4h16v12H4Z" /><path d="M12 16v4" /><path d="M8 20h8" /><path d="M8 8h5a2 2 0 0 1 0 4H8V8Z" /></svg>;
    case "refresh":
      return <svg {...common}><path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /></svg>;
    case "sparkles":
      return <svg {...common}><path d="M12 3 9.5 9.5 3 12l6.5 2.5L12 21l2.5-6.5L21 12l-6.5-2.5Z" /><path d="M19 3v4" /><path d="M21 5h-4" /><path d="M5 17v3" /><path d="M6.5 18.5h-3" /></svg>;
    case "users":
      return <svg {...common}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>;
  }
}
