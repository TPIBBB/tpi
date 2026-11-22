import type { WorldState } from "../api/types";

export function MarketPanel({ state }: { state: WorldState }) {
  const { market, government, band } = state;
  return (
    <section>
      <h2>市場與政府</h2>
      <table>
        <tbody>
          <tr><td>糧價</td><td>{market.price.toFixed(2)}</td></tr>
          <tr><td>習慣價格</td><td>{market.customary_price.toFixed(2)}</td></tr>
          <tr>
            <td>均衡區間</td>
            <td>{band ? `${band.low.toFixed(2)} ~ ${band.high.toFixed(2)}` : "校準中"}</td>
          </tr>
          <tr><td>價格上限</td><td>{market.price_cap?.toFixed(2) ?? "無"}</td></tr>
          <tr><td>政府預算</td><td>{government.budget.toFixed(0)}</td></tr>
          <tr><td>政府糧食儲備</td><td>{government.food_reserve.toFixed(1)}</td></tr>
        </tbody>
      </table>
    </section>
  );
}