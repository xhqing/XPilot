"""CLI entry point for xpilot."""

import os
import signal
import subprocess
import sys
import time
import logging
import platform
import click

from . import __version__
from .config import Config, ConfigError
from .updater import perform_update, perform_rollback, UpdateError
from .utils import get_config_dir

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging to write to both the terminal and a log file.

    The log level and file path come from settings.json (``log_level`` and
    ``log_file``), defaulting to WARNING and ``/tmp/xpilot.log``. The file
    handler is essential for debugging: the auto-switch monitor runs as a
    detached background daemon whose own stdout/stderr are discarded, so
    without a persistent log file its output would be lost for good.
    """
    try:
        settings = Config().load_config('settings.json')
    except Exception:
        settings = {}
    log_level = settings.get('log_level', 'warning')
    log_file = settings.get('log_file', '/tmp/xpilot.log')
    level = getattr(logging, log_level.upper(), logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)
    # Repeated calls (several commands invoke setup_logging) must not stack
    # duplicate handlers on the root logger.
    if getattr(root, '_xpilot_handlers_configured', False):
        return
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        '%Y-%m-%d %H:%M:%S'
    )
    # File handler (append mode) — preserves background daemon output.
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        pass
    # Stream handler — immediate feedback in interactive runs.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)
    root._xpilot_handlers_configured = True


AUTO_SWITCH_PID_FILE = '/tmp/xpilot-auto-switch.pid'
LAUNCHD_LABEL = 'com.xpilot.monitor'
LAUNCHD_PLIST_PATH = os.path.expanduser(
    f'~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist'
)


def _stop_monitor_daemon():
    """Stop the background auto-switch monitor daemon if running."""
    if not os.path.exists(AUTO_SWITCH_PID_FILE):
        return
    try:
        with open(AUTO_SWITCH_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        except OSError:
            pass
        os.remove(AUTO_SWITCH_PID_FILE)
    except Exception as e:
        click.echo(f'Warning: failed to stop monitor daemon: {e}', err=True)


def _spawn_monitor_daemon():
    """Spawn a background process that runs the auto-switch monitor loop.

    This avoids blocking the CLI command (e.g. when run by an agent/IDE) while
    keeping the watchdog alive after the command returns.
    """
    _stop_monitor_daemon()
    cmd = [sys.executable, '-m', 'xpilot.cli', 'monitor']
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        click.echo(f'Auto-switch monitor running in background (PID: {proc.pid})')
        return proc.pid
    except Exception as e:
        click.echo(f'Failed to spawn monitor daemon: {e}', err=True)
        raise


def get_managers():
    """Initialize and return all manager instances."""
    config = Config()
    from .node_manager import NodeManager
    from .routing_manager import RoutingManager
    from .proxy_manager import ProxyManager
    from .health_checker import HealthChecker
    from .auto_switch import AutoSwitch

    node_manager = NodeManager(config)
    routing_manager = RoutingManager(config)
    proxy_manager = ProxyManager(config, node_manager, routing_manager)
    health_checker = HealthChecker(proxy_manager)
    health_checker.set_node_manager(node_manager)
    auto_switch = AutoSwitch(config, node_manager, health_checker, proxy_manager)

    return config, node_manager, routing_manager, proxy_manager, health_checker, auto_switch


@click.group(invoke_without_command=True)
@click.option('--update', is_flag=True,
              help='Update xpilot to the latest GitHub release, then exit.')
@click.version_option(version=__version__, prog_name='xpilot')
def cli(update):
    """xpilot - A CLI proxy management tool with xray backend."""
    ctx = click.get_current_context()
    if update:
        # --update is a terminal action: check GitHub for a newer release and
        # install it via pip, then exit without dispatching to any subcommand.
        try:
            perform_update(printer=click.echo)
        except UpdateError as e:
            click.echo(f'Update failed: {e}', err=True)
            sys.exit(1)
        ctx.exit()
    if ctx.invoked_subcommand is None:
        # Bare `xpilot` with no subcommand: show help instead of erroring.
        click.echo(ctx.get_help())
        ctx.exit()


# ==================== Init Command ====================

@cli.command()
@click.option('-f', '--force', is_flag=True, help='Force overwrite existing configs')
def init(force):
    """Initialize configuration files."""
    config = Config()
    try:
        created = config.init_default_configs(force=force)
        if created:
            click.echo(f'Created config files: {", ".join(created)}')
        else:
            click.echo('Config files already exist. Use -f to force overwrite.')
        click.echo(f'Config directory: {config.config_dir}')
    except ConfigError as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


# ==================== Start/Stop/Restart/Status Commands ====================

@cli.command()
@click.argument('node', required=False)
def start(node):
    """Start proxy service in the background and return immediately."""
    setup_logging()
    _, node_manager, _, proxy_manager, health_checker, _ = get_managers()
    try:
        if node is None:
            # Auto-select fastest node
            click.echo('Auto-selecting fastest node...')
            nodes = node_manager.list_nodes()
            best_node = None
            best_latency = float('inf')
            for n in nodes:
                try:
                    lat = health_checker.check_latency(n, timeout=3)
                    click.echo(f'  {n["id"]}: {lat}ms')
                    if lat < best_latency:
                        best_latency = lat
                        best_node = n['id']
                except Exception:
                    click.echo(f'  {n["id"]}: timeout')
            if best_node:
                node = best_node
                click.echo(click.style(f'Selected fastest node: {node} ({best_latency:.0f}ms)', fg='green'))
            else:
                click.echo('No available node found, using default', err=True)
                sys.exit(1)
        
        node = node_manager.resolve_node_ref(node)
        node_name = proxy_manager.start(node)
        click.echo(click.style(f'Proxy started (node: {node_name})', fg='green'))
        
        # Always run in the background: spawn a detached monitor process so the
        # CLI command returns immediately while the watchdog stays alive.
        _spawn_monitor_daemon()
    except Exception as e:
        click.echo(f'Failed to start proxy: {e}', err=True)
        sys.exit(1)


@cli.command()
def stop():
    """Stop proxy service and background monitor daemon."""
    setup_logging()
    _, _, _, proxy_manager, _, _ = get_managers()
    try:
        _stop_monitor_daemon()
        proxy_manager.stop()
        click.echo('Proxy stopped')
    except Exception as e:
        click.echo(f'Failed to stop proxy: {e}', err=True)
        sys.exit(1)


@cli.command()
def restart():
    """Restart proxy service in the background and return immediately."""
    setup_logging()
    _, node_manager, _, proxy_manager, health_checker, _ = get_managers()
    try:
        # Auto-select fastest node
        click.echo('Auto-selecting fastest node...')
        nodes = node_manager.list_nodes()
        best_node = None
        best_latency = float('inf')
        for n in nodes:
            try:
                lat = health_checker.check_latency(n, timeout=3)
                click.echo(f'  {n["id"]}: {lat}ms')
                if lat < best_latency:
                    best_latency = lat
                    best_node = n['id']
            except Exception:
                click.echo(f'  {n["id"]}: timeout')
        if best_node:
            node = best_node
            click.echo(click.style(f'Selected fastest node: {node} ({best_latency:.0f}ms)', fg='green'))
            proxy_manager.stop()
            proxy_manager.start(node)
            click.echo(click.style(f'Proxy restarted (node: {node})', fg='green'))
        else:
            proxy_manager.restart()
            click.echo('Proxy restarted (no node available)')
        
        # Always run in the background: spawn a detached monitor process so the
        # CLI command returns immediately while the watchdog stays alive.
        _spawn_monitor_daemon()
    except Exception as e:
        click.echo(f'Failed to restart proxy: {e}', err=True)
        sys.exit(1)


@cli.command()
def monitor():
    """Run the auto-switch monitor loop (internal background daemon)."""
    setup_logging()
    _, _, _, proxy_manager, _, auto_switch = get_managers()
    try:
        with open(AUTO_SWITCH_PID_FILE, 'w') as f:
            f.write(str(os.getpid()))

        auto_switch.start()
        if auto_switch._running:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                click.echo('\nStopping monitor...')
                auto_switch.stop()
                proxy_manager.stop()
        else:
            click.echo('Auto-switch is disabled, exiting.')
    except Exception as e:
        click.echo(f'Failed to start monitor: {e}', err=True)
        sys.exit(1)


def _probe_proxy_latency(health_checker, socks_port, timeout=8):
    """经当前在跑的代理（socks 端口）访问 generate_204，返回真实流量延迟标签。"""
    if not socks_port:
        return 'N/A'
    try:
        ok, _code, latency_ms = health_checker._curl_through_socks(socks_port, timeout=timeout)
        if ok and latency_ms is not None:
            return click.style(f'{latency_ms}ms', fg='green')
        return click.style('不通', fg='red')
    except Exception as e:
        logger.warning(f'Proxy latency probe failed: {e}')
        return 'N/A'


def _probe_speed(health_checker, socks_port, size_bytes, timeout=30, direct=False):
    """测下载网速，返回 Mbps 标签。direct=True 测直连，否则走指定 socks 端口。"""
    try:
        speed, elapsed = health_checker._curl_download(
            socks_port, size_bytes, timeout=timeout, direct=direct)
        if speed is not None:
            return click.style(f'{speed:.1f} Mbps（{elapsed:.1f}s）', fg='green')
        return click.style('测速失败', fg='red')
    except Exception as e:
        logger.warning(f'Speed probe failed: {e}')
        return 'N/A'


def _probe_direct_latency(health_checker, timeout=8):
    """直连（不走代理）测 generate_204 延迟，返回标签（用于和经节点延迟对比）。"""
    try:
        latency_ms = health_checker.check_direct_latency(timeout=timeout)
        if latency_ms is not None:
            return click.style(f'{latency_ms}ms', fg='green')
        return click.style('不通', fg='red')
    except Exception as e:
        logger.warning(f'Direct latency probe failed: {e}')
        return 'N/A'


@cli.command()
@click.option('-v', '--verbose', is_flag=True, help='Show detailed status')
@click.option('--no-speed', is_flag=True, help='跳过网速测试（只看延迟）')
def status(verbose, no_speed):
    """显示代理状态（当前节点延迟、经节点网速、直连网速）。"""
    config, node_manager, _, proxy_manager, health_checker, _ = get_managers()
    speed_bytes = 5 * 1_000_000  # status 测速默认下载 5MB（经节点 + 直连各一次）
    try:
        info = proxy_manager.get_status()
        if info['running']:
            click.echo(click.style('Running', fg='green'))
            click.echo(f'  PID: {info["pid"]}')
            click.echo(f'  Current node: {info.get("node_name", "")} ({info["current_node"]})')
            socks_port = info.get('socks_port', 1080)
            click.echo(f'  延迟(经节点): {_probe_proxy_latency(health_checker, socks_port)}')
            click.echo(f'  延迟(直连): {_probe_direct_latency(health_checker)}')
            click.echo(f'  SOCKS port: {socks_port}')
            click.echo(f'  HTTP port: {info.get("http_port", "N/A")}')
            if not no_speed:
                click.echo('  测速中（经节点 + 直连，各 5MB，约 10s）...')
                click.echo(f'  经节点网速: {_probe_speed(health_checker, socks_port, speed_bytes)}')
                click.echo(f'  直连网速: {_probe_speed(health_checker, None, speed_bytes, direct=True)}')
        else:
            click.echo(click.style('Stopped', fg='red'))
            if info.get('current_node'):
                click.echo(f'  Last used node: {info["current_node"]}')
            click.echo(f'  延迟(直连): {_probe_direct_latency(health_checker)}')
            if not no_speed:
                click.echo('  测速中（直连 5MB）...')
                click.echo(f'  直连网速: {_probe_speed(health_checker, None, speed_bytes, direct=True)}')

        # auto_switch 状态提示：关闭时当前节点失效不会自动切换到最优。
        try:
            auto_cfg = config.load_config('settings.json').get('auto_switch', {})
            if not auto_cfg.get('enabled', False):
                click.echo(click.style(
                    '  auto_switch 未开启：当前节点失效不会自动切换到最优。'
                    '开启： xpilot config set auto_switch.enabled true',
                    fg='yellow'))
        except Exception:
            pass
    except Exception as e:
        click.echo(f'Error getting status: {e}', err=True)
        sys.exit(1)


@cli.command()
@click.argument('node')
def switch(node):
    """Switch to a different node."""
    setup_logging()
    _, node_manager, _, proxy_manager, _, _ = get_managers()
    try:
        # Resolve ID or name, then validate
        node = node_manager.resolve_node_ref(node)
        node_manager.get_node(node)
        was_running = proxy_manager.is_running()
        if was_running:
            proxy_manager.stop()
        proxy_manager.start(node)
        node_manager.set_default_node(node)
        click.echo(f'Switched to node: {node}')
    except Exception as e:
        click.echo(f'Failed to switch: {e}', err=True)
        sys.exit(1)


# ==================== Node Management Commands ====================

@cli.group()
def node():
    """Node management commands."""
    pass


@node.command('list')
@click.option('-g', '--group', default=None, help='Filter by group')
def node_list(group):
    """List all nodes."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        nodes = node_manager.list_nodes(filter_group=group)
        if not nodes:
            click.echo('No nodes found.')
            return

        default_node = node_manager.get_default_node()
        for n in nodes:
            marker = '* ' if n['id'] == default_node else '  '
            latency = f'{n.get("latency", 0)}ms' if n.get('latency', 0) > 0 else 'N/A'
            dl = n.get('download_speed')
            ul = n.get('upload_speed')
            speed_info = ''
            if dl or ul:
                dl_str = f'{dl}Mbps' if dl else 'N/A'
                ul_str = f'{ul}Mbps' if ul else 'N/A'
                speed_info = f' [DL: {dl_str} | UL: {ul_str}]'
            click.echo(f'{marker}{n["id"]}: {n["name"]} [{n["protocol"]}] '
                       f'{n["address"]}:{n["port"]} - {latency}{speed_info}')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@node.command('add')
