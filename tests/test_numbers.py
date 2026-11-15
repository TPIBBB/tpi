"""用手算得出來的數字檢查基礎引擎。

每個測試都是：一個很小的世界（數字全部寫死）→ 做一件事 → 檢查每個數字變成多少。
要看到每個數字，用：

    uv run pytest tests/test_numbers.py -s

每一行會印出「項目：實際數字（預期 …）✓」，不一致的會標 ✗ 並讓測試失敗。
"""

import math
import random

import pytest

from civsim.config import CitizenConfig, DisasterConfig, GovernmentConfig
from civsim.models import (
    GOV_ID, Action, ActionType, Citizen, CitizenType, Government, Market, Order, Persona, WorldState,
)
from civsim.world import government as gov_rules
from civsim.world import rules
from civsim.world.disasters import build_schedule
from civsim.world.equilibrium import band_from_prices
from civsim.world.government import GovernmentActionType as T
from civsim.world.government import GovernmentDecision
from civsim.world.market import clear, place_order, settle_market

GOV = GovernmentConfig()  # 進口加價 1.5 倍


# ---------- 印出數字的小工具 ----------

def title(text: str) -> None:
    print(f"\n\n【{text}】")


def check(label: str, actual, expected, formula: str = "") -> None:
    """印出一行對照，再檢查是否一致。"""
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        ok = math.isclose(actual, expected, abs_tol=1e-6)
        shown = f"{actual:g}"
    else:
        ok = actual == expected
        shown = str(actual)
    note = f"  ← {formula}" if formula else ""
    print(f"  {'✓' if ok else '✗'} {label}：{shown}（預期 {expected}）{note}")
    assert ok, f"{label}：實際 {actual}，預期 {expected}"


# ---------- 建立小世界 ----------

def citizen(cid: str, type_: CitizenType, food: float, money: float) -> Citizen:
    return Citizen(cid, type_, Persona(), food=food, money=money)


def tiny_world(*citizens: Citizen) -> WorldState:
    """政府預算 1000、儲備 100；市價 5、基準價 5；初始貨幣總量 1000。"""
    return WorldState(
        tick=0,
        citizens={c.id: c for c in citizens},
        government=Government(budget=1000.0, food_reserve=100.0),
        market=Market(price=5.0, base_price=5.0, initial_money=1000.0),
    )


def gov_do(world, type_, **params):
    decision = GovernmentDecision(type_, params)
    assert gov_rules.validate(world, decision, GOV) is None
    return gov_rules.settle(world, decision, GOV)


# ---------- 平民：生產與吃飯 ----------

def test_work_pays_wage_12():
    title("打工：消費者原本有 40 元")
    w = tiny_world(citizen("a", CitizenType.CONSUMER, food=5, money=40))
    rules.settle(w, Action("a", ActionType.WORK), CitizenConfig(), random.Random(0))
    check("錢", w.citizens["a"].money, 52, "40 + 工資 12")


def test_harvest_amount_and_drought():
    title("收成：農夫原本有 10 個糧，每次收成固定 7")
    cfg = CitizenConfig(harvest_min=7, harvest_max=7)
    w = tiny_world(citizen("f", CitizenType.PRODUCER_TRADER, food=10, money=0))
    rules.settle(w, Action("f", ActionType.HARVEST), cfg, random.Random(0))
    check("平常收成後的糧", w.citizens["f"].food, 17, "10 + 7")

    w.food_multiplier = 0.2
    rules.settle(w, Action("f", ActionType.HARVEST), cfg, random.Random(0))
    check("天災時再收成一次", w.citizens["f"].food, 18.4, "17 + 7 × 0.2")


def test_everyone_eats_1_per_day():
    title("吃飯：每人每天吃 1 個糧")
    w = tiny_world(citizen("full", CitizenType.CONSUMER, food=3, money=0),
                   citizen("low", CitizenType.CONSUMER, food=0.5, money=0))
    rules.end_of_tick_upkeep(w, CitizenConfig())
    full, low = w.citizens["full"], w.citizens["low"]
    check("有 3 個糧的人，吃完剩", full.food, 2, "3 - 1")
    check("他挨餓天數", full.hungry_days, 0)
    check("只有 0.5 個糧的人，吃完剩", low.food, 0, "不夠 1 個，吃光")
    check("他挨餓天數", low.hungry_days, 1)
    rules.end_of_tick_upkeep(w, CitizenConfig())
    check("第 2 天他挨餓天數", low.hungry_days, 2)
    check("他還活著", low.alive, True, "目前不會餓死")


