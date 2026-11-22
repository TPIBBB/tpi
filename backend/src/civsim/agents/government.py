"""AI 政府的決策與反思。負責人：Agent。引擎（world/engine.py）每天呼叫 decide，評估期結束時呼叫 reflect。

決策：GovernmentObservation → prompt → LLM 回傳 JSON 決策 → 解析成 GovernmentDecision。
  prompt 除了原始觀察，還附上程式從觀察算好的趨勢（漲跌幅、連續上漲天數、最近行動之後的變化），
  小模型（例如 Qwen2.5-7B）自己從一串價格推不出這些。
反思：天災報告 + 目前的記憶 → 新版記憶 Markdown。記憶分兩部分：
  - 天災紀錄：每次天災的結果由程式寫，LLM 不能改，所以不會被寫錯或漏記
  - 教訓：LLM 回傳 worked / failed / try_next 三個清單，由程式組成 Markdown
  LLM 的回答會先檢查（結果有沒有讀對、字數、有沒有照抄舊版），不合格就帶著問題重問一次；
  還是不合格就保留舊的教訓，但這次的天災紀錄照樣寫進去。
  連不上 LLM（網路、額度）時回傳 None，引擎記成反思失敗、維持舊版記憶。
政府只能提出決策與新版記憶，兩者都由引擎驗證後才生效。

決策的 LLM 呼叫失敗或回應無法解析時，改用 fallback（預設是不行動），避免整個模擬中斷。

省呼叫次數（call_policy="on_change"）：狀況和上次呼叫 LLM 時差不多，就沿用上次的決策、不呼叫 LLM。
上次決策被拒絕、記憶剛更新、或已經連續 max_skip_days 天沒呼叫時，一定會呼叫。跳過的天也會記一筆 LLMCall（used_llm=False）。
每次呼叫都記錄在 LLMGovernment.calls（含 prompt、回應、token 用量），確保實驗可重現。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from typing import Any, Protocol

from civsim.agents.llm_client import LLMCall, LLMClient, ParseError, extract_json, make_client
from civsim.config import GovernmentConfig
from civsim.world.government import (
    GOV_ID, GovernmentActionType, GovernmentDecision, GovernmentObservation,
)

# v4：記憶字數上限、價格歷史；v5：說明價格上限的日子不算成功、反思報告拿掉均衡區間
# v6：決策附上程式算好的趨勢與最近行動的效果、先寫 situation 再決定、行動分類；
#     反思改成程式記錄天災結果 + LLM 只寫三個教訓清單、報告改成中文摘要
GOV_PROMPT_VERSION = "gov-v6"
CALL_POLICIES = ("always", "on_change")
HUNGRY_TOLERANCE = 0.1  # on_change：挨餓人數的變化在人口的 10% 以內算沒變

# 給 LLM 看的決策說明，對照 docs/society.md「政府的行動」與 world/government.py 的參數檢查。
# 實際可用的決策以引擎的 GovernmentActionType 為準；沒有說明的決策只會列出名稱。
_TARGET = '"target_type": 平民類型（選填，不填 = 所有人）'
DECISION_DOCS = {
    "idle": "{}，今天不行動",
    "distribute_food": f'{{"qty_per_person": 正數, "only_hungry": 布林（選填）, {_TARGET}}}，從儲備發糧給平民',
    "release_reserve": '{"qty": 正數, "price": 最低願賣價（選填，不填 = 市價）}，把儲備掛賣單到市場，增加預算',
    "buy_reserve": '{"qty": 正數, "price": 最高願買價（選填，不填 = 習慣價格 × 2）}，用預算向市場買糧，會推高糧價',
    "import_food": '{"qty": 正數（每天最多 100）}，以習慣價格 × 1.5 向鎮外進口糧食到儲備',
    "cash_transfer": f'{{"per_person": 正數, "only_hungry": 布林（選填）, {_TARGET}}}，用預算發錢給平民',
    "price_cap": '{"price": 正數, "duration": 天數（整數）}，設定糧價上限',
    "collect_tax": f'{{"rate": 0 到 1 的比例, {_TARGET}}}，收取平民持有貨幣的比例到預算',
    "issue_currency": '{"amount": 正數}，發行新貨幣到預算，習慣價格會隨貨幣量上升',
    "seize_food": f'{{"rate": 0 到 1 的比例, {_TARGET}}}，徵收平民存糧的比例到儲備',
    "execute_citizen": '{"target": 平民 id}，處決一位平民',
}

# 依「對糧食與價格的作用」分類列出行動。只說明機制，不建議天災時該用哪一個。
# 不在任何分類裡的行動（例如引擎新增的）列在「其他」。
ACTION_GROUPS = (
    ("增加糧食：讓平民直接拿到糧食，或讓市場上的糧食變多", ("distribute_food", "release_reserve", "import_food")),
    ("幫平民買得起糧食（不會增加糧食）", ("cash_transfer",)),
    ("壓住價格（不會增加糧食；糧食不夠時，挨餓不會因為價格被壓住而減少）", ("price_cap",)),
    ("增加政府的預算或儲備（各有副作用，見說明）", ("buy_reserve", "collect_tax", "issue_currency", "seize_food")),
)

DEFAULT_MEMORY_MAX_CHARS = GovernmentConfig.memory_max_chars  # 和引擎的檢查（world/engine.py）用同一個預設值
REFLECT_ATTEMPTS = 2  # 反思回答不合格時，帶著問題重問；總共最多問幾次


class GovernmentController(Protocol):
    """政府的「大腦」，引擎與政府之間唯一的介面。"""

    def decide(self, obs: GovernmentObservation) -> list[GovernmentDecision]: ...

    def reflect(self, report: dict, memory: str) -> str | None:
        """看完一次天災的報告後，回傳新版記憶；不修改時回傳 None。"""
        ...


# ---- 決策 prompt ----
def _decision_list(action_types: Iterable[GovernmentActionType]) -> str:
    values = [a.value for a in action_types]
    lines: list[str] = []
    listed: set[str] = set()
    for title, members in ACTION_GROUPS:
        items = [v for v in values if v in members]  # 照引擎的順序
        if items:
            lines.append(f"【{title}】")
            lines += [f"- {v}：{DECISION_DOCS.get(v, '參數待補')}" for v in items]
            listed.update(items)
    rest = [v for v in values if v not in listed]
    if rest:
        lines.append("【其他】")
        # 沒有說明的決策也要列出名稱，引擎新增決策時政府才用得到
        lines += [f"- {v}：{DECISION_DOCS.get(v, '參數待補')}" for v in rest]
    return "\n".join(lines)


def build_system_prompt(action_types: Iterable[GovernmentActionType] = GovernmentActionType) -> str:
    return f"""你是虛擬小鎮的政府，負責治理一群平民並應對天災。