@click.option('--name', required=True, help='Node name')
@click.option('--protocol', required=True, type=click.Choice(['vmess', 'vless', 'trojan', 'ss']),
              help='Protocol type')
@click.option('--address', required=True, help='Server address')
@click.option('--port', required=True, type=int, help='Server port')
@click.option('--uuid', default=None, help='UUID (for VLess/VMess)')
@click.option('--password', default=None, help='Password (for Trojan/SS)')
@click.option('--alter-id', default=0, type=int, help='AlterID (for VMess)')
@click.option('--security', default='auto', help='Security/encryption method')
@click.option('--network', default='tcp', help='Network transport (tcp/ws/h2/grpc)')
@click.option('--tls', is_flag=True, help='Enable TLS')
@click.option('--servername', default=None, help='TLS server name (SNI)')
@click.option('--group', default='default', help='Group name')
def node_add(name, protocol, address, port, uuid, password, alter_id, security,
             network, tls, servername, group):
    """Add a new node."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        node_info = {
            'name': name,
            'protocol': protocol,
            'address': address,
            'port': port,
            'uuid': uuid,
            'password': password,
            'alterId': alter_id,
            'security': security,
            'network': network,
            'tls': tls,
            'servername': servername or address,
            'group': group,
        }
        node_id = node_manager.add_node(node_info)
        click.echo(f'Node added: {node_id} ({name})')
    except Exception as e:
        click.echo(f'Failed to add node: {e}', err=True)
        sys.exit(1)


@node.command('remove')
@click.argument('node')
def node_remove(node):
    """Remove a node."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        node = node_manager.resolve_node_ref(node)
        node_manager.remove_node(node)
        click.echo(f'Node removed: {node}')
    except Exception as e:
        click.echo(f'Failed to remove node: {e}', err=True)
        sys.exit(1)


