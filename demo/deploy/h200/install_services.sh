#!/usr/bin/env bash
# Keep the HPIS 2.0 demo running for days: systemd *user* services that restart on
# failure and keep running after you log out (and after a reboot, with lingering on).
# Nothing here needs root, restarts a shared service or installs system packages.
#
#   ./install_services.sh --lan            # the demo page, open to the intranet
#   ./install_services.sh --lan --ollama   # plus one Ollama per lane, just for the demo
#   ./install_services.sh --native-models  # plus vLLM servers (serve.sh --native)
#   ./install_services.sh --uninstall
#
# The demo service takes its model settings from the current shell:
#   HPIS_LLM_BACKEND (ollama | h200 | openrouter), HPIS_LLM_QWEN_MODEL, HPIS_LLM_GEMMA_MODEL,
#   HPIS_LLM_OLLAMA_URL, HPIS_LLM_OLLAMA_KEEP_ALIVE, HPIS_LLM_QWEN_LABEL, HPIS_LLM_GEMMA_LABEL,
#   HPIS_DEMO_PORT [8780]
#
# --ollama runs two `ollama serve` under your account, one for each lane, on the first free
# ports from 127.0.0.1:${HPIS_OLLAMA_PORT:-11535}: Ollama answers a Qwen 3.5-family model
# (Qwen3.6 / 3.8) one request at a time, so a shared server would make one lane wait for
# the other.  Both have the same settings (16k context) and GPU(s) (HPIS_OLLAMA_GPU, required,
# e.g. 0 or 0,1), and both read the model files of an Ollama that is already running (found
# automatically, or HPIS_OLLAMA_MODELS) without copying or changing them.
# HPIS_OLLAMA_BIN picks another ollama binary (for example a newer one in your home folder).
#
# --native-models: HPIS_GPU_QWEN / HPIS_GPU_GEMMA [0 / 0], HPIS_GPU_MEM_UTIL_QWEN / _GEMMA
#   [0.44 / 0.50], HPIS_MODEL_DIR_QWEN / _GEMMA, HPIS_MODEL_REPO_QWEN / _GEMMA, HF_HOME,
#   HF_ENDPOINT, http(s)_proxy, no_proxy, VLLM_API_KEY; `vllm` must be on PATH.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
native=false lan=false ollama=false uninstall=false
for opt in "$@"; do
  case "$opt" in
    --native-models) native=true ;;
    --ollama) ollama=true ;;
    --lan) lan=true ;;
    --uninstall) uninstall=true ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown option $opt" >&2; exit 2 ;;
  esac
done

if ! systemctl --user show-environment >/dev/null 2>&1; then
  cat >&2 <<'MSG'
systemd user services are not available in this session.
Fallback: keep the demo alive inside tmux with an automatic restart loop (run it in the
shell where you exported the HPIS_LLM_* settings):
  tmux new -d -s hpis2 'cd ~/HPIS2 && while true; do .venv-demo/bin/python -m demo --host 0.0.0.0 --port 8780; sleep 5; done'
MSG
  exit 1
fi

if $uninstall; then
  for u in hpis2-demo hpis2-ollama hpis2-ollama-plain hpis2-qwen hpis2-gemma; do
    systemctl --user disable --now "$u.service" 2>/dev/null || true
    rm -f "$units/$u.service"
  done
  systemctl --user daemon-reload
  echo "removed"
  exit 0
fi

[[ -x $repo/.venv-demo/bin/python ]] || { echo "missing $repo/.venv-demo; install the demo first (H200部署步骤.md step 2)" >&2; exit 1; }
mkdir -p "$units"
port=${HPIS_DEMO_PORT:-8780}
host=127.0.0.1
$lan && host=0.0.0.0
backend=${HPIS_LLM_BACKEND:-h200}
services=()
after=""

env_lines() {  # copy selected variables from this shell into the unit
  local var
  for var in "$@"; do
    if [[ -n ${!var:-} ]]; then printf 'Environment="%s=%s"\n' "$var" "${!var}"; fi
  done
}

