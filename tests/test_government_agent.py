import json
from enum import Enum

import pytest

from civsim.agents import LLMGovernment, NoGovernment, make_government
from civsim.agents.government import (
    GOV_PROMPT_VERSION,
    REFLECT_SYSTEM_PROMPT,
    build_prompt,
    build_recent_decisions,
    build_reflection_prompt,
    build_system_prompt,
    build_trends,
    parse_decisions,
    parse_lessons,
    parse_memory,
    render_lessons,
    render_memory,
    split_memory,
)
from civsim.agents.llm import ParseError, ScriptedClient
from civsim.config import GovernmentConfig
from civsim.world.government import GovernmentActionType, GovernmentDecision, GovernmentObservation

IDLE = '{"actions": [{"type": "idle", "reason": "觀望"}]}'
REPORT = {"disaster_id": 1, "window_end": 20, "goals_met": False}


def make_obs(**overrides) -> GovernmentObservation:
    values = {"tick": 3, "prices": {"food": 5.0}, "memory": "# 政府記憶", "rules": ["不能亂殺平民"]}
    return GovernmentObservation(**{**values, **overrides})


# ---- prompt ----
def test_system_prompt_lists_only_actions_the_engine_supports():
    prompt = build_system_prompt()
    assert "idle" in prompt and "execute_citizen" in prompt
    assert "publish_forecast" not in prompt  # 引擎沒有的決策不能讓政府選


def test_system_prompt_follows_engine_action_list():
    class Actions(str, Enum):
        ISSUE_CURRENCY = "issue_currency"
        NEW_POLICY = "new_policy"

    prompt = build_system_prompt(Actions)
    assert "發行新貨幣" in prompt
    assert "new_policy：參數待補" in prompt


def test_prompt_contains_rules_and_memory():
    prompt = build_prompt(make_obs())
    assert "- 不能亂殺平民" in prompt and "# 政府記憶" in prompt


def test_prompt_state_is_json_and_hides_missing_fields():
    prompt = build_prompt(make_obs(budget=500.0, hungry=3, citizens=[{"id": "c01", "type": "consumer"}]))
    state = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
    assert state["tick"] == 3 and state["budget"] == 500.0 and state["hungry"] == 3
    assert state["citizens"] == [{"id": "c01", "type": "consumer"}]
    assert "price_cap" not in state  # 沒有的資訊不給 LLM


def test_prompt_says_when_there_are_no_rules_or_memory():
    prompt = build_prompt(make_obs(rules=[], memory=None))
    assert "目前沒有社會規則" in prompt and "目前沒有記憶" in prompt


def test_system_prompt_states_goal_and_no_forecast():
    prompt = build_system_prompt()
    assert "均衡" in prompt and "沒有天災預報" in prompt and '"actions": []' in prompt


# ---- 解析 ----
def test_parse_decisions_accepts_code_fence_and_extra_text():
    decisions, skipped = parse_decisions(f"好的：\n```json\n{IDLE}\n```")
    assert decisions == [GovernmentDecision(GovernmentActionType.IDLE, {}, "觀望")] and not skipped


def test_parse_decisions_skips_bad_items():
    text = json.dumps({"actions": ["idle", {"type": "fly"}, {"type": "idle", "params": 3}, {"type": "IDLE"}]})
    decisions, skipped = parse_decisions(text)
    assert [d.type for d in decisions] == [GovernmentActionType.IDLE]
    assert len(skipped) == 3


def test_parse_decisions_allows_doing_nothing():
    assert parse_decisions('{"actions": []}') == ([], [])


@pytest.mark.parametrize("text", ["", "not json", '{"actions": "idle"}', '{"actions": [{"type": "fly"}]}'])
def test_parse_decisions_rejects_unusable_response(text):
    with pytest.raises(ParseError):
        parse_decisions(text)


def test_parse_memory():
    assert parse_memory('{"memory": "# 新記憶\\n- 經驗"}') == "# 新記憶\n- 經驗"
    assert parse_memory('{"memory": null}') is None


def test_parse_memory_keeps_code_fence_inside_markdown():
    text = json.dumps({"memory": "# 記憶\n```\n範例\n```\n"}, ensure_ascii=False)
    assert "範例" in parse_memory(text)


@pytest.mark.parametrize("text", ["沒有 JSON", '{"reason": "x"}', '{"memory": 3}'])
def test_parse_memory_rejects_bad_response(text):
    with pytest.raises(ParseError):
        parse_memory(text)


