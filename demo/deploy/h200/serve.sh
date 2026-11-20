#!/usr/bin/env bash
# Serve one of the demo's answer models with vLLM on one H200 (OpenAI-compatible API).
#
#   ./serve.sh qwen              # Docker, foreground, http://127.0.0.1:18081/v1
#   ./serve.sh gemma --detach    # Docker in the background, http://127.0.0.1:18082/v1
#   ./serve.sh qwen --native     # an installed `vllm` (venv, conda, Slurm job)
#   ./serve.sh qwen --print      # show the command only
#   ./serve.sh qwen -- --kv-cache-dtype fp8   # extra vLLM flags after --
#
# Settings come from the environment (defaults in brackets):
#   HPIS_MODEL_REPO     Hugging Face id [Qwen/Qwen3.6-27B | google/gemma-4-31B-it]
#   HPIS_MODEL_DIR      local checkpoint directory (absolute) instead of downloading
#   HPIS_SERVED_NAME    model name in the API [hpis-qwen-27b | hpis-gemma-4]
#   HPIS_PORT           host port [18081 | 18082]
#   HPIS_HOST           bind address [127.0.0.1]; reach it through an SSH tunnel
#   HPIS_GPU            GPU index [0]
#   HPIS_MAX_MODEL_LEN  context length [32768]
#   HPIS_MAX_NUM_SEQS   concurrent sequences [8]
#   HPIS_GPU_MEM_UTIL   fraction of GPU memory for this server [0.90]
#   HPIS_VLLM_IMAGE     Docker image [auto: v0.34.0 if driver >= 580, else v0.32.0-cu129]
#   HF_HOME             weights cache [~/.cache/huggingface]
#   HF_TOKEN, HF_ENDPOINT, VLLM_API_KEY, HTTP(S)_PROXY, NO_PROXY   passed through when set
set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

case "${1:-}" in
  qwen)
    repo=Qwen/Qwen3.6-27B; served=hpis-qwen-27b; port=18081; parser=qwen3; min_vllm=0.17.0 ;;
  gemma)
    repo=google/gemma-4-31B-it; served=hpis-gemma-4; port=18082; parser=gemma4; min_vllm=0.19.1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
profile=$1
shift

mode=docker detach=false dry=false
extra=()
while (($#)); do
  case "$1" in
    --native) mode=native ;;
    --docker) mode=docker ;;
    --detach|-d) detach=true ;;
    --print|--dry-run) dry=true ;;
    --) shift; extra=("$@"); break ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

repo=${HPIS_MODEL_REPO:-$repo}
served=${HPIS_SERVED_NAME:-$served}
port=${HPIS_PORT:-$port}
host=${HPIS_HOST:-127.0.0.1}
gpu=${HPIS_GPU:-0}

# Engine flags, checked against the vLLM v0.34.0 and v0.32.0 sources:
#   --reasoning-parser qwen3|gemma4 keeps any thinking out of message.content;
#   --default-chat-template-kwargs turns thinking off unless a request asks for it
#     (the demo client also sends enable_thinking=false itself);
#   --language-model-only skips the vision encoder (text-only demo).
args=(
  --served-model-name "$served"
  --dtype bfloat16
  --max-model-len "${HPIS_MAX_MODEL_LEN:-32768}"
  --max-num-seqs "${HPIS_MAX_NUM_SEQS:-8}"
  --gpu-memory-utilization "${HPIS_GPU_MEM_UTIL:-0.90}"
  --enable-prefix-caching
  --language-model-only
  --reasoning-parser "$parser"
  --default-chat-template-kwargs '{"enable_thinking": false}'
)
[[ ${#extra[@]} -gt 0 ]] && args+=("${extra[@]}")

run() {
  if $dry; then
    printf '%q ' "$@"
    printf '\n'
    exit 0
  fi
  exec "$@"
}

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]; }

if [[ $mode == native ]]; then
  if ! $dry; then
    command -v vllm >/dev/null || { echo "vllm is not on PATH; activate its environment or use Docker" >&2; exit 1; }
    have=$(vllm --version 2>/dev/null | tail -n1 || true)
    have=${have#v}
    have=${have%%+*}
    if [[ $have =~ ^[0-9]+\.[0-9]+ ]] && ! version_ge "$have" "$min_vllm"; then
      echo "vLLM $have is too old for $profile (needs >= $min_vllm; tested images: 0.32.0, 0.34.0)" >&2
      exit 1
    fi
  fi
  run env CUDA_VISIBLE_DEVICES="$gpu" VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 \
    vllm serve "${HPIS_MODEL_DIR:-$repo}" "${args[@]}" --host "$host" --port "$port"
fi

image=${HPIS_VLLM_IMAGE:-}
if [[ -z $image ]]; then
  driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader -i "$gpu" 2>/dev/null | head -n1 || true)
  if [[ -z $driver ]]; then
    $dry || { echo "nvidia-smi failed; is the NVIDIA driver installed?" >&2; exit 1; }
    image=vllm/vllm-openai:v0.34.0
  elif ((${driver%%.*} >= 580)); then
    image=vllm/vllm-openai:v0.34.0          # CUDA 13.0 build, needs driver >= 580
  else
    image=vllm/vllm-openai:v0.32.0-cu129    # newest CUDA 12.9 build
    echo "NVIDIA driver $driver < 580, using $image" >&2
  fi
fi

cmd=(docker run --name "hpis-$profile")
if $detach; then
  cmd+=(-d --restart unless-stopped)
else
  cmd+=(--rm)
fi
cmd+=(--gpus "device=$gpu" --ipc=host -p "$host:$port:8000"
      -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1)
for var in HF_TOKEN HF_ENDPOINT VLLM_API_KEY HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  if [[ -n ${!var:-} ]]; then cmd+=(-e "$var"); fi
done
if [[ -n ${HPIS_MODEL_DIR:-} ]]; then
  cmd+=(--mount "type=bind,source=$HPIS_MODEL_DIR,target=/model,readonly")
  weights=/model
else
  cache=${HF_HOME:-$HOME/.cache/huggingface}
  $dry || mkdir -p "$cache"
  cmd+=(-v "$cache:/root/.cache/huggingface")
  weights=$repo
fi
# The image's entrypoint is `vllm serve`; inside the container it listens on 8000.
run "${cmd[@]}" "$image" "$weights" "${args[@]}" --host 0.0.0.0 --port 8000