@node.command('edit')
@click.argument('node')
@click.option('--name', default=None, help='New name')
@click.option('--address', default=None, help='New address')
@click.option('--port', default=None, type=int, help='New port')
@click.option('--uuid', default=None, help='New UUID')
@click.option('--password', default=None, help='New password')
@click.option('--group', default=None, help='New group')
@click.option('--tls', is_flag=True, default=None, help='Toggle TLS')
@click.option('--servername', default=None, help='New server name')
def node_edit(node, name, address, port, uuid, password, group, tls, servername):
    """Edit node configuration."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        updates = {}
        if name:
            updates['name'] = name
        if address:
            updates['address'] = address
        if port:
            updates['port'] = port
        if uuid:
            updates['uuid'] = uuid
        if password:
            updates['password'] = password
        if group:
            updates['group'] = group
        if tls is not None:
            updates['tls'] = tls
        if servername:
            updates['servername'] = servername

        if not updates:
            click.echo('No changes specified')
            return

        node = node_manager.resolve_node_ref(node)
        node_manager.update_node(node, updates)
        click.echo(f'Node updated: {node}')
    except Exception as e:
        click.echo(f'Failed to edit node: {e}', err=True)
        sys.exit(1)


@node.command('import')
@click.argument('url')
def node_import(url):
    """Import nodes from subscription URL."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        count = node_manager.import_from_subscription(url)
        click.echo(f'Imported {count} nodes')
    except Exception as e:
        click.echo(f'Failed to import: {e}', err=True)
        sys.exit(1)