# ---- 決策 ----
def test_decide_uses_llm_and_records_call():
    gov = LLMGovernment(ScriptedClient([IDLE]))
    assert [d.type for d in gov.decide(make_obs())] == [GovernmentActionType.IDLE]
    call = gov.calls[-1]
    assert call.tick == 3 and call.trigger == "decide" and call.response == IDLE
    assert call.model == "scripted" and call.prompt_version == GOV_PROMPT_VERSION
    assert not call.used_fallback and not call.error


def test_decide_sends_system_prompt_and_observation():
    seen = {}

    def respond(system, user):
        seen.update(system=system, user=user)
        return IDLE

    LLMGovernment(ScriptedClient(respond)).decide(make_obs())
    assert seen["system"] == build_system_prompt()
    assert seen["user"] == build_prompt(make_obs())


def test_decide_limits_decisions_per_day():
    text = json.dumps({"actions": [{"type": "idle"}] * 4})
    gov = LLMGovernment(ScriptedClient([text]), max_decisions=2)
    assert len(gov.decide(make_obs())) == 2
    assert "截掉 2 個" in gov.calls[-1].skipped[0]


def test_decide_records_token_usage():
    client = ScriptedClient([IDLE], last_usage={"total_tokens": 42})
    gov = LLMGovernment(client)
    gov.decide(make_obs())
    assert gov.calls[-1].usage == {"total_tokens": 42}


@pytest.mark.parametrize("respond", [["沒有 JSON"], ['{"actions": 3}']])
def test_decide_falls_back_on_bad_response(respond):
    gov = LLMGovernment(ScriptedClient(respond))
    assert gov.decide(make_obs()) == []
    assert gov.calls[-1].used_fallback and gov.calls[-1].error


def test_decide_falls_back_on_client_error():
    def boom(system, user):
        raise TimeoutError("連線逾時")

    class Fallback(NoGovernment):
        def decide(self, obs):
            return [GovernmentDecision(GovernmentActionType.IDLE, {}, "fallback")]

    gov = LLMGovernment(ScriptedClient(boom), fallback=Fallback())
    assert gov.decide(make_obs())[0].reason == "fallback"
    assert "TimeoutError" in gov.calls[-1].error


# ---- 反思 ----
FULL_REPORT = {
    "disaster_id": 2, "kind": "drought", "window_start": 34, "window_end": 41,
    "band": {"low": 1.81, "high": 2.04, "mean": 1.93},
    "success": False, "recovered_day": None, "left_band_after_recovery": False,
    "window_prices": [4.5, 4.5, 4.5, 4.5], "capped_days": 4, "peak_price": 4.5,
    "hungry_person_days": 131, "deaths": 0,
    "decisions": [
        {"tick": 26, "type": "price_cap", "params": {"price": 4.5, "duration": 5}, "accepted": True},
        {"tick": 27, "type": "price_cap", "params": {"price": 4.5, "duration": 5}, "accepted": True},
        {"tick": 28, "type": "idle", "params": {}, "accepted": True},
        {"tick": 29, "type": "execute_citizen", "params": {"target": "c00"}, "accepted": False,
         "rejected_reason": "不能亂殺平民"},
    ],
}


def lessons(result="失敗", **lists):
    body = {"result": result, "analysis": "分析"}
    body.update({k: lists.get(k, []) for k in ("worked", "failed", "try_next")})
    return json.dumps(body, ensure_ascii=False)


def first_memory(*lesson):
    return LLMGovernment(ScriptedClient([lessons(try_next=list(lesson))])).reflect({**FULL_REPORT, "disaster_id": 1}, "")


def test_reflect_records_facts_and_llm_lessons():
    seen = {}

    def respond(system, user):
        seen.update(system=system, user=user)
        return lessons(failed=["只設價格上限沒用（第2次）"], try_next=["發糧給挨餓的人"])

    gov = LLMGovernment(ScriptedClient(respond))
    memory = gov.reflect(FULL_REPORT, "")
    assert seen["system"] == REFLECT_SYSTEM_PROMPT
    assert "- 第 2 次（乾旱）：失敗" in memory and "挨餓 131 人天" in memory and "price_cap×2" in memory
    assert "- 只設價格上限沒用（第2次）" in memory and "- 發糧給挨餓的人" in memory
    assert gov.calls[-1].tick == 41 and gov.calls[-1].trigger == "reflect"


def test_reflection_prompt_is_a_chinese_summary():
    prompt = build_reflection_prompt(FULL_REPORT, "")
    assert prompt.startswith("## 這次天災（第 2 次，乾旱）的結果")
    assert "- 結果：失敗" in prompt and "有 4 天價格是被你的價格上限壓住的" in prompt
    assert '- price_cap：2 次（第 26–27 天），參數：{"duration": 5, "price": 4.5}' in prompt
    assert "其中 1 次被拒絕，理由：不能亂殺平民" in prompt


