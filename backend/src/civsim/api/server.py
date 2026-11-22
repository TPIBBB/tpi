"""world-api：把模擬引擎包成 HTTP 服務，註冊到 digiRunner 後面。

啟動：civsim serve --config configs/baseline.yaml
文件：http://localhost:8000/docs

/world/*   給前端視覺化讀取（前端在另一個 repo：TPIBBB/tpi_frontend），只能讀，不能推進世界
/runs/*    給政府 client 呼叫（api/government.py，格式見 docs/api.md）

平民在 world-api 內部計算，不走 API。政府的路徑是 lockstep：政府每送一次決策，世界就前進一天；
世界只會因為政府的決策前進（原本的 POST /admin/step 已停用，見 docs/api.md 的 D9）。

⚠️ /world/disasters 是天災時間表，不能開放給政府的 client（政府不能事先知道天災何時發生）。

前端允許的來源由環境變數 CIVSIM_CORS_ORIGINS 設定（逗號分隔），預設為 Vite 開發伺服器。

負責人：digiRunner 整合組 / 前端組（/world/*）。
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from civsim.api.government import ApiError, create_government_router
from civsim.models import Event
from civsim.world import SimulationEngine


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:4]}"


def create_app(engine: SimulationEngine, run_id: str | None = None) -> FastAPI:
    app = FastAPI(title="civsim world-api")
    app.state.run_id = run_id or new_run_id()
    lock = threading.Lock()  # 同一時間只有一個請求能推進世界
    origins = os.getenv("CIVSIM_CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])
    event_log: list[Event] = []  # 政府的路徑推進世界時寫入，GET /world/events 讀取

    @app.exception_handler(ApiError)
    def api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.error, "message": exc.message})

    app.include_router(create_government_router(engine, app.state.run_id, lock, event_log))

    @app.get("/world/state")
    def world_state():
        w = engine.world
        return {
            "tick": w.tick,
            "phase": w.phase,
            "disaster_id": w.disaster_id,
            "market": {"price": w.market.price, "customary_price": w.market.customary_price,
                       "price_cap": w.market.price_cap, "price_cap_until": w.market.price_cap_until},
            "government": asdict(w.government),
            "band": engine.band.to_dict() if engine.band else None,
            "citizens": [
                {"id": c.id, "type": c.type.value, "alive": c.alive, "food": c.food, "money": c.money,
                 "hungry_days": c.hungry_days}
                for c in w.citizens.values()
            ],
        }

    @app.get("/world/metrics")
    def world_metrics():
        return engine.history

    @app.get("/world/events")
    def world_events(since_tick: int = 0):
        return [asdict(e) for e in event_log if e.tick >= since_tick]

    @app.get("/world/disasters")
    def world_disasters():
        """天災時間表，欄位沿用引擎的 Disaster（docs/api.md 的 D10）。只開放給前端，不能給政府。"""
        return [asdict(d) for d in engine.schedule]

    return app
