# Sourced by install_services.sh and check.sh: wait for the demo's own Ollama servers and,
# if they do not come up, show why (service state and the end of their log files).

hpis2_state_dir=${XDG_STATE_HOME:-$HOME/.local/state}/hpis2

# hpis2_wait_ollama SECONDS URL... : 0 once every URL answers /api/version, 1 after SECONDS.
hpis2_wait_ollama() {
  local limit=$1 waited=0 url all
  shift
  while :; do
    all=true
    for url in "$@"; do
      curl -s -m 2 --noproxy '*' "$url/api/version" >/dev/null 2>&1 || all=false
    done
    $all && return 0
    ((waited >= limit)) && return 1
    if ((waited == 0)); then echo "等待演示专用的 Ollama 启动（最多 ${limit} 秒）…"; fi
    sleep 3
    waited=$((waited + 3))
  done
}

# hpis2_explain_ollama: what systemd and the logs say about the two demo Ollama servers.
hpis2_explain_ollama() {
  local unit
  echo
  echo "演示专用的 Ollama 没有在运行。下面是它们的状态和最近的日志，请拍下这部分发给我："
  for unit in hpis2-ollama hpis2-ollama-plain; do
    echo "----- $unit"
    systemctl --user show "$unit.service" -p ActiveState -p SubState -p NRestarts -p ExecMainStatus -p ExecStart --no-pager 2>&1 \
      | sed -e 's/^ExecStart=.*path=\([^ ;]*\).*/ExecStart=\1/'
    if [[ -s $hpis2_state_dir/$unit.log ]]; then
      tail -n 15 "$hpis2_state_dir/$unit.log"
    else
      journalctl --user -u "$unit" -n 15 --no-pager -o cat 2>&1 | tail -n 15
    fi
  done
}