def test_reflection_prompt_hides_equilibrium_band():
    """均衡區間是後台的評分標準，不能透過反思報告讓政府看到（試跑時曾被寫進記憶）。"""
    prompt = build_reflection_prompt(FULL_REPORT, "")
    assert "1.81" not in prompt and "2.04" not in prompt and "1.93" not in prompt
    assert "band" in FULL_REPORT  # 引擎保存的報告不受影響


def test_records_accumulate_and_lessons_are_replaced():
    gov = LLMGovernment(ScriptedClient([lessons(try_next=["a"]), lessons(result="成功", worked=["b"])]))
    first = gov.reflect({**FULL_REPORT, "disaster_id": 1}, "")
    second = gov.reflect({**FULL_REPORT, "success": True, "recovered_day": 2}, first)
    assert "- 第 1 次（乾旱）：失敗" in second and "- 第 2 次（乾旱）：成功：評估期第 2 天" in second
    assert "- b" in second and "- a" not in second  # 教訓整份換成新版
    assert "- 第 1 次" in gov.calls[-1].prompt.split("## 所有天災的紀錄")[1]


def test_reflect_retries_when_result_is_misread():
    """試跑時 Qwen 把失敗的天災寫成「災害未發生」：讀錯結果要帶著問題重問。"""
    gov = LLMGovernment(ScriptedClient([lessons(result="成功", worked=["上限有效"]), lessons(failed=["上限無效"])]))
    memory = gov.reflect(FULL_REPORT, "")
    assert "上限無效" in memory and "上限有效" not in memory
    assert [c.trigger for c in gov.calls] == ["reflect", "reflect-retry"]
    assert "報告的結果是「失敗」" in gov.calls[-1].prompt


def test_reflect_keeps_old_lessons_but_still_records_facts_when_llm_keeps_failing():
    old = first_memory("舊教訓")
    gov = LLMGovernment(ScriptedClient(["不是 JSON", lessons(result="成功", worked=["錯的"])]))
    memory = gov.reflect(FULL_REPORT, old)
    assert "舊教訓" in memory and "錯的" not in memory and "- 第 2 次" in memory
    assert gov.calls[-1].used_fallback and gov.last_reflect_error


def test_reflect_retries_when_lessons_are_copied():
    """試跑時第二次反思把舊記憶原封不動抄回來。"""
    gov = LLMGovernment(ScriptedClient([lessons(try_next=["舊教訓"]), lessons(failed=["新教訓"])]))
    assert "新教訓" in gov.reflect(FULL_REPORT, first_memory("舊教訓"))
    assert "一模一樣" in gov.calls[-1].prompt


def test_copied_lessons_are_accepted_on_the_last_try():
    gov = LLMGovernment(ScriptedClient([lessons(try_next=["舊教訓"])] * 2))
    memory = gov.reflect(FULL_REPORT, first_memory("舊教訓"))
    assert "舊教訓" in memory and not gov.calls[-1].used_fallback


def test_reflect_prompt_states_memory_limit():
    seen = {}

    def respond(system, user):
        seen["system"], seen["user"] = system, user
        return lessons(try_next=["x"])

    LLMGovernment(ScriptedClient(respond), memory_max_chars=1234).reflect(REPORT, "舊記憶")
    assert "1234" in seen["system"]
    assert "你目前的教訓（3 字）" in seen["user"] and "最多" in seen["user"]
    assert str(GovernmentConfig.memory_max_chars) in REFLECT_SYSTEM_PROMPT  # 預設值和引擎一致


def test_reflect_rejects_too_long_lessons_and_memory_fits_limit():
    long = lessons(try_next=["很長" * 500])
    gov = LLMGovernment(ScriptedClient([long, long]), memory_max_chars=400)
    memory = gov.reflect(FULL_REPORT, "")
    assert len(memory) <= 400 and "很長很長" not in memory and "- 第 2 次" in memory
    assert "超過上限" in gov.calls[-1].prompt


def test_old_records_are_dropped_so_lessons_always_fit():
    """天災紀錄越來越多時，刪最舊的紀錄，教訓仍然寫得進去（不會每次都因為超過字數被退回）。"""
    gov = LLMGovernment(ScriptedClient([lessons(try_next=[f"第{i}次的教訓"]) for i in range(1, 7)]),
                        memory_max_chars=600)
    memory = ""
    for i in range(1, 7):
        memory = gov.reflect({**FULL_REPORT, "disaster_id": i}, memory)
        assert len(memory) <= 600 and f"第{i}次的教訓" in memory
    assert "- 第 6 次" in memory and "- 第 1 次" not in memory
    assert all(c.trigger == "reflect" for c in gov.calls)  # 沒有因為太長而重問