@node.command('export')
@click.option('-f', '--format', 'fmt', default='json',
              type=click.Choice(['json', 'yaml']), help='Export format')
def node_export(fmt):
    """Export nodes configuration."""
    _, node_manager, _, _, _, _ = get_managers()
    try:
        output = node_manager.export_nodes(format=fmt)
        click.echo(output)
    except Exception as e:
        click.echo(f'Failed to export: {e}', err=True)
        sys.exit(1)


# ==================== Test Commands ====================

def _run_connectivity_test(node, test_all, current, group, tcp_only):
    """连通性检测（原 ``xpilot test`` 的逻辑，供 test group 无子命令时复用）。"""
    _, node_manager, _, _, health_checker, _ = get_managers()
    try:
        if test_all:
            node_ids = node_manager.get_node_ids()
        elif current:
            default = node_manager.get_default_node()
            if not default:
                click.echo('No default node set')
                sys.exit(1)
            node_ids = [default]
        elif group:
            nodes = node_manager.list_nodes(filter_group=group)
            node_ids = [n['id'] for n in nodes]
        elif node:
            node_ids = [node_manager.resolve_node_ref(node)]
        else:
            click.echo('Specify a node, --all, --current, or --group')
            return

        if not node_ids:
            click.echo('No nodes to test')
            return

        if tcp_only:
            click.echo(f'Testing {len(node_ids)} node(s) [TCP only]...')
            results = health_checker.batch_check(node_ids)
            results = health_checker.sort_by_latency(results)
            for r in results:
                ok = r.get('connected')
                st = click.style('OK', fg='green') if ok else click.style('FAIL', fg='red')
                latency = f'{r["latency"]}ms' if r.get('latency', -1) > 0 else 'N/A'
                error = f'  ({r["error"]})' if r.get('error') else ''
                click.echo(f'  {r["name"]}: {st} {latency}{error}')
            return

        # 默认：真实流量检测（并发）。
        click.echo(f'Testing {len(node_ids)} node(s) with real traffic '
                   f'(concurrent, may take ~15s)...')
        results = health_checker.batch_check_real(node_ids)
        # 可用排前、按代理延迟升序；不可用排后。
        results.sort(key=lambda r: (not r.get('ok'), r.get('latency_ms') or float('inf')))

        usable = [r for r in results if r.get('ok')]
        default_id = node_manager.get_default_node()
        for r in results:
            mark = '* ' if r.get('id') == default_id else '  '
            tcp = r.get('tcp_ms')
            tcp_str = click.style(f'{tcp}ms', fg='green') if tcp else click.style('超时', fg='red')
            if r.get('ok'):
                proxy_str = click.style(
                    f'OK ({r.get("latency_ms")}ms, http={r.get("http_code")})', fg='green')
            else:
                proxy_str = click.style(f'FAIL ({r.get("error") or "不通"})', fg='red')
            click.echo(f'{mark}{r.get("name")}  [TCP {tcp_str}]  [代理 {proxy_str}]')

        click.echo(f'直连延迟: {_probe_direct_latency(health_checker)}（基准，对比各节点经节点延迟）')
        click.echo(click.style(f'\n代理可用：{len(usable)}/{len(results)}',
                               fg='green' if usable else 'red'))
        if not usable:
            click.echo(click.style(
                '全部节点代理流量不通——多为密钥/IP 过期。若有订阅源：'
                '`xpilot subscription update`；否则先 `subscription add <url> <name>`。',
                fg='yellow'))
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@cli.group(invoke_without_command=True)
@click.option('--node', 'node', default=None, help='测试指定节点（名称或 ID）')
@click.option('--all-nodes', '-a', 'test_all', is_flag=True, help='Test all nodes')
@click.option('--current', is_flag=True, help='Test current node only')
@click.option('--group', default=None, help='Test nodes in a group')
@click.option('--tcp', 'tcp_only', is_flag=True,
              help='仅做 TCP 快速检测（不验证代理流量是否真能出去）')
