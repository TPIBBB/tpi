import { PHASE_LABEL, type TickMetrics } from "../api/types";

// TODO(前端組)：改成折線圖（糧價、挨餓人數隨回合變化），並支援比較不同實驗
export function MetricsPanel({ metrics }: { metrics: TickMetrics[] }) {
  return (
    <section>
      <h2>每回合指標</h2>
      <table>
        <thead>
          <tr>
            <th>回合</th><th>天災</th><th>階段</th><th>糧價</th>
            <th>存活</th><th>挨餓</th><th>死亡</th><th>金錢吉尼</th><th>政府儲備</th>
          </tr>
        </thead>
        <tbody>
          {[...metrics].reverse().map((m) => (
            <tr key={m.tick}>
              <td>{m.tick}</td>
              <td>{m.disaster_id ?? "-"}</td>
              <td>{PHASE_LABEL[m.phase] ?? m.phase}</td>
              <td>{m.price.toFixed(2)}</td>
              <td>{m.alive}</td>
              <td>{m.hungry}</td>
              <td>{m.deaths}</td>
              <td>{m.money_gini.toFixed(3)}</td>
              <td>{m.gov_reserve.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}