def test_freeform_initial_memory_is_shown_as_lessons():
    prompt = build_reflection_prompt(FULL_REPORT, "# 初始經驗\n- 先觀察")
    assert "- 先觀察" in prompt.split("## 你目前的教訓")[1]


def test_build_government_passes_memory_limit(monkeypatch):
    from civsim.agents import build_government
    from civsim.config import SimConfig

    monkeypatch.setattr("civsim.agents.government.make_client", lambda: ScriptedClient([]))
    config = SimConfig()
    config.government.controller = "llm"
    config.government.memory_max_chars = 777
    assert "777" in build_government(config).reflect_system_prompt


def test_reflect_network_failure_keeps_memory():
    def boom(system, user):
        raise TimeoutError("連線逾時")

    gov = LLMGovernment(ScriptedClient(boom))
    assert gov.reflect(REPORT, "舊記憶") is None
    assert "TimeoutError" in gov.last_reflect_error and len(gov.calls) == 1  # 連不上就不重問


@pytest.mark.parametrize("text", ["沒有 JSON", '{"reason": "x"}', '{"worked": 3}'])
def test_parse_lessons_rejects_bad_response(text):
    with pytest.raises(ParseError):
        parse_lessons(text)


def test_parse_lessons_accepts_string_and_strips_bullets():
    data = parse_lessons('{"result": "失敗", "failed": "- 上限沒用", "worked": null, "try_next": ["• 發糧", ""]}')
    assert data["failed"] == ["上限沒用"] and data["worked"] == [] and data["try_next"] == ["發糧"]


def test_split_and_render_memory_round_trip():
    memory = render_memory(["- 第 1 次（乾旱）：失敗"], render_lessons({"failed": ["上限沒用"]}))
    records, rest = split_memory(memory)
    assert records == ["- 第 1 次（乾旱）：失敗"] and "- 上限沒用" in rest and "天災紀錄" not in rest


# ---- 建立 ----
def test_no_government_never_acts():
    assert NoGovernment().decide(make_obs()) == []
    assert NoGovernment().reflect(REPORT, "舊記憶") is None


def test_make_government_without_provider_fails_loudly(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError):
        make_government()


# ---- 沒變化就不呼叫（call_policy="on_change"）----
def counting_government(**options):
    seen = []

    def respond(system, user):
        seen.append(user)
        return IDLE
    return LLMGovernment(ScriptedClient(respond), call_policy="on_change", **options), seen


def test_on_change_skips_when_nothing_changed_and_reuses_decision():
    gov, seen = counting_government()
    first = gov.decide(make_obs(tick=1, hungry=3))
    again = gov.decide(make_obs(tick=2, prices={"food": 5.1}, hungry=4))  # 價格差 2%、挨餓多 1 人
    assert len(seen) == 1
    # 內容和上次相同，但標記成重播：紀錄才分得出哪些是 LLM 當天親自做的決定
    assert [(d.type, d.params, d.reason) for d in again] == [(d.type, d.params, d.reason) for d in first]
    assert all(d.replayed for d in again) and not any(d.replayed for d in first)
    assert not gov.calls[-1].used_llm and "第 1 天" in gov.calls[-1].trigger


@pytest.mark.parametrize("change", [
    {"prices": {"food": 6.0}},  # 價格變 20%
    {"hungry": 10},  # 挨餓多很多
    {"deaths": 1},
    {"memory": "# 新記憶"},
    {"recent_decisions": [{"tick": 1, "type": "idle", "params": {}, "accepted": False}]},  # 昨天被拒絕
])
def test_on_change_calls_when_something_changed(change):
    gov, seen = counting_government()
    gov.decide(make_obs(tick=1, hungry=3))
    gov.decide(make_obs(**{"tick": 2, "hungry": 3, **change}))
    assert len(seen) == 2


def test_on_change_calls_at_least_every_max_skip_days():
    gov, seen = counting_government(max_skip_days=2)
    for tick in range(1, 8):
        gov.decide(make_obs(tick=tick))
    assert len(seen) == 3  # 第 1、4、7 天


def test_on_change_calls_again_after_reflection_and_failure():
    gov, seen = counting_government()
    gov.decide(make_obs(tick=1))
    gov.reflect(REPORT, "舊記憶")  # 回應不是 memory，反思失敗也一樣要重新呼叫
    gov.decide(make_obs(tick=2))
    assert sum(not u.startswith("## 這次天災") for u in seen) == 2


