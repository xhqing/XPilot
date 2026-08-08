#!/usr/bin/env python3
"""Standalone isolated proxy manager for development.

Starts an independent xray instance on ports 2080/2087 without
touching system proxy settings. Completely isolated from the main
xpilot instance (ports 1080/1087).
"""

import os
import sys
import json
import signal
import subprocess
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

# Isolated paths — never overlap with production
ISOLATED_PREFIX = 'xpilot-dev'
PID_FILE = f'/tmp/{ISOLATED_PREFIX}.pid'
XRAY_CONFIG = f'/tmp/{ISOLATED_PREFIX}-xray.json'
STDOUT_LOG = f'/tmp/{ISOLATED_PREFIX}-xray-stdout.log'
STDERR_LOG = f'/tmp/{ISOLATED_PREFIX}-xray-stderr.log'

# Isolated ports — never touch 1080/1087
SOCKS_PORT = 2080
HTTP_PORT = 2087


def find_xray() -> str:
    """Find xray binary."""
    candidates = [
        '/opt/homebrew/bin/xray',
        '/usr/local/bin/xray',
        os.path.expanduser('~/.local/bin/xray'),
        '/opt/local/bin/xray',
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Search via which
    import shutil
    xray = shutil.which('xray')
    if xray:
        return xray
    raise RuntimeError(
        'xray binary not found. Install it:\n'
        '  bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install'
    )


def load_nodes() -> dict:
    """Load nodes from the user config directory.

    Reads the same nodes.json the main xpilot CLI uses
    (``~/.config/xpilot/nodes.json`` or ``$XDG_CONFIG_HOME/xpilot``),
    so all node configuration lives in one place outside the project tree.
    ``PROXY_TOOLKIT_CONFIG_DIR`` overrides the directory wholesale (for tests).
    """
    override = os.environ.get('PROXY_TOOLKIT_CONFIG_DIR')
    if override:
        base = override
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        base = os.path.join(xdg or os.path.expanduser('~/.config'), 'xpilot')
    nodes_path = os.path.join(base, 'nodes.json')
    with open(nodes_path) as f:
        return json.load(f)


def build_xray_config(node: dict) -> dict:
    """Build xray JSON config for the given node."""
    protocol = node['protocol']

    if protocol == 'vmess':
        outbound_settings = {
            'vnext': [{
                'address': node['address'],
                'port': node['port'],
                'users': [{
                    'id': node.get('uuid', ''),
                    'alterId': node.get('alterId', 0),
                    'security': node.get('security', 'auto'),
                }]
            }]
        }
    elif protocol == 'vless':
        outbound_settings = {
            'vnext': [{
                'address': node['address'],
                'port': node['port'],
                'users': [{
                    'id': node.get('uuid', ''),
                    'encryption': 'none',
                }]
            }]
        }
    elif protocol in ('ss', 'shadowsocks'):
        outbound_settings = {
            'servers': [{
                'address': node['address'],
                'port': node['port'],
                'password': node.get('password', ''),
                'method': node.get('security', 'chacha20-ietf-poly1305'),
            }]
        }
    elif protocol == 'trojan':
        outbound_settings = {
            'servers': [{
                'address': node['address'],
                'port': node['port'],
                'password': node.get('password', ''),
            }]
        }
    else:
        raise ValueError(f'Unsupported protocol: {protocol}')

    xray_protocol = 'shadowsocks' if protocol in ('ss', 'shadowsocks') else protocol

    return {
        'log': {'loglevel': 'warning'},
        'inbounds': [
            {
                'port': SOCKS_PORT,
                'protocol': 'socks',
                'settings': {'auth': 'noauth', 'udp': True},
                'sniffing': {'enabled': True, 'destOverride': ['http', 'tls']},
            },
            {
                'port': HTTP_PORT,
                'protocol': 'http',
                'settings': {},
            },
        ],
        'outbounds': [
            {
                'protocol': xray_protocol,
                'settings': outbound_settings,
                'tag': 'proxy',
            },
            {'tag': 'direct', 'protocol': 'freedom', 'settings': {}},
            {'tag': 'block', 'protocol': 'blackhole', 'settings': {}},
        ],
        'routing': {
            'domainStrategy': 'IPIfNonMatch',
            'rules': [],
        },
    }


def start(node_id: str = None) -> int:
    """Start isolated xray instance. Returns PID."""
    if is_running():
        pid = _get_pid()
        logger.info(f'Isolated proxy already running (PID: {pid})')
        return pid

    data = load_nodes()
    nodes = data['nodes']
    default = data.get('default_node')

    if node_id:
        if node_id not in nodes:
            raise ValueError(f'Node not found: {node_id}')
        node = nodes[node_id]
    elif default and default in nodes:
        node = nodes[default]
    else:
        node_id = list(nodes.keys())[0]
        node = nodes[node_id]

    xray_bin = find_xray()
    config = build_xray_config(node)

    # Write config
    with open(XRAY_CONFIG, 'w') as f:
        json.dump(config, f, indent=2)

    # Clear log files
    open(STDOUT_LOG, 'w').close()
    open(STDERR_LOG, 'w').close()

    stdout_f = open(STDOUT_LOG, 'a')
    stderr_f = open(STDERR_LOG, 'a')

    proc = subprocess.Popen(
        [xray_bin, 'run', '-config', XRAY_CONFIG],
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True,
    )
    stdout_f.close()
    stderr_f.close()

    pid = proc.pid
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

    # Verify it started
    time.sleep(1)
    if proc.poll() is not None:
        error = ''
        try:
            with open(STDERR_LOG) as f:
                error = f.read().strip()
        except Exception:
            pass
        raise RuntimeError(f'xray failed to start:\n{error}\nConfig: {XRAY_CONFIG}')

    node_name = node.get('name', node_id)
    protocol = node.get('protocol', '')
    address = node.get('address', '')
    port = node.get('port', 0)

    logger.info(
        f'Isolated proxy started\n'
        f'  PID: {pid}\n'
        f'  Node: {node_name} ({protocol}://{address}:{port})\n'
        f'  SOCKS: 127.0.0.1:{SOCKS_PORT}\n'
        f'  HTTP:  127.0.0.1:{HTTP_PORT}\n'
        f'  System proxy: NOT modified (fully isolated)'
    )
    return pid


def stop() -> bool:
    """Stop isolated xray instance."""
    pid = _get_pid()
    if pid is None:
        logger.info('Isolated proxy is not running')
        return True

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError as e:
        logger.error(f'Failed to stop: {e}')
        return False

    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    logger.info(f'Isolated proxy stopped (PID: {pid})')
    return True


def restart(node_id: str = None) -> int:
    """Restart isolated proxy."""
    stop()
    time.sleep(0.5)
    return start(node_id)


def status() -> dict:
    """Get isolated proxy status."""
    running = is_running()
    result = {
        'running': running,
        'pid': _get_pid() if running else None,
        'socks_port': SOCKS_PORT,
        'http_port': HTTP_PORT,
        'system_proxy_modified': False,
        'note': 'This instance does NOT modify system proxy settings',
    }

    if running and os.path.exists(XRAY_CONFIG):
        try:
            with open(XRAY_CONFIG) as f:
                cfg = json.load(f)
                out = cfg['outbounds'][0]
                result['protocol'] = out['protocol']
                if out['protocol'] == 'shadowsocks':
                    s = out['settings']['servers'][0]
                    result['server'] = f'{s["address"]}:{s["port"]}'
                else:
                    s = out['settings']['vnext'][0]
                    result['server'] = f'{s["address"]}:{s["port"]}'
        except Exception:
            pass

    return result


def is_running() -> bool:
    """Check if isolated proxy is running."""
    pid = _get_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return False


def _get_pid() -> int:
    """Get PID from file."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            pass
    return None


def list_nodes() -> None:
    """List available nodes."""
    data = load_nodes()
    nodes = data['nodes']
    default = data.get('default_node')
    print(f'\n{"ID":<12} {"Name":<20} {"Protocol":<12} {"Address":<35} {"Default"}')
    print('-' * 90)
    for nid, node in nodes.items():
        flag = ' *' if nid == default else ''
        print(f'{nid:<12} {node.get("name",""):<20} {node.get("protocol",""):<12} '
              f'{node.get("address","")}:{node.get("port",0):<5} {flag}')
    print()


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(f'Usage: python isolated_proxy.py <command> [node_id]\n')
        print('Commands:')
        print('  start [node_id]  - Start isolated proxy (default node if omitted)')
        print('  stop             - Stop isolated proxy')
        print('  restart [node_id] - Restart isolated proxy')
        print('  status           - Show status')
        print('  nodes            - List available nodes')
        sys.exit(1)

    cmd = sys.argv[1]
    node_id = sys.argv[2] if len(sys.argv) > 2 else None

    commands = {
        'start': lambda: start(node_id),
        'stop': lambda: stop(),
        'restart': lambda: restart(node_id),
        'status': lambda: print(json.dumps(status(), indent=2)),
        'nodes': lambda: list_nodes(),
    }

    if cmd not in commands:
        print(f'Unknown command: {cmd}')
        sys.exit(1)

    try:
        commands[cmd]()
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