@click.pass_context
def test(ctx, node, test_all, current, group, tcp_only):
    """测试节点连通性 / 网速（无参数=连通性，speed 子命令=测网速）。

    不带子命令时执行连通性检测（默认真实流量，--tcp 走 TCP 快速）。
    子命令 speed 测网速（经代理或直连）。
    """
    setup_logging()
    if ctx.invoked_subcommand is None:
        _run_connectivity_test(node, test_all, current, group, tcp_only)


@test.command('speed')
@click.option('--all-nodes', '-a', 'test_all', is_flag=True, help='测所有节点经代理网速')
@click.option('--direct', is_flag=True, help='测直连网速（不走代理）')
@click.option('--current', is_flag=True, help='测当前节点经代理网速')
@click.option('--size', default=10, type=int, help='每次下载大小（MB，默认 10）')
def test_speed(test_all, direct, current, size):
    """测网速：经代理下载或直连下载（Cloudflare 测速端点）。"""
    setup_logging()
    _, node_manager, _, _, health_checker, _ = get_managers()
    size_bytes = size * 1_000_000
    try:
        if direct:
            click.echo(f'测直连网速（不走代理，下载 {size}MB）...')
            r = health_checker.check_direct_speed(size_bytes=size_bytes)
            if r['speed_mbps'] is not None:
                click.echo(click.style(
                    f'  直连: {r["speed_mbps"]:.1f} Mbps（用时 {r["elapsed"]:.1f}s）',
                    fg='green'))
            else:
                click.echo(click.style(f'  直连测速失败: {r["error"]}', fg='red'))
            return

        if test_all:
            node_ids = node_manager.get_node_ids()
        elif current:
            default = node_manager.get_default_node()
            if not default:
                click.echo('No default node set')
                sys.exit(1)
            node_ids = [default]
        else:
            click.echo('Specify --all-nodes, --current, or --direct')
            return
        if not node_ids:
            click.echo('No nodes to test')
            return

        click.echo(f'测 {len(node_ids)} 个节点经代理网速（每节点下载 {size}MB，并发）...')
        results = health_checker.batch_check_speed(node_ids, size_bytes=size_bytes)
        # 速度快的排前面，测速失败的排后面。
        results.sort(key=lambda r: (r.get('speed_mbps') is None,
                                    -(r.get('speed_mbps') or 0)))
        default_id = node_manager.get_default_node()
        for r in results:
            mark = '* ' if r.get('id') == default_id else '  '
            if r.get('speed_mbps') is not None:
                spd = click.style(f'{r["speed_mbps"]:6.1f} Mbps', fg='green')
                click.echo(f'{mark}{r.get("name")}  {spd}  ({r["elapsed"]:.1f}s)')
            else:
                click.echo(f'{mark}{r.get("name")}  {click.style("FAIL", fg="red")}'
                           f'  ({r.get("error")})')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


