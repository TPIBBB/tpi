import { useState } from "react";
import { DECISION_LABEL, type DisasterReport, type GovDecision } from "../api/types";

/** 把決策參數排成好讀的文字，例如 price=4、duration=10 */
function formatParams(params: Record<string, unknown>) {
  const entries = Object.entries(params);
  if (entries.length === 0) return "-";
  return entries.map(([k, v]) => `${k}=${String(v)}`).join("、");
}

function DecisionRow({ d }: { d: GovDecision }) {
  return (
    <tr className={d.accepted ? undefined : "rejected"}>
      <td>{d.tick}</td>
      <td>{DECISION_LABEL[d.type] ?? d.type}</td>
      <td>{formatParams(d.params)}</td>
      <td>{d.reason}</td>
      <td>{d.accepted ? "✅ 執行" : `❌ 被拒：${d.rejected_reason ?? ""}`}</td>
    </tr>
  );
}

export function DecisionPanel({ reports }: { reports: DisasterReport[] }) {
  const [hideIdle, setHideIdle] = useState(true);
  if (reports.length === 0) return null;

  return (
    <section>
      <h2>政府決策</h2>
      <label className="hint">
        <input type="checkbox" checked={hideIdle} onChange={(e) => setHideIdle(e.target.checked)} />
        隱藏「不行動」
      </label>
      {reports.map((r) => {
        const shown = hideIdle ? r.decisions.filter((d) => d.type !== "idle") : r.decisions;
        const rejected = r.decisions.filter((d) => !d.accepted).length;
        return (
          <div key={r.disaster_id}>
            <h3>
              天災 {r.disaster_id}（第 {r.window_start}～{r.window_end} 回合評估）：共 {r.decisions.length} 個決策，
              被拒 {rejected} 個
            </h3>
            {shown.length === 0 ? (
              <p className="hint">這次天災政府都選擇不行動</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>回合</th><th>決策</th><th>參數</th><th>理由</th><th>結果</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((d, i) => (
                    <DecisionRow key={`${d.tick}-${i}`} d={d} />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        );
      })}
    </section>
  );
}