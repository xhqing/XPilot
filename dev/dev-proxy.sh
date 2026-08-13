#!/bin/bash
# =============================================================================
# dev-proxy — Isolated proxy manager for xpilot development
# =============================================================================
# Mirrors the `xpilot` CLI interface exactly, but uses:
#   - Independent ports 2080/2087 (never touches 1080/1087)
#   - No system proxy modification
#   - Independent PID/log files
#   - Shared node/routing config (same data as xpilot)
# =============================================================================

set -euo pipefail

# Resolve the real path of this script (follows symlinks)
resolve_path() {
    local path="$1"
    while [ -L "$path" ]; do
        local dir
        dir="$(cd "$(dirname "$path")" && pwd)"
        path="$(readlink "$path")"
        [[ "$path" != /* ]] && path="$dir/$path"
    done
    echo "$(cd "$(dirname "$path")" && pwd)"
}

SCRIPT_DIR="$(resolve_path "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ISOLATED_PY="${SCRIPT_DIR}/isolated_proxy.py"

# Passthrough wrapper: calls the real xpilot CLI with shared config.
# Uses env var to pass args since python3 -c doesn't receive them in sys.argv.
run_cli() {
    local subcmd="$1"
    shift
    local cli_args=""
    for arg in "$@"; do
        cli_args="$cli_args $arg"
    done
    cd "$PROJECT_ROOT" && RUN_CLI_SUBCMD="$subcmd" RUN_CLI_ARGS="$cli_args" PROXY_TOOLKIT_CONFIG_DIR="$PROJECT_ROOT/config" python3 -B <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.abspath('.'))
subcmd = os.environ['RUN_CLI_SUBCMD']
args = os.environ.get('RUN_CLI_ARGS', '').split()
from xpilot.cli import cli
sys.argv = ['dev-proxy', subcmd] + args
cli(standalone_mode=False)
PYEOF
}

usage() {
    cat <<'EOF'
Usage: dev-proxy <command> [args]

Mirror of `xpilot` CLI — all commands match exactly.

Proxy management:
  start [node]         Start proxy with isolated config (ports 2080/2087)
  stop                 Stop proxy
  restart [node]       Restart proxy
  status [-v]          Show proxy status
  switch <node>        Switch to a different node

Node management (shared config):
  node list [-g group] List all nodes
  node add ...         Add a new node
  node remove <node>   Remove a node
  node edit ...        Edit a node
  node import <url>    Import from subscription URL
  node export [-f fmt] Export nodes configuration

Test:
  test [node]          Test node connectivity
  test -a              Test all nodes
  test --current       Test current node
  test --group <name>  Test nodes in a group
  test --speed         Test upload/download speed

Routing (shared config):
  routing list         List all routing rules
  routing add ...      Add a routing rule
  routing remove ...   Remove a routing rule
  routing domain ...   Domain-to-node routing rules

Config:
  config show          Show current configuration

Help:
  help, --help, -h     Show this help

Isolated proxy runs on 127.0.0.1:2080 (SOCKS) / 2087 (HTTP).
System proxy is NOT modified — your real proxy (1080/1087) is unaffected.
EOF
    exit 0
}

# --- Proxy commands (use isolated proxy, NOT system proxy) ---

cmd_start() {
    local node="${1:-}"
    if [ -n "$node" ]; then
        python3 "$ISOLATED_PY" start "$node"
    else
        python3 "$ISOLATED_PY" start
    fi
}

cmd_stop() {
    python3 "$ISOLATED_PY" stop
}

cmd_restart() {
    local node="${1:-}"
    if [ -n "$node" ]; then
        python3 "$ISOLATED_PY" restart "$node"
    else
        python3 "$ISOLATED_PY" restart
    fi
}

cmd_status() {
    python3 "$ISOLATED_PY" status
}

cmd_switch() {
    local node="${1:?Error: node ID required}"
    python3 "$ISOLATED_PY" restart "$node"
}

# --- Test command ---
# Uses heredoc + env var to pass args (avoids python3 -c sys.argv issues)

cmd_test() {
    local test_args=""
    for arg in "$@"; do
        test_args="$test_args $arg"
    done
    cd "$PROJECT_ROOT" && TEST_CLI_ARGS="$test_args" PROXY_TOOLKIT_SETTINGS_FILE="settings-isolated.json" PROXY_TOOLKIT_CONFIG_DIR="$PROJECT_ROOT/config" python3 -B <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.abspath('.'))
args = os.environ.get('TEST_CLI_ARGS', '').split()
from xpilot.cli import cli
sys.argv = ['dev-proxy', 'test'] + args
cli(standalone_mode=False)
PYEOF
}

# --- Main dispatch (mirrors xpilot exactly) ---

cmd="${1:-help}"
shift 2>/dev/null || true

case "$cmd" in
    start)       cmd_start "$@" ;;
    stop)        cmd_stop ;;
    restart)     cmd_restart "$@" ;;
    status)      cmd_status ;;
    switch)      cmd_switch "$@" ;;
    test)        cmd_test "$@" ;;
    # Everything else passes through to xpilot CLI
    node)        run_cli node "$@" ;;
    routing)     run_cli routing "$@" ;;
    config)      run_cli config "$@" ;;
    subscription) run_cli subscription "$@" ;;
    help|--help|-h) usage ;;
    *)
        echo "Unknown command: $cmd"
        usage
        ;;
esac