# ==================== Routing Commands ====================

@cli.group()
def routing():
    """Routing rule management commands."""
    pass


@routing.command('list')
def routing_list():
    """List all routing rules."""
    _, node_manager, routing_manager, _, _, _ = get_managers()
    try:
        proxy_rules = routing_manager.get_proxy_rules()
        direct_rules = routing_manager.get_direct_rules()
        block_rules = routing_manager.get_block_rules()
        domain_rules = routing_manager.get_domain_rules()

        click.echo('Proxy rules:')
        for r in proxy_rules:
            click.echo(f'  [PROXY] {r}')

        click.echo('\nDirect rules:')
        for r in direct_rules:
            click.echo(f'  [DIRECT] {r}')

        if block_rules:
            click.echo('\nBlock rules:')
            for r in block_rules:
                click.echo(f'  [BLOCK] {r}')

        if domain_rules:
            click.echo('\nDomain-to-node rules:')
            for i, rule in enumerate(domain_rules):
                desc = rule.get('description', ', '.join(rule['domains']))
                click.echo(f'  [{i}] {desc} -> {rule["node_id"]}')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@routing.command('add')
@click.argument('type', type=click.Choice(['proxy', 'direct', 'block']))
@click.argument('rule')
def routing_add(type, rule):
    """Add a routing rule."""
    _, _, routing_manager, _, _, _ = get_managers()
    try:
        if type == 'proxy':
            routing_manager.add_proxy_rule(rule)
        elif type == 'direct':
            routing_manager.add_direct_rule(rule)
        elif type == 'block':
            routing_manager.add_block_rule(rule)
        click.echo(f'Added {type} rule: {rule}')
    except Exception as e:
        click.echo(f'Failed to add rule: {e}', err=True)
        sys.exit(1)


@routing.command('remove')
@click.argument('rule')
def routing_remove(rule):
    """Remove a routing rule."""
    _, _, routing_manager, _, _, _ = get_managers()
    try:
        if routing_manager.remove_rule(rule):
            click.echo(f'Removed rule: {rule}')
        else:
            click.echo(f'Rule not found: {rule}')
    except Exception as e:
        click.echo(f'Failed to remove rule: {e}', err=True)
        sys.exit(1)


# ==================== Domain Routing Commands ====================

@routing.group()
def domain():
    """Domain-to-node routing rules (specific sites use specific nodes)."""
    pass


@domain.command('add')
@click.option('--domains', '-d', required=True, multiple=True,
              help='Domain patterns (can be specified multiple times, e.g., -d github.com -d *.github.io)')
@click.option('--node', '-n', required=True, help='Node ID to route these domains through')
@click.option('--desc', default='', help='Description for this rule')
def domain_add(domains, node, desc):
    """Add a domain-to-node routing rule.

    Example: Route GitHub through a specific node.

        xpilot routing domain add -d github.com -d '*.github.io' -d api.github.com -n github_node --desc "GitHub"
    """
    _, node_manager, routing_manager, _, _, _ = get_managers()
    try:
        # Resolve node ID or name, then validate
        node = node_manager.resolve_node_ref(node)
        node_manager.get_node(node)
        domain_list = list(domains)
        if not domain_list:
            click.echo('At least one domain is required')
            sys.exit(1)
        description = desc or ', '.join(domain_list)
        routing_manager.add_domain_rule(domain_list, node, description)
        click.echo(f'Added domain rule: {description} -> {node}')
    except Exception as e:
        click.echo(f'Failed to add domain rule: {e}', err=True)
        sys.exit(1)


