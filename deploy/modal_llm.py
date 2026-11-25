"""在 Modal 上用 vLLM 架一個開源模型，提供 OpenAI 相容 API 給 AI 政府使用。負責人：Agent。

為什麼：Gemini 免費版每天只能呼叫 20 次，跑不完實驗。Modal 免費方案每月有 $30 的 GPU 額度，
自己架模型就沒有呼叫次數限制，只算 GPU 開機的時間。

第一次使用（用 uvx 執行 modal CLI，不用另外安裝；只有負責部署的人要做，其他人只要填 .env）：
    uvx modal setup                                   # 登入 Modal（會開瀏覽器）
    uvx modal secret create civsim-llm-key VLLM_API_KEY=<自己設一組長一點的密碼>
    uvx modal run deploy/modal_llm.py::download       # 先把模型下載到 Volume（只用 CPU，約 10 分鐘，只要做一次）
    uvx modal deploy deploy/modal_llm.py              # 部署，完成後會印出網址
    uvx modal app logs civsim-llm                     # 模型載入失敗時看 log

.env 設定（網址後面要加 /v1）：
    LLM_PROVIDER=modal
    LLM_BASE_URL=https://<部署印出的網址>/v1
    LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
    LLM_API_KEY=<上面設的 VLLM_API_KEY>

費用：GPU 只在有請求時開機，閒置 SCALEDOWN_MINUTES 分鐘後自動關機，關機後不計費。
冷啟動（GPU 關機後的第一次呼叫）要等幾分鐘開機與載入模型；之後同一次開機內的呼叫只要幾秒。
不用時可以用 `uvx modal app stop civsim-llm` 停掉。

換模型：改 MODEL_NAME 後重新 deploy，.env 的 LLM_MODEL 也要改成一樣。正式實驗前要鎖定模型，
A、C 組才能比較。
"""

import os
import subprocess

import modal

MODEL_NAME = os.environ.get("CIVSIM_MODEL", "Qwen/Qwen2.5-7B-Instruct")  # 開源、中文好、不用另外申請權限
HF_CACHE = "/root/.cache/huggingface"
GPU = "L4"  # 24 GB，跑 7B～8B 模型夠用；約 $0.80／小時（2026/11 Modal 價格頁）
MAX_MODEL_LEN = 16384  # prompt 最長的是反思（天災報告 + 記憶），約幾千 token
SCALEDOWN_MINUTES = 5  # 閒置幾分鐘後關機；太短會常常冷啟動，太長會多付閒置的錢
PORT = 8000

image = (
    modal.Image.debian_slim(python_version="3.12")
    # vllm 0.9.2 沒有限制 transformers 上限，不鎖的話會裝到 transformers 5.x / huggingface_hub 2.x，
    # 跟 vllm 0.9.2 不相容、模型載入失敗。鎖在同期版本（會一起帶入 huggingface_hub<1.0）。
    .pip_install("vllm==0.9.2", "transformers==4.53.3", "huggingface_hub[hf_transfer]<1.0")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
# 模型檔案存在 Volume，之後冷啟動不用重新下載
hf_cache = modal.Volume.from_name("civsim-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("civsim-vllm-cache", create_if_missing=True)

app = modal.App("civsim-llm")


@app.function(
    image=image,
    gpu=GPU,
    scaledown_window=SCALEDOWN_MINUTES * 60,
    timeout=60 * 60,
    volumes={HF_CACHE: hf_cache, "/root/.cache/vllm": vllm_cache},
    secrets=[modal.Secret.from_name("civsim-llm-key")],  # 沒有金鑰的人不能用你的 GPU 額度
    max_containers=1,  # 只開一台，避免額度一下子被用完
)
@modal.concurrent(max_inputs=16)
@modal.web_server(port=PORT, startup_timeout=20 * 60)
def serve():
    cmd = [
        "vllm", "serve", MODEL_NAME,
        "--served-model-name", MODEL_NAME,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--max-model-len", str(MAX_MODEL_LEN),
        "--api-key", os.environ["VLLM_API_KEY"],
        "--enforce-eager",  # 跳過 CUDA graph 與編譯最佳化：冷啟動快很多，每次回應稍微慢一點
    ]
    subprocess.Popen(cmd)


@app.function(image=image, volumes={HF_CACHE: hf_cache}, timeout=60 * 60)
def download():
    """把模型下載到 Volume，之後冷啟動就不用重新下載。只用 CPU，不花 GPU 費用。"""
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL_NAME, ignore_patterns=["*.pt", "*.bin"])  # 只要 safetensors
    hf_cache.commit()
    print(f"已下載 {MODEL_NAME} 到 {path}")
