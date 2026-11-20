#!/usr/bin/env bash
# Start one text-chat endpoint on the H200 host. The presentation uses the served alias.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: serve.sh {qwen|gemma} [--native] [--print]
  --native     Run the installed vllm command (also suitable inside an allocated GPU job).
  --print      Show the command with no model download or server startup.
Options via environment:
  HPIS_MODEL_DIR    Existing local Hugging Face checkpoint directory
  HPIS_MODEL_REPO   Hugging Face model ID if fetching weights
  HPIS_GPU          GPU device ID/UUID (Docker default 0; native honors CUDA_VISIBLE_DEVICES)
  HPIS_PORT         Port bound on the H200 host (qwen 18081, gemma 18082)
  HPIS_SERVED_NAME  API model alias (hpis-qwen-27b or hpis-gemma-4)
  HPIS_MAX_MODEL_LEN, HPIS_VLLM_IMAGE, HF_HOME, HF_TOKEN, VLLM_API_KEY
HELP
}

case "${1:-}" in
  qwen)  family=qwen; checkpoint=Qwen/Qwen3.6-27B; alias=hpis-qwen-27b; default_port=18081; parser=qwen3 ;;
  gemma) family=gemma; checkpoint=google/gemma-4-31B-it; alias=hpis-gemma-4; default_port=18082; parser=gemma4 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
shift
native=false
preview=false
for opt in "$@"; do
  case "$opt" in
    --native) native=true ;;
    --print) preview=true ;;
    *) usage >&2; exit 2 ;;
  esac
done

port=${HPIS_PORT:-$default_port}
weights=${HPIS_MODEL_REPO:-$checkpoint}
alias=${HPIS_SERVED_NAME:-$alias}
params=(
  --served-model-name "$alias"
  --tensor-parallel-size 1
  --dtype bfloat16
  --max-model-len "${HPIS_MAX_MODEL_LEN:-16384}"
  --max-num-seqs 4
  --language-model-only
  --reasoning-parser "$parser"
  --default-chat-template-kwargs '{"enable_thinking":false}'
)

if $native; then
  if [[ -n "${HPIS_MODEL_DIR:-}" ]]; then weights=$HPIS_MODEL_DIR; fi
  gpu=${HPIS_GPU:-${CUDA_VISIBLE_DEVICES:-0}}
  cmd=(env "CUDA_VISIBLE_DEVICES=$gpu" vllm serve "$weights" "${params[@]}" --host 127.0.0.1 --port "$port")
else
  cmd=(docker run --rm --name "hpis-$family-$port" --gpus "device=${HPIS_GPU:-0}"
       --shm-size=8g --publish "127.0.0.1:$port:8000")
  cache=${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}
  if [[ -n "${HPIS_MODEL_DIR:-}" ]]; then
    # --mount rejects a nonexistent host directory; it cannot silently create empty weights.
    cmd+=(--mount "type=bind,source=$HPIS_MODEL_DIR,target=/checkpoint,readonly")
    weights=/checkpoint
  else
    if ! $preview; then mkdir -p "$cache"; fi
    cmd+=(--mount "type=bind,source=$cache,target=/root/.cache/huggingface")
    if [[ -n "${HF_TOKEN:-}" ]]; then cmd+=(--env HF_TOKEN); fi
  fi
  if [[ -n "${VLLM_API_KEY:-}" ]]; then cmd+=(--env VLLM_API_KEY); fi
  cmd+=("${HPIS_VLLM_IMAGE:-vllm/vllm-openai:v0.32.0-cu129}" "$weights" "${params[@]}"
        --host 0.0.0.0 --port 8000)
fi

if $preview; then
  printf '%q ' "${cmd[@]}"
  printf '\n'
else
  exec "${cmd[@]}"
fi
