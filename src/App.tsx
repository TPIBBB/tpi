import { useState } from "react";
import { parseJsonl } from "./api/client";
import { PHASE_LABEL, type TickMetrics } from "./api/types";
import { AgentTable } from "./components/AgentTable";
import { EventTimeline } from "./components/EventTimeline";
import { MarketPanel } from "./components/MarketPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { PriceChart } from "./components/PriceChart";
import { useWorld } from "./useWorld";

export default function App() {
  const [offlineFile, setOfflineFile] = useState<string | null>(null);
  const { state, metrics, events, error, setMetrics } = useWorld(offlineFile !== null);

  async function loadFile(file: File) {
    setMetrics(parseJsonl<TickMetrics>(await file.text()));
    setOfflineFile(file.name);
  }

  return (
    <main>
      <header>
        <h1>Multi-Agent 文明模擬器</h1>
        {offlineFile ? (
          <span>
            離線紀錄：{offlineFile} <button onClick={() => setOfflineFile(null)}>回到即時</button>
          </span>
        ) : (
          state && <span>第 {state.tick} 回合・{PHASE_LABEL[state.phase] ?? state.phase}</span>
        )}
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

      {!offlineFile && error && (
        <p className="error">連不到後端：{error}（請先在後端執行 civsim serve）</p>
      )}

      {!offlineFile && state && (
        <div className="grid">
          <MarketPanel state={state} />
          <EventTimeline events={events} />
        </div>
      )}
      <PriceChart metrics={metrics} band={state?.band ?? null} />
      <MetricsPanel metrics={metrics} />
      {!offlineFile && state && <AgentTable citizens={state.citizens} />}
    </main>
  );
}