@domain.command('remove')
@click.argument('index', type=int)
def domain_remove(index):
    """Remove a domain rule by index.

    Use `xpilot routing list` to see rule indices.

    Example:

        xpilot routing domain remove 0
    """
    _, _, routing_manager, _, _, _ = get_managers()
    try:
        if routing_manager.remove_domain_rule(index):
            click.echo(f'Removed domain rule at index {index}')
        else:
            click.echo(f'No rule at index {index}')
    except Exception as e:
        click.echo(f'Failed to remove domain rule: {e}', err=True)
        sys.exit(1)


@domain.command('clear')
@click.option('-f', '--force', is_flag=True, help='Force clear without confirmation')
def domain_clear(force):
    """Clear all domain-to-node rules."""
    _, _, routing_manager, _, _, _ = get_managers()
    try:
        if not force:
            if not click.confirm('Clear all domain rules?'):
                click.echo('Cancelled')
                return
        routing_manager.clear_domain_rules()
        click.echo('Cleared all domain rules')
    except Exception as e:
        click.echo(f'Failed to clear domain rules: {e}', err=True)
        sys.exit(1)


# ==================== Subscription Commands ====================

@cli.group()
def subscription():
    """Subscription management commands."""
    pass


@subscription.command('add')
@click.argument('url')
@click.option('--name', required=True, help='Subscription name')
def subscription_add(url, name):
    """Add a subscription source."""
    config, _, _, _, _, _ = get_managers()
    try:
        settings = config.load_config('settings.json')
        subs = settings.setdefault('subscriptions', {})
        subs[name] = url
        config.save_config('settings.json', settings)
        click.echo(f'Subscription added: {name}')
    except Exception as e:
        click.echo(f'Failed to add subscription: {e}', err=True)
        sys.exit(1)


@subscription.command('update')
@click.argument('name', required=False)
def subscription_update(name):
    """Update subscription(s)."""
    config, node_manager, _, _, _, _ = get_managers()
    try:
        settings = config.load_config('settings.json')
        subs = settings.get('subscriptions', {})
        if not subs:
            click.echo('No subscriptions configured')
            return

        if name:
            if name not in subs:
                click.echo(f'Subscription not found: {name}')
                sys.exit(1)
            subs = {name: subs[name]}

        for sub_name, url in subs.items():
            click.echo(f'Updating {sub_name}...')
            try:
                # update 语义：按名字刷新已有节点的连接字段（密钥/IP 轮换后真正生效），
                # 同时导入新增节点。仅追加新节点请用 `node import`。
                count = node_manager.import_from_subscription(url, update_existing=True, source=sub_name)
                click.echo(f'  Refreshed {count} nodes from {sub_name}')
            except Exception as e:
                click.echo(f'  Failed: {e}', err=True)
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@subscription.command('list')
def subscription_list():
    """List subscription sources."""
    config, _, _, _, _, _ = get_managers()
    try:
        settings = config.load_config('settings.json')
        subs = settings.get('subscriptions', {})
        if not subs:
            click.echo('No subscriptions configured')
            return
        for name, url in subs.items():
            click.echo(f'  {name}: {url}')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@subscription.command('remove')
@click.argument('name')
def subscription_remove(name):
    """Remove a subscription source."""
    config, _, _, _, _, _ = get_managers()
    try:
        settings = config.load_config('settings.json')
        subs = settings.get('subscriptions', {})
        if name not in subs:
            click.echo(f'Subscription not found: {name}')
            sys.exit(1)
        del subs[name]
        config.save_config('settings.json', settings)
        click.echo(f'Subscription removed: {name}')
    except Exception as e:
        click.echo(f'Failed to remove subscription: {e}', err=True)
        sys.exit(1)


# ==================== Launchd Service Commands (macOS) ====================

def _build_launchd_plist() -> str:
    """Build the launchd plist content for the monitor daemon."""
    python_bin = sys.executable
    # Use the project root so the package is importable.
    workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stdout_log = '/tmp/xpilot-monitor.log'
    stderr_log = '/tmp/xpilot-monitor.err'

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-m</string>
        <string>xpilot.cli</string>
        <string>monitor</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""


