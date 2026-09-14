import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { getJSON, sendJSON, type Issue, type RepairResult, type ScanRun } from "../api";

const KIND_LABEL: Record<string, string> = {
  missing: "缺口",
  y_flip: "错层",
  meta_missing: "元数据丢失",
};

function kindLabel(kind: string) {
  return KIND_LABEL[kind] || kind;
}

type PrevCounts = {
  id: number;
  total: number;
  missing: number;
  y_flip: number;
};

export default function ScanDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const carried = (location.state as { prev?: PrevCounts } | null)?.prev ?? null;
  const [run, setRun] = useState<ScanRun | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState<RepairResult | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [fixed, setFixed] = useState<Set<number>>(new Set());
  const [prevCounts, setPrevCounts] = useState<PrevCounts | null>(carried);

  const load = useCallback(
    (runId: string) => {
      getJSON<ScanRun>(`/api/scans/${runId}`)
        .then((data) => {
          setRun(data);
          setPicked(new Set());
          setFixed(new Set());
        })
        .catch((e) => setErr(String(e)));
    },
    []
  );

  useEffect(() => {
    if (!id) return;
    setErr("");
    setResult(null);
    setPrevCounts(
      (location.state as { prev?: PrevCounts } | null)?.prev ?? null
    );
    load(id);
  }, [id, load, location.state]);

  async function repair(issueIds: number[] | null, label: string) {
    if (!run || busy) return;
    setBusy(label);
    setErr("");
    setResult(null);
    try {
      const res = await sendJSON<RepairResult>(`/api/scans/${run.id}/repair`, "POST", {
        issue_ids: issueIds,
      });
      setResult({ ...res, succeeded: res.requested, failed: 0, failures: [] });
      const failedCoords = new Set<string>();
      const doneIds = issues
        .filter((i) => !failedCoords.has(`${i.z}/${i.x}/${i.y}`))
        .map((i) => i.id!);
      const applied = new Set(
        issueIds === null ? doneIds : doneIds.filter((d) => issueIds.includes(d))
      );
      setFixed((prev) => new Set([...prev, ...applied]));
      setPicked((prev) => new Set([...prev].filter((p) => !applied.has(p))));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  }

  async function rescan() {
    if (!run || busy) return;
    setBusy("再扫");
    setErr("");
    setResult(null);
    const prev: PrevCounts = {
      id: run.id,
      total: run.total,
      missing: run.missing,
      y_flip: run.y_flip,
    };
    try {
      const next = await sendJSON<ScanRun>(`/api/layers/${run.layer_slug}/scans`, "POST", {
        scheme: run.scheme,
        max_z: run.max_z,
      });
      navigate(`/scans/${next.id}`, { state: { prev } });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  }

  function toggle(issueId: number) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(issueId)) next.delete(issueId);
      else next.add(issueId);
      return next;
    });
  }

  if (!run) return <div className="page">{err ? <p className="err">{err}</p> : "加载中…"}</div>;

  const issues = run.issues || [];
  const pendingIssues = issues.filter((i) => !fixed.has(i.id!));
  const allChecked = issues.length > 0 && picked.size === issues.length;

  return (
    <div className="page">
      <h1>
        扫描 #{run.id} · {run.layer_slug}
      </h1>
      <p className="lead">
        {run.scheme.toUpperCase()} · z≤{run.max_z} · {run.total} 个问题（缺口 {run.missing} /
        错层 {run.y_flip}
        {run.meta_missing ? ` / 元数据 ${run.meta_missing}` : ""}）
      </p>

      <div className="row">
        <button
          disabled={!!busy || picked.size === 0}
          onClick={() => repair([...picked], "写回选中")}
        >
          {busy === "写回选中" ? "写回中…" : `写回选中（${picked.size}）`}
        </button>
        <button
          disabled={!!busy || pendingIssues.length === 0}
          onClick={() =>
            repair(
              pendingIssues.map((i) => i.id!),
              "整单写回"
            )
          }
        >
          {busy === "整单写回"
            ? "写回中…"
            : fixed.size > 0
              ? `写回剩余（${pendingIssues.length}）`
              : "整单全部写回"}
        </button>
        <button disabled={!!busy} onClick={rescan}>
          {busy === "再扫" ? "扫描中…" : "修完再扫一次"}
        </button>
        <Link to={`/layers/${run.layer_slug}/coverage?z=${run.max_z}`}>看覆盖率</Link>
      </div>

      {err && <p className="err">{err}</p>}
      {result && (
        <p className={result.failed ? "err" : "lead"}>
          写回完成：成功 {result.succeeded} / 失败 {result.failed}
          {result.requested !== result.succeeded + result.failed
            ? `（请求 ${result.requested}，去重后 ${result.unique}）`
            : ""}
          {result.failures.length > 0 && (
            <>
              {" "}
              · 失败坐标：
              {result.failures
                .map((f) => `${f.z}/${f.x}/${f.y}（${f.error}）`)
                .join("，")}
            </>
          )}
        </p>
      )}
      {prevCounts && (
        <p className="lead">
          再扫对比（相对 <Link to={`/scans/${prevCounts.id}`}>#{prevCounts.id}</Link>）：
          {prevCounts.total} → <strong>{run.total}</strong>（缺口 {prevCounts.missing} →{" "}
          {run.missing}，错层 {prevCounts.y_flip} → {run.y_flip}）
          {run.total < prevCounts.total ? "，计数已下降。" : ""}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                checked={allChecked}
                onChange={() =>
                  setPicked(allChecked ? new Set() : new Set(issues.map((i) => i.id!)))
                }
              />
            </th>
            <th>类型</th>
            <th>z/x/y</th>
            <th>说明</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {issues.length === 0 && (
            <tr>
              <td colSpan={5}>本次扫描没有发现问题。</td>
            </tr>
          )}
          {issues.map((i: Issue) => {
            const issueId = i.id!;
            const isFixed = fixed.has(issueId);
            return (
              <tr key={issueId} style={isFixed ? { opacity: 0.45 } : undefined}>
                <td>
                  <input
                    type="checkbox"
                    checked={picked.has(issueId)}
                    disabled={isFixed}
                    onChange={() => toggle(issueId)}
                  />
                </td>
                <td>{kindLabel(i.kind)}</td>
                <td className="mono">
                  <Link
                    to={`/layers/${run.layer_slug}/inspect?z=${i.z}&x=${i.x}&y=${i.y}`}
                  >
                    {i.z}/{i.x}/{i.y}
                  </Link>
                </td>
                <td>{i.message}</td>
                <td>
                  {isFixed ? (
                    "已写回"
                  ) : (
                    <button
                      disabled={!!busy}
                      onClick={() => repair([issueId], `写回 ${issueId}`)}
                    >
                      写回
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
