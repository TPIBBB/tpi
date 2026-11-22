import { useEffect, useState } from "react";
import { listRuns, loadRun, parseJsonl, type PublishedRun } from "./api/client";
import { PHASE_LABEL, type EquilibriumBand, type TickMetrics } from "./api/types";
import { AgentTable } from "./components/AgentTable";
import { EventTimeline } from "./components/EventTimeline";
import { MarketPanel } from "./components/MarketPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { PriceChart } from "./components/PriceChart";
import { useWorld } from "./useWorld";

// 靜態網站（GitHub Pages）沒有後端：不讀即時狀態，只看 public/runs/ 裡已發布的實驗
const STATIC = import.meta.env.VITE_STATIC === "1";

function runFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get("run");
}

export default function App() {
  const [offlineFile, setOfflineFile] = useState<string | null>(null);
  const [offlineBand, setOfflineBand] = useState<EquilibriumBand | null>(null);
  const [runs, setRuns] = useState<PublishedRun[]>([]);
  const [runId, setRunId] = useState<string | null>(runFromUrl());
  const [runError, setRunError] = useState<string | null>(null);
  const offline = STATIC || offlineFile !== null || runId !== null;
  const { state, metrics, events, error, setMetrics } = useWorld(offline);

  useEffect(() => {
    listRuns()
      .then((list) => {
        setRuns(list);
        if (STATIC && runId === null && list.length > 0) setRunId(list[0].id);
      })
      .catch(() => setRuns([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (runId === null) return;
    const url = new URL(window.location.href);
    url.searchParams.set("run", runId);
    window.history.replaceState(null, "", url);
    setRunError(null);
    loadRun(runId)
      .then(({ metrics, band }) => {
        setMetrics(metrics);
        setOfflineBand(band);
        setOfflineFile(null);
      })
      .catch((err) => setRunError(`讀不到實驗 ${runId}：${err}`));
  }, [runId, setMetrics]);

  async function loadFile(file: File) {
    setMetrics(parseJsonl<TickMetrics>(await file.text()));
    setOfflineBand(null);
    setOfflineFile(file.name);
    setRunId(null);
  }

  function backToLive() {
    setOfflineFile(null);
    setRunId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    window.history.replaceState(null, "", url);
  }

  const live = !offline;
  return (
    <main>
      <header>
        <h1>Multi-Agent 文明模擬器</h1>
        {runs.length > 0 && (
          <select value={runId ?? ""} onChange={(e) => (e.target.value ? setRunId(e.target.value) : backToLive())}>
            {!STATIC && <option value="">即時（連後端）</option>}
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        )}
        {offlineFile && <span>離線紀錄：{offlineFile}</span>}
        {!STATIC && offline && <button onClick={backToLive}>回到即時</button>}
        {live && state && <span>第 {state.tick} 回合・{PHASE_LABEL[state.phase] ?? state.phase}</span>}
        <label className="file">
          載入離線紀錄（metrics.jsonl）
          <input
            type="file"
            accept=".jsonl"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) loadFile(file);
            }}
          />
        </label>
      </header>

      {runError && <p className="error">{runError}</p>}
      {live && error && <p className="error">連不到後端：{error}（請先在後端執行 civsim serve）</p>}

      {live && state && (
        <div className="grid">
          <MarketPanel state={state} />
          <EventTimeline events={events} />
        </div>
      )}
      <PriceChart metrics={metrics} band={live ? (state?.band ?? null) : offlineBand} />
      <MetricsPanel metrics={metrics} />
      {live && state && <AgentTable citizens={state.citizens} />}
    </main>
  );
}
