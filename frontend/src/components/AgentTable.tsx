import type { CitizenView } from "../api/types";

// TODO(前端組)：改成地圖或關係網路圖
export function AgentTable({ citizens }: { citizens: CitizenView[] }) {
  return (
    <section>
      <h2>居民（{citizens.filter((c) => c.alive).length} / {citizens.length} 存活）</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>類型</th><th>食物</th><th>金錢</th><th>連續挨餓天數</th>
          </tr>
        </thead>
        <tbody>
          {citizens.map((c) => (
            <tr key={c.id} className={c.alive ? undefined : "dead"}>
              <td>{c.id}</td>
              <td>{c.type}</td>
              <td>{c.food.toFixed(1)}</td>
              <td>{c.money.toFixed(0)}</td>
              <td>{c.hungry_days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}