import { useCallback, useEffect, useMemo, useState } from "react";
import type { ArtifactRow } from "./api";
import * as api from "./api";
import { groupArtifacts, listOutputArtifacts } from "./artifactGroups";

function fmtTime(t: ArtifactRow["updated_time"]): string {
  if (t == null) return "—";
  if (typeof t === "string") {
    if (/^\d+$/.test(t)) {
      return new Date(parseInt(t, 10) * 1000).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    return t;
  }
  if (typeof t === "number") {
    if (t > 1_000_000_000_000) return new Date(t).toLocaleString("zh-CN");
    return new Date(t * 1000).toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return "—";
}

function linkCell(d: ArtifactRow) {
  if (d.url && !d.url.startsWith("https://dev-placeholder.invalid")) {
    return (
      <a href={d.url} target="_blank" rel="noreferrer">
        打开
      </a>
    );
  }
  if (d.url && d.url.startsWith("https://dev-placeholder.invalid")) {
    return (
      <span className="muted" title="DEV 占位，无真实页面">
        模拟链接
      </span>
    );
  }
  return <span className="muted">在飞书内打开</span>;
}

function IconRefresh() {
  return (
    <svg
      className="icon-sprite"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M21 2v6h-6" />
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M3 22v-6h6" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
    </svg>
  );
}

function IconDocPlus() {
  return (
    <svg
      className="icon-sprite"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M12 11v6" />
      <path d="M9 14h6" />
    </svg>
  );
}

export function App() {
  const [me, setMe] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [items, setItems] = useState<ArtifactRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [wb, setWb] = useState(true);
  const [slides, setSlides] = useState(false);
  const [deliverHint, setDeliverHint] = useState<string | null>(null);
  const [devHint, setDevHint] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(true);

  const { sourceDocs } = useMemo(() => groupArtifacts(items), [items]);

  const outputArtifacts = useMemo(() => listOutputArtifacts(items), [items]);

  const load = useCallback(async () => {
    setErr(null);
    setListLoading(true);
    try {
      const m = await api.fetchMe();
      setMe(m.user_open_id);
      const a = await api.fetchArtifacts();
      setItems(a.artifacts);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    api
      .postDevLogin()
      .then(load)
      .catch((e) => setErr(String(e)));
  }, [load]);

  function toggle(fileToken: string) {
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(fileToken)) n.delete(fileToken);
      else n.add(fileToken);
      return n;
    });
  }

  async function onDeliver() {
    if (!selected.size) {
      setErr("请至少选一项云文档");
      return;
    }
    if (!wb && !slides) {
      setErr("请至少选画板或 PPT 之一");
      return;
    }
    setErr(null);
    const res = await api.startDeliver([...selected], { whiteboard: wb, slides: slides });
    setDeliverHint(res.message);
  }

  return (
    <div>
      <h1>im-office-agent（应用内 H5 示意）</h1>
      <p className="muted">Gateway + 外部 Agents（由 gateway.yaml 配置）；`POST /api/v1/auth/dev` 已自动登录。生产环境改为飞书 OAuth。列表来自云空间 `ARTIFACTS` 配置目录，无本地业务库。</p>
      {err && <p className="error">{err}</p>}

      <section className="session-bar">
        <div className="session-line">
          <div className="session-left">
            <span className="session-title">会话</span>
            <span className="session-user" title="user_open_id">
              {me ? <>已登录 · {me}</> : "…"}
            </span>
          </div>
          <div className="session-actions">
            <button
              type="button"
              className="icon-button"
              onClick={() => void load()}
              title="刷新列表"
              aria-label="刷新列表"
            >
              <IconRefresh />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                setDevHint(null);
                void api
                  .triggerDevSummary()
                  .then(() => {
                    setDevHint("已触发生成；若已配置云盘，请稍后点刷新在目标目录查看新文档。");
                    void load();
                  })
                  .catch((e) => setErr(String(e)));
              }}
              title="生成一条开发用总结"
              aria-label="生成一条开发用总结"
            >
              <IconDocPlus />
            </button>
          </div>
        </div>
        {devHint && <p className="muted session-hint">{devHint}</p>}
      </section>

      <div
        className="workbench"
        aria-label="源文档与交付工作区"
        data-area="交付工作台"
      >
        <h2 className="workbench-title">交付工作台</h2>
        <div className="workbench-block" role="region" aria-label="来源云文档">
          <h3>来源云文档</h3>
          {listLoading ? (
            <p className="muted">正在拉取目录…</p>
          ) : sourceDocs.length === 0 ? (
            <p className="muted">
              暂无可作交付来源。请确认目录中有飞书云文件（文档、表格、多维表格、知识库页面等）；<code>DEV_SKIP_LARK=true</code>{" "}
              时仅显示占位数据。
            </p>
          ) : (
            <div className="artifact-table" role="table" aria-label="来源云文档列表">
              <div className="artifact-row artifact-head" role="row">
                <span className="cell c-check" />
                <span className="cell c-name">名称</span>
                <span className="cell c-type">类型</span>
                <span className="cell c-time">最近更新</span>
                <span className="cell c-link">操作</span>
              </div>
              {sourceDocs.map((d) => (
                <div className="artifact-row" key={d.file_token} role="row">
                  <div className="cell c-check">
                    <input
                      type="checkbox"
                      title="选择用于交付"
                      checked={selected.has(d.file_token)}
                      onChange={() => toggle(d.file_token)}
                    />
                  </div>
                  <div className="cell c-name" title={d.name}>
                    <span className="name-text">{d.name}</span>
                    <code className="file-token" title="file_token">
                      {d.file_token}
                    </code>
                  </div>
                  <div className="cell c-type">
                    <span className={`type-badge t-${d.type}`}>{d.type}</span>
                  </div>
                  <div className="cell c-time">{fmtTime(d.updated_time)}</div>
                  <div className="cell c-link">{linkCell(d)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="workbench-block" role="region" aria-label="交付选项">
          <h3>交付选项</h3>
          <div className="deliver-row" role="group" aria-label="生成交付与提交">
            <div className="deliver-toggles">
              <label>
                <input type="checkbox" checked={wb} onChange={() => setWb(!wb)} />
                生成画板
              </label>{" "}
              <label>
                <input type="checkbox" checked={slides} onChange={() => setSlides(!slides)} />
                生成 PPT
              </label>
            </div>
            <button type="button" onClick={() => void onDeliver()}>
              开始生成
            </button>
          </div>
          {deliverHint && <p className="muted deliver-hint">{deliverHint}</p>}
        </div>
      </div>

      <div className="workbench" data-area="产出物" aria-label="产出物">
        <h2 className="workbench-title">产出物</h2>
        <div className="workbench-block" role="region" aria-label="产出物列表">
          <p className="muted section-lead">
            与上方来源列表同一云盘目录；含画板、幻灯片等产出，仅作展示、不参与交付多选。类型以「类型」列中的 badge 区分。
          </p>
          {outputArtifacts.length === 0 ? (
            <p className="muted out-empty">当前目录下暂无产出的画板、幻灯片等，或需等待生成后点刷新。</p>
          ) : (
            <div
              className="artifact-table artifact-table-readonly"
              role="table"
              aria-label="产出物合并列表"
            >
              <div className="artifact-row artifact-head" role="row">
                <span className="cell c-name c-name-only">名称</span>
                <span className="cell c-type">类型</span>
                <span className="cell c-time">最近更新</span>
                <span className="cell c-link">操作</span>
              </div>
              {outputArtifacts.map((d) => (
                <div className="artifact-row" key={d.file_token} role="row">
                  <div className="cell c-name c-name-only" title={d.name}>
                    <span className="name-text">{d.name}</span>
                    <code className="file-token" title="file_token">
                      {d.file_token}
                    </code>
                  </div>
                  <div className="cell c-type">
                    <span className={`type-badge t-${d.type}`}>{d.type}</span>
                  </div>
                  <div className="cell c-time">{fmtTime(d.updated_time)}</div>
                  <div className="cell c-link">{linkCell(d)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