@cli.command('install-launchd')
def install_launchd():
    """Install a launchd service to keep the monitor daemon alive (macOS).

    With KeepAlive=true, launchd will automatically restart the monitor if it
    crashes or is killed, and start it on login. This provides an outer layer
    of protection beyond the in-process watchdog.
    """
    if platform.system() != 'Darwin':
        click.echo('launchd integration is only available on macOS', err=True)
        sys.exit(1)

    try:
        # Unload existing service if present
        if os.path.exists(LAUNCHD_PLIST_PATH):
            subprocess.run(
                ['launchctl', 'unload', LAUNCHD_PLIST_PATH],
                capture_output=True
            )

        # Write plist
        os.makedirs(os.path.dirname(LAUNCHD_PLIST_PATH), exist_ok=True)
        plist_content = _build_launchd_plist()
        with open(LAUNCHD_PLIST_PATH, 'w') as f:
            f.write(plist_content)

        # Load the service
        result = subprocess.run(
            ['launchctl', 'load', LAUNCHD_PLIST_PATH],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            click.echo(f'Failed to load launchd service: {result.stderr.strip()}', err=True)
            sys.exit(1)

        click.echo(click.style('launchd service installed and started', fg='green'))
        click.echo(f'  Label: {LAUNCHD_LABEL}')
        click.echo(f'  Plist: {LAUNCHD_PLIST_PATH}')
        click.echo(f'  Logs:  /tmp/xpilot-monitor.log')
        click.echo('  The monitor will auto-start on login and restart if killed.')
    except PermissionError as e:
        click.echo(f'Permission denied: {e}', err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f'Failed to install launchd service: {e}', err=True)
        sys.exit(1)


@cli.command('uninstall-launchd')
def uninstall_launchd():
    """Uninstall the launchd monitor service (macOS)."""
    if platform.system() != 'Darwin':
        click.echo('launchd integration is only available on macOS', err=True)
        sys.exit(1)

    try:
        if not os.path.exists(LAUNCHD_PLIST_PATH):
            click.echo('launchd service is not installed')
            return

        subprocess.run(
            ['launchctl', 'unload', LAUNCHD_PLIST_PATH],
            capture_output=True
        )
        os.remove(LAUNCHD_PLIST_PATH)
        click.echo(click.style('launchd service removed', fg='green'))
        click.echo(f'  Removed: {LAUNCHD_PLIST_PATH}')
    except Exception as e:
        click.echo(f'Failed to uninstall launchd service: {e}', err=True)
        sys.exit(1)


# ==================== Config Commands ====================

@cli.group()
def config():
    """Configuration management commands."""
    pass


@config.command('show')
def config_show():
    """Show current configuration."""
    cfg, _, _, _, _, _ = get_managers()
    try:
        settings = cfg.load_config('settings.json')
        import json
        click.echo(json.dumps(settings, indent=2))
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        sys.exit(1)


@config.command('set')
@click.argument('key')
@click.argument('value')
def config_set(key, value):
    """Set a configuration value."""
    cfg, _, _, _, _, _ = get_managers()
    try:
        # Try to convert value types
        if value.lower() in ('true', 'false'):
            value = value.lower() == 'true'
        elif value.isdigit():
            value = int(value)
        cfg.set_setting(key, value)
        click.echo(f'Set {key} = {value}')
    except Exception as e:
        click.echo(f'Failed to set config: {e}', err=True)
        sys.exit(1)


@config.command('reset')
@click.option('-f', '--force', is_flag=True, help='Force reset without confirmation')
def config_reset(force):
    """Reset configuration to defaults."""
    if not force:
        if not click.confirm('This will overwrite all configuration files. Continue?'):
            click.echo('Cancelled')
            return
    cfg, _, _, _, _, _ = get_managers()
    try:
        created = cfg.init_default_configs(force=True)
        click.echo(f'Reset config files: {", ".join(created)}')
    except Exception as e:
        click.echo(f'Failed to reset config: {e}', err=True)
        sys.exit(1)


# ==================== Rollback Command ====================

@cli.command()
@click.option('--version', '-v', 'version', default=None,
              help='Target version to roll back to (e.g. 0.1.1). '
                   'If omitted, rolls back to the previous release.')
def rollback(version):
    """Roll back xpilot to a specific or the previous GitHub release.

    Without --version, installs the release immediately older than the
    currently running one. With --version X.Y.Z, installs that exact
    release. Installation reuses the same GitHub-Release assets as
    --update (wheel, then sdist, then git tag).
    """
    try:
        perform_rollback(version, printer=click.echo)
    except UpdateError as e:
        click.echo(f'Rollback failed: {e}', err=True)
        sys.exit(1)


if __name__ == '__main__':
    cli()