你的目標：天災會讓糧食（food）減產、價格上漲。天災結束後有一段固定天數的評估期，評估期內要讓糧價回到正常的均衡水準，
而且回到之後要一直維持到評估期結束，中途又跑出正常範圍就算失敗。
價格被你設的價格上限（price_cap）壓住的日子，不算回到正常水準：上限只能暫時壓住成交價，不能代替市場本身恢復。

你要知道的事：
- 一天就是一回合。你每天只能做一個決策，不想行動就回傳 idle 或空清單；多送的決策會被拒絕。
- 沒有天災預報，也不會有人告訴你天災何時開始或結束，請從價格、挨餓與死亡人數的變化自己判斷。
- 你看得到昨天的成交價、這次週期（上次重置以來）每天的成交價（price_history）、自己的預算與儲備、人口、死亡與挨餓人數、平民的 id 與類型、目前的價格上限，看不到平民各自的存糧與存款。
- 「程式幫你算好的趨勢」和「你最近的決策與之後的變化」是程式從上面這些資料算出來的，沒有額外的資訊。
- 沒有人會告訴你正常的糧價是多少，請從價格歷史與你的記憶自己判斷。
- 平民類型：producer_trader（種糧並賣掉多的）、producer_self（自耕自食，不買賣）、consumer（打工，用錢買糧）、dependent（不生產也不買賣，存糧吃完只能靠政府發糧）。
- 平民依固定規則工作與交易，有一部分人不會服從你的政策。
- 你的決策由模擬引擎驗證；資源不足或違反社會規則的決策會被拒絕，拒絕理由會出現在近期決策裡。
- 每次天災的評估期結束後，社會會被重置回天災前的狀態，只有你的記憶會留下來。

請遵守「社會規則」，並參考「你的記憶」裡從過去天災學到的經驗。

可用決策與參數（依作用分類）：
{_decision_list(action_types)}

決定之前，先在 situation 用兩三句話寫下你的判斷：
1. 從趨勢看，現在可能正在發生天災嗎？
2. 你最近的行動之後，價格和挨餓人數有沒有改善？
3. 所以今天該做什麼？