# ---------- 市場：集合競價 ----------

def test_one_buyer_one_seller():
    title("市場：A 最多出 6 元買 10 個，B 最少要 4 元賣 4 個，昨天價格 5")
    w = tiny_world(citizen("A", CitizenType.CONSUMER, food=0, money=100),
                   citizen("B", CitizenType.PRODUCER_TRADER, food=10, money=0))
    place_order(w, Order("A", "buy", 10, 6))
    place_order(w, Order("B", "sell", 4, 4))
    check("下單後 A 的錢（先扣住）", w.citizens["A"].money, 40, "100 - 10 × 6")
    check("下單後 B 的糧（先扣住）", w.citizens["B"].food, 6, "10 - 4")

    result, _ = settle_market(w, random.Random(0))
    A, B = w.citizens["A"], w.citizens["B"]
    check("成交量", result.volume, 4, "只有 4 個可賣")
    check("成交價", result.price, 5, "4~6 都能成交 4 個，選最接近昨天的 5")
    check("A 的糧", A.food, 4)
    check("A 的錢", A.money, 80, "100 - 4 × 5，沒用到的退回")
    check("B 的糧", B.food, 6)
    check("B 的錢", B.money, 20, "4 × 5")


def test_volume_is_maximised():
    title("市場：買 10元×5、6元×5、3元×5；賣 4元×5、5元×5、9元×5")
    orders = [Order("b1", "buy", 5, 10), Order("b2", "buy", 5, 6), Order("b3", "buy", 5, 3),
              Order("s1", "sell", 5, 4), Order("s2", "sell", 5, 5), Order("s3", "sell", 5, 9)]
    r = clear(orders, last_price=5, price_cap=None, rng=random.Random(0))
    check("總需求", r.demand, 15)
    check("總供給", r.supply, 15)
    check("成交量", r.volume, 10, "價格 5~6 時，買的 10 個、賣的 10 個")
    check("成交價", r.price, 5, "5、5.5、6 都成交 10 個，選最接近昨天的 5")


def test_no_trade_price_moves_to_best_bid():
    title("市場：只有人想用 8 元買 3 個，沒人賣，昨天價格 5")
    r = clear([Order("b", "buy", 3, 8)], last_price=5, price_cap=None, rng=random.Random(0))
    check("成交量", r.volume, 0)
    check("價格", r.price, 8, "沒成交，價格往最高買價走")


def test_price_cap_6():
    title("市場：買家出 10 元，賣家至少要 7 元，政府上限 6 元")
    orders = [Order("b", "buy", 5, 10), Order("s", "sell", 5, 7)]
    r = clear(orders, last_price=5, price_cap=6, rng=random.Random(0))
    check("成交量", r.volume, 0, "6 元沒人願意賣")
    check("價格", r.price, 6, "卡在上限")
    check("被上限壓住", r.capped, True)


# ---------- 政府（預算 1000、儲備 100） ----------

def test_distribute_food():
    title("政府發糧：2 個人各發 2 個")
    w = tiny_world(citizen("a", CitizenType.DEPENDENT, food=0, money=0),
                   citizen("b", CitizenType.DEPENDENT, food=1, money=0))
    gov_do(w, T.DISTRIBUTE_FOOD, qty_per_person=2)
    check("a 的糧", w.citizens["a"].food, 2, "0 + 2")
    check("b 的糧", w.citizens["b"].food, 3, "1 + 2")
    check("政府儲備", w.government.food_reserve, 96, "100 - 2 人 × 2")


def test_cash_transfer_and_tax():
    title("政府發錢再收稅：a 有 50 元、b 有 30 元")
    w = tiny_world(citizen("a", CitizenType.CONSUMER, food=0, money=50),
                   citizen("b", CitizenType.CONSUMER, food=0, money=30))
    gov_do(w, T.CASH_TRANSFER, per_person=10)
    check("每人發 10 元後 a 的錢", w.citizens["a"].money, 60)
    check("每人發 10 元後 b 的錢", w.citizens["b"].money, 40)
    check("政府預算", w.government.budget, 980, "1000 - 2 × 10")

    gov_do(w, T.COLLECT_TAX, rate=0.1)
    check("收 10% 稅後 a 的錢", w.citizens["a"].money, 54, "60 - 6")
    check("收 10% 稅後 b 的錢", w.citizens["b"].money, 36, "40 - 4")
    check("政府預算", w.government.budget, 990, "980 + 6 + 4")


