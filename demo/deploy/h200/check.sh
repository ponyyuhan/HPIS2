#!/usr/bin/env bash
# Check the installed demo's model servers with exactly the settings the service uses
# (~/.config/hpis2/demo.env, written by install_services.sh).
#   ./check.sh            # both models
#   ./check.sh -p qwen    # one model
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
envfile=${XDG_CONFIG_HOME:-$HOME/.config}/hpis2/demo.env
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
[[ -f $envfile ]] || { echo "not installed yet: run ./install_services.sh first (H200部署步骤.md step 5)" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$envfile"
set +a

# With the demo's own Ollama servers (install_services.sh --ollama), give them time to
# start after an install or restart, and say why if they do not.
if [[ -f $units/hpis2-ollama.service && -n ${HPIS_LLM_OLLAMA_URL:-} ]]; then
  # shellcheck source=ollama_wait.sh
  . "$here/ollama_wait.sh"
  if ! hpis2_wait_ollama 90 "$HPIS_LLM_OLLAMA_URL" ${HPIS_LLM_PLAIN_OLLAMA_URL:+"$HPIS_LLM_PLAIN_OLLAMA_URL"}; then
    hpis2_explain_ollama
    exit 1
  fi
fi

cd "$repo"
exec .venv-demo/bin/python -m demo.llm --check "$@"