只回傳一個 JSON 物件，不要有其他文字：
{{"situation": "兩三句判斷", "actions": [{{"type": "...", "params": {{...}}, "reason": "一句話說明理由"}}]}}
不需要行動時 actions 填空清單：{{"situation": "...", "actions": []}}"""


def _pct(new: float, old: float) -> str:
    if not old:
        return "—"
    return f"{(new - old) / abs(old) * 100:+.0f}%"


def _streak(prices: list[float], rising: bool) -> int:
    """最後連續上漲（或下跌）了幾天。"""
    n = 0
    for prev, cur in zip(reversed(prices[:-1]), reversed(prices)):
        if (cur > prev) if rising else (cur < prev):
            n += 1
        else:
            break
    return n


def build_trends(obs: GovernmentObservation, hungry_history: dict[int, int] | None = None) -> str:
    """從政府看得到的資料算出趨勢，寫成幾行中文。只做算術，不加入政府看不到的資訊（例如均衡區間）。"""
    hungry_history = hungry_history or {}
    prices = [h["price"] for h in obs.price_history]
    lines = []
    if prices:
        last = prices[-1]
        changes = [f"比 {n} 天前 {_pct(last, prices[-1 - n])}" for n in (1, 3, 7) if len(prices) > n]
        if len(prices) > 1:
            # 和週期內最低價比，不和第一天比：第一個週期開頭的價格還在從初始狀態往下收斂，第一天不是正常水準
            changes.append(f"比這次週期最低價 {_pct(last, min(prices))}")
        lines.append(f"- 這次週期第 {len(prices)} 天，昨天成交價 {last:.2f}" + ("；" + "、".join(changes) if changes else ""))
        for rising, word in ((True, "上漲"), (False, "下跌")):
            n = _streak(prices, rising)
            if n >= 2:
                lines.append(f"- 價格已經連續{word} {n} 天")
        lines.append(f"- 這次週期最低價 {min(prices):.2f}、最高價 {max(prices):.2f}")
    cap = obs.price_cap
    if cap:
        until = f"（到第 {cap['until']} 天）" if cap.get("until") is not None else ""
        if prices and prices[-1] >= cap["price"] - 1e-6:
            lines.append(f"- 昨天成交價等於你的價格上限 {cap['price']}{until}：這是被上限壓住的價格，不代表市場已經恢復")
        else:
            lines.append(f"- 你的價格上限 {cap['price']} 生效中{until}，昨天成交價低於上限")
    past = [f"{n} 天前 {hungry_history[obs.tick - n]}" for n in (1, 3) if obs.tick - n in hungry_history]
    lines.append(f"- 挨餓人數 {obs.hungry}" + (f"（{'、'.join(past)}）" if past else "") + f"；這次週期死亡 {obs.deaths} 人")
    return "\n".join(lines)


def _params_text(params: dict | None) -> str:
    return json.dumps(params or {}, ensure_ascii=False, sort_keys=True)


def build_recent_decisions(obs: GovernmentObservation, hungry_history: dict[int, int] | None = None,
                           max_groups: int = 6) -> str:
    """近期決策：連續相同的決策合併成一行，並附上執行之後價格與挨餓人數的變化，讓政府看得到自己行動的效果。"""
    hungry_history = hungry_history or {}
    price_at = {h["tick"]: h["price"] for h in obs.price_history}
    now_price = obs.price_history[-1]["price"] if obs.price_history else obs.prices.get("food")
    groups: list[dict[str, Any]] = []
    for d in obs.recent_decisions:
        tick = d.get("tick", 0)
        key = (d.get("type"), _params_text(d.get("params")), d.get("accepted", True), d.get("rejected_reason"))
        if groups and groups[-1]["key"] == key and groups[-1]["last"] == tick - 1:
            groups[-1]["last"] = tick
            groups[-1]["n"] += 1
        else:
            groups.append({"key": key, "first": tick, "last": tick, "n": 1})
    if not groups:
        return "（這次週期還沒有決策）"

    lines = [f"- （更早的 {len(groups) - max_groups} 組省略）"] if len(groups) > max_groups else []
    for g in groups[-max_groups:]:
        type_, params, accepted, rejected_reason = g["key"]
        days = f"第 {g['first']} 天" if g["first"] == g["last"] else f"第 {g['first']}–{g['last']} 天"
        text = f"- {days} {type_}" + (f" {params}" if params != "{}" else "") + (f" ×{g['n']}" if g["n"] > 1 else "")
        if not accepted:
            text += f"：被拒絕，理由：{rejected_reason}"
        elif type_ != "idle":
            effects = []
            before = price_at.get(g["first"] - 1)  # 做決策那天看到的價格
            if before is not None and now_price is not None:
                effects.append(f"價格 {before:.2f} → 現在 {now_price:.2f}（{_pct(now_price, before)}）")
            hungry_before = hungry_history.get(g["first"])
            if hungry_before is not None:
                effects.append(f"挨餓 {hungry_before} → 現在 {obs.hungry} 人")
            if effects:
                text += "：之後" + "，".join(effects)
        lines.append(text)
    return "\n".join(lines)


def build_prompt(obs: GovernmentObservation, hungry_history: dict[int, int] | None = None) -> str:
    """把觀察整理成幾段：數字用 JSON，趨勢與近期決策寫成中文，價格歷史壓成一行，規則用清單，記憶保留 Markdown。

    hungry_history：{tick: 當天看到的挨餓人數}，由 LLMGovernment 自己記錄（觀察裡只有今天的人數）。
    """
    state = {
        "tick": obs.tick,
        "prices": obs.prices,
        "budget": obs.budget,
        "food_reserve": obs.food_reserve,
        "population": obs.population,
        "deaths": obs.deaths,
        "hungry": obs.hungry,
        "price_cap": obs.price_cap,
        "citizens": obs.citizens,
    }
    # 沒有的資訊不要給 LLM 看，避免它猜測 None 的意思
    state = {k: v for k, v in state.items() if v is not None}
    rules = "\n".join(f"- {r}" for r in obs.rules) or "（目前沒有社會規則）"
    memory = (obs.memory or "").strip() or "（目前沒有記憶）"
    # 價格歷史單獨一段、壓成一行：整個週期可能有近 50 天，用 indent 排版會佔掉上百行
    history = json.dumps([[h["tick"], h["price"]] for h in obs.price_history], separators=(",", ":"))
    return (
        f"## 今天的狀態\n```json\n{json.dumps(state, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 程式幫你算好的趨勢\n{build_trends(obs, hungry_history)}\n\n"
        f"## 你最近的決策與之後的變化\n{build_recent_decisions(obs, hungry_history)}\n\n"
        f"## 這次週期每天的成交價（price_history，[tick, 成交價]，由舊到新）\n{history}\n\n"
        f"## 社會規則\n{rules}\n\n"
        f"## 你的記憶\n{memory}\n"
    )


# ---- 記憶的格式 ----
MEMORY_TITLE = "# 政府記憶"
RECORDS_HEADING = "## 天災紀錄（程式依評估結果自動記錄）"
LESSON_SECTIONS = (("worked", "## 有效的做法"), ("failed", "## 無效的做法"), ("try_next", "## 下次要試"))
KIND_NAMES = {"drought": "乾旱"}

# 天災報告裡不能給政府看的欄位：均衡區間是後台判定成功用的標準，給了等於先看答案（11/20 決定）。
# 反思 prompt 是由下面的函式挑欄位寫成中文，本來就不會用到這些欄位；留著這個清單當作明確的約定。
HIDDEN_REPORT_FIELDS = ("band",)


def split_memory(memory: str | None) -> tuple[list[str], str]:
    """把記憶拆成 (天災紀錄的每一行, 其餘的教訓文字)。不是這個格式的記憶（例如 initial_memory）整份當作教訓。"""
    records: list[str] = []
    rest: list[str] = []
    in_records = False
    for line in (memory or "").splitlines():
        s = line.strip()
        if s.startswith("## 天災紀錄"):
            in_records = True
            continue
        if in_records and s.startswith("#"):
            in_records = False
        if in_records:
            if s.startswith("- ") and "還沒有天災" not in s:
                records.append(s)
            continue
        if s == MEMORY_TITLE:
            continue
        rest.append(line)
    return records, "\n".join(rest).strip()


def render_memory(records: list[str], lessons: str) -> str:
    parts = [MEMORY_TITLE, "", RECORDS_HEADING, *(records or ["- （還沒有天災）"])]
    if lessons.strip():
        parts += ["", lessons.strip()]
    return "\n".join(parts) + "\n"


def render_lessons(data: dict) -> str:
    out: list[str] = []
    for key, heading in LESSON_SECTIONS:
        items = data.get(key) or []
        out += [heading, *([f"- {i}" for i in items] or ["- （還沒有）"]), ""]
    return "\n".join(out).strip()


def lesson_budget(records: list[str], max_chars: int) -> int:
    """扣掉標題與天災紀錄之後，教訓還能用幾個字元。"""
    return max(0, max_chars - len(render_memory(records, "")) - 2)  # 2 = 紀錄和教訓之間的空行


def trim_records(records: list[str], max_chars: int) -> list[str]:
    """天災紀錄最多佔記憶的一半，超過就刪最舊的，教訓才一直有空間寫。"""
    records = list(records)
    while len(records) > 1 and len(render_memory(records, "")) > max_chars // 2:
        records.pop(0)
    return records


def _tick_ranges(ticks: list[int]) -> str:
    ticks = sorted(set(t for t in ticks if isinstance(t, int)))
    if not ticks:
        return ""
    spans, start, prev = [], ticks[0], ticks[0]
    for t in ticks[1:] + [None]:
        if t is not None and t == prev + 1:
            prev = t
            continue
        spans.append(str(start) if start == prev else f"{start}–{prev}")
        if t is not None:
            start = prev = t
    return "第 " + "、".join(spans) + " 天"


def _decision_counts(decisions: list[dict]) -> list[tuple[str, list[dict]]]:
    by_type: dict[str, list[dict]] = {}
    for d in decisions:
        by_type.setdefault(str(d.get("type", "?")), []).append(d)
    return sorted(by_type.items(), key=lambda kv: -len(kv[1]))


def summarize_decisions(decisions: list[dict]) -> list[str]:
    """依類型統計這次週期的決策：次數、哪幾天、用過的參數、被拒絕的理由。逐筆列出對小模型來說太長也太雜。"""
    lines = []
    for type_, ds in _decision_counts(decisions):
        line = f"- {type_}：{len(ds)} 次（{_tick_ranges([d.get('tick') for d in ds])}）"
        params = list(dict.fromkeys(_params_text(d.get("params")) for d in ds if d.get("accepted", True)))
        params = [p for p in params if p != "{}"]
        if params:
            line += "，參數：" + "；".join(params[:3]) + ("等" if len(params) > 3 else "")
        rejected = [d for d in ds if d.get("accepted") is False]
        if rejected:
            reasons = list(dict.fromkeys(str(d.get("rejected_reason") or "未說明") for d in rejected))
            line += f"，其中 {len(rejected)} 次被拒絕，理由：" + "；".join(reasons[:3])
        lines.append(line)
    return lines


def _result_text(report: dict) -> str:
    success = report.get("success")
    if success is True:
        day = report.get("recovered_day")
        return f"成功：評估期第 {day} 天回到正常並維持到結束" if day is not None else "成功"
    if success is False:
        if report.get("left_band_after_recovery"):
            return "失敗：價格回到正常後又跑出正常範圍"
        return "失敗：評估期內價格沒有回到正常水準"
    return "結果不明"


def _expected_result(report: dict) -> str | None:
    return {True: "成功", False: "失敗"}.get(report.get("success"))


def disaster_record(report: dict) -> str:
    """一次天災的結果寫成一行，由程式產生，放進記憶的天災紀錄。"""
    kind = KIND_NAMES.get(report.get("kind"), report.get("kind") or "天災")
    parts = [_result_text(report)]
    if report.get("hungry_person_days") is not None:
        parts.append(f"挨餓 {report['hungry_person_days']} 人天")
    if report.get("deaths") is not None:
        parts.append(f"死亡 {report['deaths']} 人")
    if report.get("peak_price") is not None:
        parts.append(f"最高價 {report['peak_price']:.2f}")
    window = report.get("window_prices") or []
    if window and report.get("capped_days") is not None:
        parts.append(f"評估期 {report['capped_days']}/{len(window)} 天被價格上限壓住")
    decisions = report.get("decisions")
    if decisions is not None:
        counts = "、".join(f"{t}×{len(ds)}" for t, ds in _decision_counts(decisions))
        parts.append(f"決策：{counts or '沒有'}")
    return f"- 第 {report.get('disaster_id', '?')} 次（{kind}）：" + "；".join(parts)


# ---- 反思 prompt ----
def build_reflect_system_prompt(max_chars: int = DEFAULT_MEMORY_MAX_CHARS) -> str:
    """反思的 system prompt。整份記憶的字數上限要寫進 prompt：超過上限的記憶會被引擎拒絕。"""
    return f"""你是虛擬小鎮的政府。一次天災的評估期剛結束，請根據這次的結果整理教訓。