def test_import_food():
    title("政府進口 10 個糧")
    w = tiny_world()
    gov_do(w, T.IMPORT_FOOD, qty=10)
    check("政府預算", w.government.budget, 925, "1000 - 10 × 習慣價格 5 × 加價 1.5")
    check("政府儲備", w.government.food_reserve, 110, "100 + 10")


def test_seize_food():
    title("政府徵收自耕農 50% 的糧：自耕農和消費者各有 8 個")
    w = tiny_world(citizen("f", CitizenType.PRODUCER_SELF, food=8, money=0),
                   citizen("c", CitizenType.CONSUMER, food=8, money=0))
    gov_do(w, T.SEIZE_FOOD, rate=0.5, target_type="producer_self")
    check("自耕農的糧", w.citizens["f"].food, 4, "8 的一半")
    check("消費者的糧（不受影響）", w.citizens["c"].food, 8)
    check("政府儲備", w.government.food_reserve, 104, "100 + 4")


def test_issue_currency_raises_customary_price():
    title("政府發行 500 元新貨幣（原本全社會共 1000 元）")
    w = tiny_world()
    gov_do(w, T.ISSUE_CURRENCY, amount=500)
    check("政府預算", w.government.budget, 1500, "1000 + 500")
    check("習慣價格", w.market.customary_price, 7.5, "5 × (1000 + 500) / 1000")


def test_release_reserve_sells_into_market():
    title("政府用至少 4 元賣 10 個儲備；平民最多出 6 元買 3 個")
    w = tiny_world(citizen("a", CitizenType.CONSUMER, food=0, money=100))
    gov_do(w, T.RELEASE_RESERVE, qty=10, price=4)
    place_order(w, Order("a", "buy", 3, 6))
    result, _ = settle_market(w, random.Random(0))
    check("成交量", result.volume, 3)
    check("成交價", result.price, 5)
    check("政府儲備", w.government.food_reserve, 97, "100 - 3，賣不掉的 7 個退回")
    check("政府預算", w.government.budget, 1015, "1000 + 3 × 5")
    check("平民的糧", w.citizens["a"].food, 3)
    check("平民的錢", w.citizens["a"].money, 85, "100 - 3 × 5")


def test_money_and_food_totals_do_not_change_in_trade():
    title("交易前後，全社會的錢和糧總量不變")
    w = tiny_world(citizen("A", CitizenType.CONSUMER, food=0, money=100),
                   citizen("B", CitizenType.PRODUCER_TRADER, food=10, money=0))
    place_order(w, Order("A", "buy", 10, 6))
    place_order(w, Order("B", "sell", 4, 4))
    place_order(w, Order(GOV_ID, "sell", 2, 3))
    settle_market(w, random.Random(0))
    money = sum(c.money for c in w.citizens.values()) + w.government.budget
    food = sum(c.food for c in w.citizens.values()) + w.government.food_reserve
    check("錢的總量", money, 1100, "A 100 + B 0 + 政府 1000")
    check("糧的總量", food, 110, "A 0 + B 10 + 政府 100")


# ---------- 天災時程與均衡區間 ----------

def test_disaster_calendar():
    title("天災時程：穩定 3 天、天災 2 天、評估 4 天，共 2 次")
    s = build_schedule(DisasterConfig(count=2, stable_days=3, stable_jitter=0, disaster_days=2, evaluation_days=4))
    check("第 1 次：天災開始第幾天", s[0].start_tick, 3, "第 0~2 天是穩定期")
    check("第 1 次：天災最後一天", s[0].end_tick, 4)
    check("第 1 次：評估期最後一天", s[0].window_end, 8, "第 5~8 天評估")
    check("第 2 次：週期開始", s[1].cycle_start, 9)
    check("第 2 次：天災開始", s[1].start_tick, 12)
    check("第 2 次：評估期最後一天", s[1].window_end, 17)


def test_equilibrium_band():
    title("均衡區間：成交價 4、5、6，k = 2")
    band = band_from_prices([4, 5, 6], k=2)
    check("平均", band.mean, 5)
    check("標準差", band.std, 0.8165, "√(2/3)")
    check("區間下限", band.low, 3.367, "5 - 2 × 0.8165")
    check("區間上限", band.high, 6.633, "5 + 2 × 0.8165")
    check("6.5 在區間內", band.contains(6.5), True)
    check("7 在區間內", band.contains(7), False)
