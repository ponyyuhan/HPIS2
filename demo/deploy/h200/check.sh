#!/usr/bin/env bash
# Check the installed demo's model servers with exactly the settings the service uses
# (~/.config/hpis2/demo.env, written by install_services.sh).
#   ./check.sh            # both models
#   ./check.sh -p qwen    # one model
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
envfile=${XDG_CONFIG_HOME:-$HOME/.config}/hpis2/demo.env
[[ -f $envfile ]] || { echo "not installed yet: run ./install_services.sh first (H200部署步骤.md step 4)" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$envfile"
set +a
cd "$repo"
exec .venv-demo/bin/python -m demo.llm --check "$@"