你的記憶分兩部分：
1. 天災紀錄：每次天災的結果由程式自動記錄，你不用寫，也不能改。
2. 教訓：由你整理，下次天災時你會讀到。社會會被重置回天災前的狀態，教訓是你唯一能留下的經驗。

整理教訓的步驟：
1. 先確認這次是「成功」還是「失敗」，照抄報告的結果寫在 result
2. 在 analysis 用一兩句話說明為什麼會有這個結果
3. 再整理三個清單：
   - worked（有效的做法）：在成功的天災中用過、而且有幫助的做法。還沒成功過就留空
   - failed（無效的做法）：失敗時用的主要做法、被拒絕的決策（附上被拒絕的理由）
   - try_next（下次要試）：還沒試過、或只成功過一次（可能只是運氣）的做法

注意：
- 失敗代表這次的做法不夠好，不要把失敗時用的做法寫進 worked
- 價格被你的價格上限壓住的日子不算恢復：上限只能壓住價格，不會增加糧食
- 每一條註明依據哪幾次天災，例如「（第1、2次）」
- 舊的教訓和這次的結果矛盾時，要修改或刪掉
- 社會規則不屬於記憶，記憶不能改變規則，也不能用來繞過規則
- 整份記憶（含天災紀錄）最多 {max_chars} 個字元；教訓可用的字數寫在報告最後

