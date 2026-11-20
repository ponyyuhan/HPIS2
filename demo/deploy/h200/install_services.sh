#!/usr/bin/env bash
# Keep the HPIS 2.0 demo running for days: systemd *user* services that restart on
# failure and keep running after you log out (and after a reboot, with lingering on).
#
#   ./install_services.sh                   # demo only (models run as Docker containers,
#                                           #   which serve.sh --detach already restarts)
#   ./install_services.sh --native-models   # also run `serve.sh qwen|gemma --native`
#   ./install_services.sh --lan             # open the page at http://<server-ip>:8780
#                                           #   for everyone on the intranet (no SSH tunnel)
#   ./install_services.sh --uninstall
#
# Settings read from the current shell and written into the services:
#   HPIS_DEMO_PORT [8780]
#   native models: HPIS_GPU_QWEN / HPIS_GPU_GEMMA [0 / 0],
#                  HPIS_GPU_MEM_UTIL_QWEN / _GEMMA [0.44 / 0.50],
#                  HPIS_MODEL_DIR_QWEN / _GEMMA (local weights), HPIS_MODEL_REPO_QWEN / _GEMMA,
#                  HF_HOME, HF_ENDPOINT, http_proxy, https_proxy, no_proxy, VLLM_API_KEY
#   `vllm` must be on PATH (activate its conda/venv first) for --native-models.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
native=false lan=false uninstall=false
for opt in "$@"; do
  case "$opt" in
    --native-models) native=true ;;
    --lan) lan=true ;;
    --uninstall) uninstall=true ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option $opt" >&2; exit 2 ;;
  esac
done

if ! systemctl --user show-environment >/dev/null 2>&1; then
  cat >&2 <<'MSG'
systemd user services are not available in this session.
Fallback: keep the demo alive inside tmux with an automatic restart loop:
  tmux new -d -s hpis2 'cd ~/HPIS2 && while true; do HPIS_LLM_BACKEND=h200 .venv-demo/bin/python -m demo --port 8780; sleep 5; done'
MSG
  exit 1
fi

if $uninstall; then
  for u in hpis2-demo hpis2-qwen hpis2-gemma; do
    systemctl --user disable --now "$u.service" 2>/dev/null || true
    rm -f "$units/$u.service"
  done
  systemctl --user daemon-reload
  echo "removed"
  exit 0
fi

[[ -x $repo/.venv-demo/bin/python ]] || { echo "missing $repo/.venv-demo; install the demo first (H200部署步骤.md step 5)" >&2; exit 1; }
mkdir -p "$units"
port=${HPIS_DEMO_PORT:-8780}
host=127.0.0.1
$lan && host=0.0.0.0

env_lines() {  # copy selected variables from this shell into the unit
  local var
  for var in "$@"; do
    if [[ -n ${!var:-} ]]; then printf 'Environment="%s=%s"\n' "$var" "${!var}"; fi
  done
}

after=""
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
  after=" hpis2-qwen.service hpis2-gemma.service"
fi

{
  echo "[Unit]"
  echo "Description=HPIS 2.0 demo (web page + HPIS/HPIC services)"
  echo "After=network-online.target$after"
  echo "[Service]"
  echo "WorkingDirectory=$repo"
  echo "Environment=HPIS_LLM_BACKEND=h200"
  echo "Environment=PYTHONUNBUFFERED=1"
  env_lines VLLM_API_KEY HPIS_LLM_QWEN_API_KEY HPIS_LLM_GEMMA_API_KEY HPIS_LLM_QWEN_LABEL HPIS_LLM_GEMMA_LABEL HPIS_DEMO_RUNTIME
  echo "ExecStart=$repo/.venv-demo/bin/python -m demo --host $host --port $port"
  echo "Restart=always"
  echo "RestartSec=5"
  echo "[Install]"
  echo "WantedBy=default.target"
} > "$units/hpis2-demo.service"

systemctl --user daemon-reload
services=(hpis2-demo.service)
$native && services=(hpis2-qwen.service hpis2-gemma.service hpis2-demo.service)
systemctl --user enable --now "${services[@]}"

if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q yes; then
  loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" || \
    echo "WARNING: run 'sudo loginctl enable-linger $USER' so the services survive logout and reboot" >&2
fi

echo
echo "installed: ${services[*]}"
echo "status:    systemctl --user status ${services[*]}"
echo "logs:      journalctl --user -u hpis2-demo -f"
if $lan; then
  echo "page:      http://$(hostname -I 2>/dev/null | awk '{print $1}'):$port   (anyone on the intranet can open it)"
else
  echo "page:      http://127.0.0.1:$port on this server; from a laptop: ssh -NT -L $port:127.0.0.1:$port <server>"
fi