port_busy() {  # is something listening on TCP port $1, on any address?
  if command -v ss >/dev/null 2>&1; then
    [[ -n $(ss -ltnH "sport = :$1" 2>/dev/null) ]]
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

has_models() {  # does the Ollama models folder $1 hold the configured models, readable by us?
  local dir=$1 m name tag manifest
  [[ -d $dir/manifests && -r $dir/blobs ]] || return 1
  for m in ${HPIS_LLM_QWEN_MODEL:-} ${HPIS_LLM_GEMMA_MODEL:-}; do
    if [[ ${m##*/} == *:* ]]; then name=${m%:*} tag=${m##*:}; else name=$m tag=latest; fi
    manifest=$(find "$dir/manifests" -path "*/$name/$tag" -print -quit 2>/dev/null)
    [[ -n $manifest && -r $manifest ]] || return 1
  done
}

if $ollama; then
  ollama_bin=${HPIS_OLLAMA_BIN:-$(command -v ollama || true)}
  [[ -x $ollama_bin ]] || { echo "ollama is not on PATH (or set HPIS_OLLAMA_BIN)" >&2; exit 1; }
  [[ -n ${HPIS_OLLAMA_GPU:-} ]] || { echo "set HPIS_OLLAMA_GPU to a free GPU from nvidia-smi, e.g. HPIS_OLLAMA_GPU=3" >&2; exit 1; }
  models=${HPIS_OLLAMA_MODELS:-}
  if [[ -z $models ]]; then
    service_dir=$(systemctl show ollama.service -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -n 's/^OLLAMA_MODELS=//p' | head -n1)
    for dir in "$HOME/.ollama/models" $service_dir /usr/share/ollama/.ollama/models /var/lib/ollama/models; do
      if has_models "$dir"; then models=$dir; break; fi
    done
  fi
  if [[ -z $models ]]; then
    # For example a colleague's Ollama: ask the running servers where their files are
    # (from the repository root, where the demo package is importable).
    models=$(cd "$repo" && HPIS_LLM_BACKEND=ollama .venv-demo/bin/python -m demo.llm --models-dir) || models=""
  fi
  if [[ -z $models ]] || ! has_models "$models"; then
    echo "cannot find ${HPIS_LLM_QWEN_MODEL:-?} and ${HPIS_LLM_GEMMA_MODEL:-?} in a model folder readable by $USER${models:+ (looked in $models)}." >&2
    echo "Check the names with: cd $repo && .venv-demo/bin/python -m demo.llm --find-ollama" >&2
    echo "then set HPIS_OLLAMA_MODELS to the model folder it shows." >&2
    exit 1
  fi
  for u in hpis2-ollama hpis2-ollama-plain; do  # a reinstall keeps its ports
    systemctl --user stop "$u.service" 2>/dev/null || true
  done
  next_free() { local p=$1; while port_busy "$p"; do p=$((p + 1)); done; echo "$p"; }
  oport=${HPIS_OLLAMA_PORT:-$(next_free 11535)}
  pport=$(next_free $((oport + 1)))
  ctx=16384
  ollama_unit() {  # $1 unit name, $2 port, $3 lane
    {
      echo "[Unit]"
      echo "Description=HPIS 2.0 demo: Ollama for the $3 lane"
      echo "After=network-online.target"
      echo "[Service]"
      echo "Environment=\"OLLAMA_HOST=127.0.0.1:$2\""
      echo "Environment=\"OLLAMA_MODELS=$models\""
      echo "Environment=\"OLLAMA_NUM_PARALLEL=1\""
      echo "Environment=\"OLLAMA_CONTEXT_LENGTH=$ctx\""
      echo "Environment=\"OLLAMA_KEEP_ALIVE=30m\""
      echo "Environment=\"OLLAMA_NOPRUNE=1\""            # never delete anything in the shared folder
      echo "Environment=\"CUDA_DEVICE_ORDER=PCI_BUS_ID\""  # GPU numbers as nvidia-smi shows them
      echo "Environment=\"CUDA_VISIBLE_DEVICES=$HPIS_OLLAMA_GPU\""
      echo "ExecStart=$ollama_bin serve"
      echo "Restart=always"
      echo "RestartSec=5"
      echo "[Install]"
      echo "WantedBy=default.target"
    } > "$units/$1.service"
  }
  ollama_unit hpis2-ollama "$oport" "HPIS 2.0"
  ollama_unit hpis2-ollama-plain "$pport" "plaintext cloud"
  services+=(hpis2-ollama.service hpis2-ollama-plain.service)
  after+=" hpis2-ollama.service hpis2-ollama-plain.service"
  backend=ollama
else
  # Installed earlier with --ollama, now without: stop the demo-only Ollama servers.
  for u in hpis2-ollama hpis2-ollama-plain; do
    if [[ -f $units/$u.service ]]; then
      systemctl --user disable --now "$u.service" 2>/dev/null || true
      rm -f "$units/$u.service"
    fi
  done
fi

if $native; then
  vllm_bin=$(command -v vllm || true)
  [[ -n $vllm_bin ]] || { echo "vllm is not on PATH; activate its environment first" >&2; exit 1; }
  for profile in qwen gemma; do
    up=$(printf %s "$profile" | tr a-z A-Z)
    gpu_var=HPIS_GPU_$up util_var=HPIS_GPU_MEM_UTIL_$up dir_var=HPIS_MODEL_DIR_$up repo_var=HPIS_MODEL_REPO_$up
    default_util=0.44; [[ $profile == gemma ]] && default_util=0.50
    wait_first=""
    if [[ $profile == gemma ]]; then
      # Let Qwen finish loading before Gemma measures free GPU memory.
      wait_first="ExecStartPre=/bin/bash -c 'for i in \$(seq 1 360); do curl -sf http://127.0.0.1:18081/v1/models >/dev/null && exit 0; sleep 5; done; exit 0'"
    fi
    after_unit=""
    [[ $profile == gemma ]] && after_unit=" hpis2-qwen.service"
    {
      echo "[Unit]"
      echo "Description=HPIS 2.0 demo model ($profile, vLLM)"
      echo "After=network-online.target$after_unit"
      echo "[Service]"
      echo "WorkingDirectory=$here"
      echo "Environment=\"PATH=$(dirname "$vllm_bin"):/usr/local/bin:/usr/bin:/bin\""
      echo "Environment=\"HPIS_GPU=${!gpu_var:-0}\""
      echo "Environment=\"HPIS_GPU_MEM_UTIL=${!util_var:-$default_util}\""
      echo "Environment=\"HPIS_MAX_NUM_SEQS=${HPIS_MAX_NUM_SEQS:-4}\""
      [[ -n ${!dir_var:-} ]] && echo "Environment=\"HPIS_MODEL_DIR=${!dir_var}\""
      [[ -n ${!repo_var:-} ]] && echo "Environment=\"HPIS_MODEL_REPO=${!repo_var}\""
      env_lines HF_HOME HF_ENDPOINT HF_TOKEN http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY VLLM_API_KEY HPIS_MAX_MODEL_LEN
      [[ -n $wait_first ]] && echo "$wait_first"
      echo "ExecStart=$here/serve.sh $profile --native"
      echo "Restart=always"
      echo "RestartSec=10"
      echo "TimeoutStartSec=infinity"
      echo "[Install]"
      echo "WantedBy=default.target"
    } > "$units/hpis2-$profile.service"
  done
  services+=(hpis2-qwen.service hpis2-gemma.service)
  after+=" hpis2-qwen.service hpis2-gemma.service"
fi

{
  echo "[Unit]"
  echo "Description=HPIS 2.0 demo (web page + HPIS/HPIC services)"
  echo "After=network-online.target$after"
  [[ -n $after ]] && echo "Wants=${after# }"
  echo "[Service]"
  echo "WorkingDirectory=$repo"
  echo "Environment=PYTHONUNBUFFERED=1"
  env_lines HPIS_LLM_OLLAMA_URL HPIS_LLM_PLAIN_OLLAMA_URL HPIS_LLM_OLLAMA_KEEP_ALIVE HPIS_LLM_OLLAMA_NUM_CTX \
    HPIS_LLM_QWEN_MODEL HPIS_LLM_GEMMA_MODEL HPIS_LLM_QWEN_BASE_URL HPIS_LLM_GEMMA_BASE_URL \
    HPIS_LLM_QWEN_LABEL HPIS_LLM_GEMMA_LABEL VLLM_API_KEY HPIS_LLM_QWEN_API_KEY HPIS_LLM_GEMMA_API_KEY \
    HPIS_DEMO_RUNTIME
  echo "Environment=HPIS_LLM_BACKEND=$backend"
  if $ollama; then  # later assignments win, so these override the shell's values
    echo "Environment=\"HPIS_LLM_OLLAMA_URL=http://127.0.0.1:$oport\""
    echo "Environment=\"HPIS_LLM_PLAIN_OLLAMA_URL=http://127.0.0.1:$pport\""
    echo "Environment=\"HPIS_LLM_OLLAMA_NUM_CTX=$ctx\""
  fi
  echo "ExecStart=$repo/.venv-demo/bin/python -m demo --host $host --port $port"
  echo "Restart=always"
  echo "RestartSec=5"
  echo "[Install]"
  echo "WantedBy=default.target"
} > "$units/hpis2-demo.service"
services+=(hpis2-demo.service)

if [[ -z ${HPIS_LLM_BACKEND:-} ]] && ! $ollama; then
  echo "WARNING: HPIS_LLM_BACKEND is not set, so the demo will look for vLLM on 127.0.0.1:18081/18082." >&2
  echo "         With Ollama, run the export line of H200部署步骤.md step 3 in this shell and install again." >&2
fi

systemctl --user daemon-reload
systemctl --user enable "${services[@]}"
systemctl --user restart "${services[@]}"  # a reinstall must pick up the new settings

if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q yes; then
  loginctl enable-linger "$USER" 2>/dev/null || \
    echo "NOTE: run 'sudo loginctl enable-linger $USER' once so these services keep running after you log out (it only affects your account)" >&2
fi

echo
echo "installed: ${services[*]}"
echo "models:    $backend · qwen=${HPIS_LLM_QWEN_MODEL:-default} · gemma=${HPIS_LLM_GEMMA_MODEL:-default}"
echo "status:    systemctl --user status ${services[*]}"
echo "logs:      journalctl --user -u hpis2-demo -f"
if $ollama; then
  echo "ollama:    HPIS 2.0 lane on port $oport, plaintext lane on port $pport, GPU $HPIS_OLLAMA_GPU, model files in $models"
  echo "check:     cd $repo && HPIS_LLM_OLLAMA_URL=http://127.0.0.1:$oport HPIS_LLM_PLAIN_OLLAMA_URL=http://127.0.0.1:$pport HPIS_LLM_OLLAMA_NUM_CTX=$ctx .venv-demo/bin/python -m demo.llm --check"
fi
if $lan; then
  echo "page:      http://$(hostname -I 2>/dev/null | awk '{print $1}'):$port   (anyone on the intranet can open it)"
else
  echo "page:      http://127.0.0.1:$port on this server; from a laptop: ssh -NT -L $port:127.0.0.1:$port <server>"
fi