只回傳一個 JSON 物件，不要有其他文字。三個清單都是完整的新版本（會取代舊的教訓），每條一句話：
{{"result": "成功" 或 "失敗", "analysis": "一兩句話", "worked": ["..."], "failed": ["..."], "try_next": ["..."]}}"""


REFLECT_SYSTEM_PROMPT = build_reflect_system_prompt()


def build_reflection_prompt(report: dict, memory: str, max_chars: int = DEFAULT_MEMORY_MAX_CHARS) -> str:
    """反思的使用者訊息：先講結論的中文摘要。不放均衡區間（HIDDEN_REPORT_FIELDS）。"""
    records, lessons = split_memory(memory)
    records = trim_records(records + [disaster_record(report)], max_chars)
    kind = KIND_NAMES.get(report.get("kind"), report.get("kind") or "天災")
    lines = [f"## 這次天災（第 {report.get('disaster_id', '?')} 次，{kind}）的結果", f"- 結果：{_result_text(report)}"]
    window = report.get("window_prices") or []
    if window:
        span = ""
        if report.get("window_start") is not None and report.get("window_end") is not None:
            span = f"（第 {report['window_start']}–{report['window_end']} 天）"
        lines.append(f"- 評估期{span}每天的成交價：" + "、".join(f"{p:.2f}" for p in window))
        if report.get("capped_days"):
            lines.append(f"- 評估期 {len(window)} 天裡，有 {report['capped_days']} 天價格是被你的價格上限壓住的，這些天不算恢復")
    facts = []
    if report.get("peak_price") is not None:
        facts.append(f"這次週期最高價 {report['peak_price']:.2f}")
    if report.get("hungry_person_days") is not None:
        facts.append(f"挨餓 {report['hungry_person_days']} 人天（每天挨餓人數的加總）")
    if report.get("deaths") is not None:
        facts.append(f"死亡 {report['deaths']} 人")
    if facts:
        lines.append("- " + "；".join(facts))
    decisions = summarize_decisions(report.get("decisions") or [])
    lines += ["", "## 你這次週期的決策（依類型統計）", *(decisions or ["- 沒有決策"])]
    lines += ["", "## 所有天災的紀錄（程式記錄，包含這一次）", *records]
    lines += ["", f"## 你目前的教訓（{len(lessons)} 字）", lessons or "（目前沒有教訓）"]
    lines += ["", f"新版教訓（三個清單組成 Markdown 後）最多 {lesson_budget(records, max_chars)} 個字元。"]
    return "\n".join(lines)


def _feedback(problems: list[tuple[str, str]]) -> str:
    return "\n\n## 你上一次的回答有問題，請修正後重新回答\n" + "\n".join(f"- {m}" for _, m in problems)


# ---- 解析 ----
def parse_decisions(
    text: str, max_decisions: int | None = None
) -> tuple[list[GovernmentDecision], list[str]]:
    """回傳 (決策清單, 被略過的項目說明)。situation 只是讓 LLM 先想清楚，不影響決策（原文留在 LLMCall.response）。

    個別格式錯誤的決策會被略過，其餘照常執行；整份回應都無法使用時丟出 ParseError。
    這裡只檢查格式，決策是否合法（資源、規則）仍由引擎驗證。
    """
    data = extract_json(text)
    if isinstance(data, list):
        data = {"actions": data}
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise ParseError("回應缺少 actions 陣列")

    decisions: list[GovernmentDecision] = []
    skipped: list[str] = []
    for i, item in enumerate(data["actions"]):
        if not isinstance(item, dict):
            skipped.append(f"#{i} 不是物件")
            continue
        try:
            type_ = GovernmentActionType(str(item.get("type", "")).strip().lower())
        except ValueError:
            skipped.append(f"#{i} 未知的決策類型 {item.get('type')!r}")
            continue
        params = item.get("params") or {}
        if not isinstance(params, dict):
            skipped.append(f"#{i} params 不是物件")
            continue
        decisions.append(GovernmentDecision(type_, params, str(item.get("reason", ""))))

    if not decisions and skipped:
        raise ParseError("沒有任何可用的決策：" + "；".join(skipped))
    if max_decisions is not None and len(decisions) > max_decisions:
        skipped.append(f"一天最多 {max_decisions} 個決策，截掉 {len(decisions) - max_decisions} 個")
        decisions = decisions[:max_decisions]
    return decisions, skipped


def parse_memory(text: str) -> str | None:
    """舊格式（gov-v5 以前）：LLM 直接回傳整份記憶。保留給遠端政府或舊的工具使用。"""
    data = extract_json(text)
    if not isinstance(data, dict) or "memory" not in data:
        raise ParseError("回應缺少 memory 欄位")
    memory = data["memory"]
    if memory is not None and not isinstance(memory, str):
        raise ParseError("memory 必須是字串或 null")
    return memory


def parse_lessons(text: str) -> dict:
    """解析反思回答：{"result", "analysis", "worked", "failed", "try_next"}。清單裡的項目去掉開頭的 - 號。"""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ParseError("回應不是 JSON 物件")
    if not any(key in data for key, _ in LESSON_SECTIONS):
        raise ParseError("回應缺少 worked、failed、try_next")
    out: dict[str, Any] = {"result": str(data.get("result") or "").strip(),
                           "analysis": str(data.get("analysis") or "").strip()}
    for key, _ in LESSON_SECTIONS:
        items = data.get(key)
        if items is None:
            items = []
        elif isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            raise ParseError(f"{key} 必須是清單")
        out[key] = [s for s in (str(i).strip().lstrip("-•* ").strip() for i in items) if s]
    return out


def _normalize_result(text: str) -> str:
    if any(w in text for w in ("失敗", "不成功", "未成功", "沒有成功", "沒成功")):
        return "失敗"
    return "成功" if "成功" in text else ""


# 嚴重的問題：重問後仍然存在就不採用這份教訓；輕微的問題：重問後仍然存在就照樣採用
SERIOUS_PROBLEMS = ("result", "too_long")


def check_lessons(data: dict, rendered: str, report: dict, old_lessons: str, budget: int) -> list[tuple[str, str]]:
    """檢查反思回答，回傳 [(問題代碼, 給 LLM 看的說明)]；空清單表示沒問題。"""
    problems = []
    expected = _expected_result(report)
    if expected and _normalize_result(data["result"]) != expected:
        problems.append(("result", f"報告的結果是「{expected}」，你的 result 寫的是「{data['result']}」。"
                                   "請重新看一次結果，教訓要根據正確的結果來寫"))
    if len(rendered) > budget:
        problems.append(("too_long", f"教訓共 {len(rendered)} 字，超過上限 {budget} 字。請合併重複的條目、刪掉不重要的"))
    if not any(data[key] for key, _ in LESSON_SECTIONS):
        problems.append(("empty", "三個清單都是空的。請至少寫下一條這次學到的事"))
    elif old_lessons and rendered.strip() == old_lessons.strip():
        problems.append(("same", f"教訓和舊版一模一樣，第 {report.get('disaster_id', '?')} 次天災的結果沒有反映進去。"
                                 "請根據這次的結果修改、補充或刪除"))
    return problems


# ---- 政府 ----
class NoGovernment:
    """完全不介入：LLM 失敗時的預設 fallback，也能用來觀察天災本身的影響。"""

    def decide(self, obs: GovernmentObservation) -> list[GovernmentDecision]:
        return []

    def reflect(self, report: dict, memory: str) -> str | None:
        return None


class LLMGovernment:
    def __init__(
        self,
        client: LLMClient,
        fallback: GovernmentController | None = None,
        max_decisions: int | None = None,  # 一天最多幾個決策；None 表示不限制
        call_policy: str = "always",
        price_tolerance: float = 0.05,
        max_skip_days: int = 5,
        memory_max_chars: int = DEFAULT_MEMORY_MAX_CHARS,  # 要和引擎的 government.memory_max_chars 一致
        reflect_attempts: int = REFLECT_ATTEMPTS,
    ):
        if call_policy not in CALL_POLICIES:
            raise ValueError(f"未知的 call_policy: {call_policy}，可用：{CALL_POLICIES}")
        self.client = client
        self.fallback = fallback or NoGovernment()
        self.max_decisions = max_decisions
        self.call_policy = call_policy
        self.price_tolerance = price_tolerance
        self.max_skip_days = max_skip_days
        self.memory_max_chars = memory_max_chars
        self.reflect_attempts = max(1, reflect_attempts)
        self.reflect_system_prompt = build_reflect_system_prompt(memory_max_chars)
        self.calls: list[LLMCall] = []
        self.last_reflect_error: str | None = None  # 上一次反思沒採用 LLM 教訓的原因
        self._last_obs: GovernmentObservation | None = None  # 上次成功呼叫 LLM 時的觀察
        self._last_decisions: list[GovernmentDecision] = []
        self._skipped = 0  # 已經連續幾天沒呼叫
        self._hungry: dict[int, int] = {}  # 這次週期每天看到的挨餓人數，給趨勢與「行動之後的變化」用
        self._history_len = 0

    def decide(self, obs: GovernmentObservation) -> list[GovernmentDecision]:
        self._track_hungry(obs)
        if self.call_policy == "on_change":
            same_as = self._unchanged_since(obs)
            if same_as is not None:
                self._skipped += 1
                self.calls.append(LLMCall(
                    obs.tick, GOV_ID, GOV_PROMPT_VERSION, self.client.model, used_llm=False,
                    trigger=f"skip：狀況和第 {same_as} 天差不多，沿用當天的決策"))
                return [replace(d, replayed=True) for d in self._last_decisions]

        record = self._new_call(obs.tick, "decide", build_prompt(obs, self._hungry))
        self._skipped = 0
        try:
            record.response = self._complete(build_system_prompt(), record.prompt, record)
            decisions, record.skipped = parse_decisions(record.response, self.max_decisions)
        except Exception as e:  # 網路、額度、格式錯誤都不能讓模擬中斷
            record.error = f"{type(e).__name__}: {e}"
            record.used_fallback = True
            self._last_obs = None  # 失敗的那天不算數，隔天一定重新呼叫
            return self.fallback.decide(obs)
        self._last_obs, self._last_decisions = obs, decisions
        return decisions

    def _track_hungry(self, obs: GovernmentObservation) -> None:
        """觀察裡只有今天的挨餓人數，所以自己記下每天的人數。價格歷史變短代表社會重置、進入新週期，就清空。"""
        if len(obs.price_history) < self._history_len:
            self._hungry.clear()
        self._history_len = len(obs.price_history)
        self._hungry[obs.tick] = obs.hungry

    def _unchanged_since(self, obs: GovernmentObservation) -> int | None:
        """狀況和上次呼叫 LLM 時差不多就回傳那一天的 tick，否則回傳 None（要呼叫）。"""
        last = self._last_obs
        if last is None or self._skipped >= self.max_skip_days:
            return None
        recent = obs.recent_decisions[-1] if obs.recent_decisions else None
        if recent is not None and recent.get("tick") == obs.tick - 1 and not recent.get("accepted", True):
            return None  # 昨天的決策被拒絕，要讓 LLM 看到理由
        old, new = last.prices.get("food"), obs.prices.get("food")
        if old is None or new is None or abs(new - old) > self.price_tolerance * max(abs(old), 1e-9):
            return None
        # 挨餓人數每天會小幅跳動（例如只發一天份的糧），差距在人口的 10%（至少 2 人）以內算沒變
        if abs(obs.hungry - last.hungry) > max(2, round(HUNGRY_TOLERANCE * max(obs.population, 1))):
            return None
        same = (last.deaths, last.population, last.price_cap, last.rules, last.memory) == \
               (obs.deaths, obs.population, obs.price_cap, obs.rules, obs.memory)
        return last.tick if same else None

    def reflect(self, report: dict, memory: str) -> str | None:
        """回傳新版記憶：天災紀錄一定加上這一次，教訓用 LLM 的新版本；LLM 一直答不好就保留舊的教訓。"""
        self.last_reflect_error = None
        self._last_obs = None  # 反思後記憶改了，下一天一定重新呼叫
        records, old_lessons = split_memory(memory)
        records = trim_records(records + [disaster_record(report)], self.memory_max_chars)
        budget =lesson_budget(records, self.memory_max_chars)
        base = build_reflection_prompt(report, memory, self.memory_max_chars)
        tick = report.get("window_end", -1)

        lessons: str | None = None
        problems: list[tuple[str, str]] = []
        record: LLMCall | None = None
        for attempt in range(self.reflect_attempts):
            last_try = attempt == self.reflect_attempts - 1
            prompt = base + (_feedback(problems) if problems else "")
            record = self._new_call(tick, "reflect" if attempt == 0 else "reflect-retry", prompt)
            try:
                record.response = self._complete(self.reflect_system_prompt, prompt, record)
                data = parse_lessons(record.response)
            except ParseError as e:
                record.error = f"ParseError: {e}"
                problems = [("format", f"回應格式錯誤（{e}）。請只回傳規定格式的 JSON")]
                continue
            except Exception as e:  # 網路或額度問題：client 已經重試過，不再重問
                # 回傳 None，引擎記成 gov:memory_failed、維持舊版記憶（和 gov-v5 以前一樣），
                # 不寫天災紀錄：基礎設施出錯的 run 應該被看出來，而不是看起來正常
                record.error = self.last_reflect_error = f"{type(e).__name__}: {e}"
                return None
            rendered = render_lessons(data)
            problems = check_lessons(data, rendered, report, old_lessons, budget)
            record.skipped = [f"檢查沒通過：{m}" for _, m in problems]
            if not problems or (last_try and not any(code in SERIOUS_PROBLEMS for code, _ in problems)):
                lessons = rendered
                break

        if lessons is None:  # 天災紀錄照樣寫進去，教訓維持舊版
            self.last_reflect_error = "；".join(m for _, m in problems) or "反思沒有產生教訓"
            if record is not None:
                record.used_fallback = True
            lessons = old_lessons
        return self._fit(records, lessons)

    def _fit(self, records: list[str], lessons: str) -> str:
        """保險：記憶超過字數上限時（例如保留的舊教訓太長），先刪最舊的天災紀錄，還是太長就截短教訓，
        避免整份被引擎拒絕。"""
        records = list(records)
        memory = render_memory(records, lessons)
        while len(memory) > self.memory_max_chars and len(records) > 1:
            records.pop(0)
            memory = render_memory(records, lessons)
        if len(memory) > self.memory_max_chars:
            keep = max(0, lesson_budget(records, self.memory_max_chars) - 1)
            memory = render_memory(records, lessons[:keep] + "…" if keep else "")
        return memory[: self.memory_max_chars]

    def _complete(self, system: str, user: str, record: LLMCall) -> str:
        try:
            return self.client.complete(system, user)
        finally:  # 回應無法使用時也要記下花了多少 token
            record.usage = getattr(self.client, "last_usage", None)

    def _new_call(self, tick: int, trigger: str, prompt: str) -> LLMCall:
        record = LLMCall(tick, GOV_ID, GOV_PROMPT_VERSION, self.client.model,
                         used_llm=True, trigger=trigger, prompt=prompt)
        self.calls.append(record)
        return record


def make_government(fallback: GovernmentController | None = None, **options) -> LLMGovernment:
    """依環境變數建立 AI 政府。沒有設定供應商時直接報錯，不靜靜改用 fallback，
    否則實驗結果會被誤認成 LLM 的決策。
    """
    client = make_client()
    if client is None:
        raise ValueError("尚未設定 LLM_PROVIDER，無法建立 AI 政府")
    return LLMGovernment(client, fallback, **options)