def test_always_policy_calls_every_day():
    gov = LLMGovernment(ScriptedClient([IDLE, IDLE]))
    gov.decide(make_obs(tick=1))
    gov.decide(make_obs(tick=2))
    assert all(c.used_llm for c in gov.calls)


def test_unknown_call_policy_fails():
    with pytest.raises(ValueError):
        LLMGovernment(ScriptedClient([]), call_policy="sometimes")


def test_prompt_includes_compact_price_history():
    prompt = build_prompt(make_obs(price_history=[{"tick": 1, "price": 5.0}, {"tick": 2, "price": 8.5}]))
    assert "[[1,5.0],[2,8.5]]" in prompt
    assert "price_history" in build_system_prompt()


def test_prompts_explain_capped_days_do_not_count():
    assert "price_cap" in build_system_prompt() and "不算回到正常水準" in build_system_prompt()
    assert "壓住" in REFLECT_SYSTEM_PROMPT and "不算恢復" in REFLECT_SYSTEM_PROMPT


# ---- 決策 prompt：趨勢、近期決策的效果、行動分類（gov-v6）----
def history(*prices, start=10):
    return [{"tick": start + i, "price": p} for i, p in enumerate(prices)]


def test_trends_show_changes_and_streaks():
    obs = make_obs(tick=15, price_history=history(2.0, 2.0, 2.5, 3.0, 4.0), hungry=7)
    text = build_trends(obs, {14: 5, 12: 1})
    assert "這次週期第 5 天，昨天成交價 4.00" in text
    assert "比 1 天前 +33%" in text and "比 3 天前 +100%" in text and "比這次週期最低價 +100%" in text
    assert "價格已經連續上漲 3 天" in text
    assert "挨餓人數 7（1 天前 5、3 天前 1）" in text


def test_trends_warn_when_price_is_pinned_by_cap():
    obs = make_obs(price_history=history(2.0, 4.0), price_cap={"price": 4.0, "until": 20})
    assert "被上限壓住的價格，不代表市場已經恢復" in build_trends(obs)


def test_recent_decisions_are_grouped_with_effects():
    decisions = [{"tick": t, "type": "price_cap", "params": {"price": 4}, "accepted": True} for t in (11, 12, 13)]
    decisions.append({"tick": 14, "type": "execute_citizen", "params": {"target": "c00"},
                      "accepted": False, "rejected_reason": "不能亂殺平民"})
    obs = make_obs(tick=15, price_history=history(2.0, 3.0, 4.0, 4.0, 4.0), hungry=9, recent_decisions=decisions)
    text = build_recent_decisions(obs, {11: 2})
    assert '第 11–13 天 price_cap {"price": 4} ×3：之後價格 2.00 → 現在 4.00（+100%），挨餓 2 → 現在 9 人' in text
    assert "第 14 天 execute_citizen" in text and "被拒絕，理由：不能亂殺平民" in text


def test_prompt_puts_recent_decisions_in_text_not_json():
    obs = make_obs(recent_decisions=[{"tick": 2, "type": "idle", "params": {}, "accepted": True}])
    prompt = build_prompt(obs)
    state = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
    assert "recent_decisions" not in state and "第 2 天 idle" in prompt and "程式幫你算好的趨勢" in prompt


def test_government_tracks_hungry_and_resets_each_cycle():
    seen = []

    def respond(system, user):
        seen.append(user)
        return IDLE

    gov = LLMGovernment(ScriptedClient(respond))
    gov.decide(make_obs(tick=1, hungry=2, price_history=history(2.0, start=0)))
    gov.decide(make_obs(tick=2, hungry=6, price_history=history(2.0, 2.0, start=0)))
    assert "挨餓人數 6（1 天前 2）" in seen[-1]
    gov.decide(make_obs(tick=3, hungry=1, price_history=history(2.0, start=2)))  # 重置後價格歷史變短
    assert "挨餓人數 1；" in seen[-1]


def test_system_prompt_groups_actions_and_asks_for_situation():
    prompt = build_system_prompt()
    supply = prompt.index("【增加糧食")
    cap = prompt.index("【壓住價格")
    assert supply < prompt.index("- distribute_food") < cap < prompt.index("- price_cap")
    assert "不會增加糧食" in prompt and '"situation"' in prompt


def test_parse_decisions_ignores_situation():
    text = json.dumps({"situation": "價格連續上漲，可能在天災中", "actions": [{"type": "idle"}]}, ensure_ascii=False)
    assert [d.type for d in parse_decisions(text)[0]] == [GovernmentActionType.IDLE]
