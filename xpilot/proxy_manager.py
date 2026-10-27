"""Proxy service management (xray process control) for xpilot."""

import os
import json
import re
import signal
import subprocess
import logging
import time
import platform

from .legacy_cleanup import cleanup_legacy_state

logger = logging.getLogger(__name__)


def _legacy_cleanup_log(level: str, message: str) -> None:
    """把遗留文件清理的日志转发到本模块 logger（统一日志出口）。"""
    if level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.info(message)


class ProxyError(Exception):
    """Proxy related errors."""
    pass


class ProxyStartError(ProxyError):
    """Failed to start proxy."""
    pass


class ProxyStopError(ProxyError):
    """Failed to stop proxy."""
    pass


class ConfigError(ProxyError):
    """Configuration generation error."""
    pass


class ProxyManager:
    """Manage xray proxy service."""

    # 旧版本的 PID 文件位置（只读识别：升级前拉起的实例可能还把 PID 留在这里。
    # 新版本不再写入该路径，仅由 probe_runtime 以端口探测接管）。
    LEGACY_PID_FILE = '/tmp/xpilot.pid'

    def __init__(self, config, node_manager, routing_manager=None):
        from .utils import get_runtime_dir

        self.config = config
        self.node_manager = node_manager
        self.routing_manager = routing_manager
        self._process = None

        # 运行状态文件（PID / 运行配置 / 日志）不进 /tmp：临时目录被系统清理后
        # 会出现「xray 还在跑、工具显示 Stopped」的状态错位与静默断网（Issue #1）。
        self.runtime_dir = get_runtime_dir(getattr(config, 'config_dir', None))
        self.pid_file = os.path.join(self.runtime_dir, 'xpilot.pid')
        self.runtime_config_path = os.path.join(self.runtime_dir, 'xray-config.json')
        self.stdout_log_path = os.path.join(self.runtime_dir, 'xray-stdout.log')
        self.stderr_log_path = os.path.join(self.runtime_dir, 'xray-stderr.log')

    def cleanup_legacy_state(self) -> list:
        """清理 0.6.3 及更早版本遗留在临时目录的状态文件（Issue #5）。

        旧版本的 PID / 运行配置 / 日志写在 ``/tmp``，新版本一律读
        ``<配置目录>/run``，旧文件是惰性的但会误导事故排查。只删白名单
        里的名字，PID 文件先确认其描述的进程已死（删活进程的 PID 文件会
        重新制造状态错位）。返回实际删除的路径列表。
        """
        return cleanup_legacy_state(log=_legacy_cleanup_log)

    def start(self, node_id: str = None) -> bool:
        """Start proxy service with specified node."""
        # 探测运行状态之前先清掉旧版本遗留文件：让后续判定只看当前版本的
        # 状态文件，也避免旧的 /tmp/xpilot.pid 被误认为活实例。
        self.cleanup_legacy_state()
        state = self.probe_runtime()
        if state['running']:
            # 孤儿 / 多实例处置：宁可拒绝启动，也不让两个 xray 同时抢同一个端口
            # （SO_REUSEPORT 下两个实例能同时 bind 成功，连接随机落到不同实例）。
            if len(state['pids']) > 1:
                pid_text = ', '.join(str(p) for p in state['pids'])
                raise ProxyStartError(
                    f'检测到多个 xray 实例同时监听本工具端口（PIDs: {pid_text}），'
                    '为避免端口争用不再启动新实例；请先执行 xpilot stop 清理后重新 start'
                )
            current = node_id or self.node_manager.get_default_node()
            node_name = current
            if current:
                try:
                    node_name = self.node_manager.get_node(current).get('name', current)
                except Exception:
                    node_name = current
            logger.warning(
                f'Proxy is already running (PID: {state["pid"]}, detected via {state["source"]})')
            return node_name or True

        if node_id is None:
            node_id = self.node_manager.get_default_node()
            if not node_id:
                raise ProxyStartError('No node specified and no default node set')

        node = self.node_manager.get_node(node_id)
        settings = self.config.load_config('settings.json')

        # Check port availability
        from .utils import is_port_available
        socks_port = settings.get('socks_port', 1080)
        http_port = settings.get('http_port', 1087)
        if not is_port_available(socks_port):
            raise ProxyStartError(f'Port {socks_port} is already in use')
        if not is_port_available(http_port):
            logger.warning(f'HTTP port {http_port} may be in use')

        # All protocols (including Shadowsocks) are handled by xray
        self._start_xray(node, settings, socks_port, http_port)

        # Set system proxy
        if settings.get('system_proxy', {}).get('enabled', True):
            self.set_system_proxy(True)

        # Update default node
        self.node_manager.set_default_node(node_id)
        node_name = node.get('name', node_id)
        logger.info(f'Proxy started with node: {node_name} (PID: {self._process.pid})')
        return node_name

    def _start_xray(self, node: dict, settings: dict, socks_port: int, http_port: int):
        """Start xray for non-SS protocols."""
        xray_config = self.generate_xray_config(node)

        # 运行配置连同 PID 一起落在 runtime 目录（不再随 /tmp 被清理）：
        # 它既是 xray 的启动参数，也是「运行中的实例用的是哪份配置」的诊断依据。
        xray_config_path = self.runtime_config_path
        with open(xray_config_path, 'w') as f:
            json.dump(xray_config, f, indent=2)

        xray_bin = settings.get('xray_bin', '/usr/local/bin/xray')
        if not os.path.exists(xray_bin):
            raise ProxyStartError(f'Xray binary not found: {xray_bin}')

        stdout_file = self.stdout_log_path
        stderr_file = self.stderr_log_path

        with open(stdout_file, 'w') as stdout_f, open(stderr_file, 'w') as stderr_f:
            self._process = subprocess.Popen(
                [xray_bin, 'run', '-config', xray_config_path],
                stdout=stdout_f,
                stderr=stderr_f,
                start_new_session=True,
                close_fds=False
            )

        # Save PID
        self._write_pid_file(self._process.pid)

        # Quick check that the daemon is still running
        time.sleep(1)
        if self._process.poll() is not None:
            time.sleep(0.5)
            error_output = ''
            try:
                with open(stderr_file, 'r') as sf:
                    error_output = sf.read().strip()
            except Exception:
                pass
            if not error_output:
                try:
                    with open(stdout_file, 'r') as sf:
                        error_output = sf.read().strip()
                except Exception:
                    pass
            if not error_output:
                error_output = 'Xray daemon exited immediately after start'
            raise ProxyStartError(f'Xray failed to start: {error_output}\nConfig: {xray_config_path}')

    def stop(self, force: bool = True) -> bool:
        """Stop proxy service.
        
        Args:
            force: If True, also kill any xray process using the ports
        """
        state = self.probe_runtime()
        pids = list(state['pids'])
        if state['pid'] and state['pid'] not in pids:
            pids.append(state['pid'])
        if state['running'] or pids:
            for pid in pids:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except OSError:
                    pass
            time.sleep(1)
            # Force kill if still running
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            self._remove_pid_file()
            self._process = None

        # Also kill any xray process that might be holding our ports
        if force:
            self._kill_xray_on_ports()

        # Disable system proxy
        try:
            self.set_system_proxy(False)
        except Exception as e:
            logger.warning(f'Failed to disable system proxy: {e}')

        # 收尾时顺手清理旧版本遗留文件（Issue #5）：stop 完还有人翻 /tmp 的
        # 时候，看到的应该是干净状态。
        self.cleanup_legacy_state()

        logger.info('Proxy stopped')
        return True

    def _kill_xray_on_ports(self):
        """Kill any xray process using our ports."""
        settings = self.config.load_config('settings.json')
        socks_port = settings.get('socks_port', 1080)
        http_port = settings.get('http_port', 1087)
        
        # Use lsof to find processes using our ports
        for port in [socks_port, http_port]:
            try:
                result = subprocess.run(
                    ['lsof', '-ti', f':{port}'],
                    capture_output=True, text=True, timeout=3
                )
                if result.stdout.strip():
                    for pid in result.stdout.strip().split('\n'):
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                        except (OSError, ValueError):
                            pass
            except Exception:
                pass

    def restart(self) -> bool:
        """Restart proxy service."""
        node_id = self.node_manager.get_default_node()
        self.stop()
        if node_id:
            return self.start(node_id)
        return True

    def get_status(self) -> dict:
        """Get proxy service status."""
        state = self.probe_runtime()
        running = state['running']
        status = {
            'running': running,
            'pid': state['pid'],
            'current_node': self.node_manager.get_default_node(),
            'source': state['source'],
            'warnings': list(state['warnings']),
            'runtime_dir': self.runtime_dir,
            'config_stale': None,
            'config_stale_detail': None,
        }

        if running:
            settings = self.config.load_config('settings.json')
            status['socks_port'] = settings.get('socks_port', 1080)
            status['http_port'] = settings.get('http_port', 1087)
            try:
                node = self.node_manager.get_node(status['current_node'])
                status['node_name'] = node.get('name', '')
            except Exception:
                status['node_name'] = ''

            # 运行实例是否还在用「当前默认节点」的配置：订阅刷新 / 节点变更后，
            # 内存里的旧配置不会自动生效，必须显式提示 restart（Issue #1 建议 4）。
            matched, detail = self.running_config_matches_current()
            status['config_stale'] = matched
            status['config_stale_detail'] = detail

        return status

    def running_config_matches_current(self):
        """运行中实例用的配置与当前默认节点是否一致。

        返回 ``(matched, detail)``：``True`` = 一致，``False`` = 不一致
        （需要 restart 才生效），``None`` = 无法判定（运行配置缺失 / 无默认节点）。
        """
        if not os.path.exists(self.runtime_config_path):
            return None, '运行时配置缺失，无法确认运行实例所用的节点'
        try:
            with open(self.runtime_config_path, 'r') as f:
                runtime_config = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return None, f'运行时配置无法解析: {e}'

        endpoint = self._config_proxy_endpoint(runtime_config)
        if not endpoint:
            return None, '运行时配置里没有可识别的代理出站节点'

        node_id = self.node_manager.get_default_node()
        if not node_id:
            return None, '未设置默认节点，无法比对运行实例所用配置'
        try:
            node = self.node_manager.get_node(node_id)
        except Exception:
            return None, f'默认节点 {node_id} 不存在，无法比对运行实例所用配置'

        running_endpoint = f'{endpoint[0]}:{endpoint[1]}'
        current_endpoint = f'{node.get("address")}:{node.get("port")}'
        if running_endpoint == current_endpoint:
            return True, f'运行实例使用 {running_endpoint}，与当前默认节点一致'
        return False, (f'运行实例仍使用 {running_endpoint}，当前默认节点为 '
                       f'{current_endpoint}，需执行 xpilot restart 才能生效')

    @staticmethod
    def _config_proxy_endpoint(config: dict):
        """从 xray 配置里取首个代理出站的 ``(address, port)``。"""
        for outbound in config.get('outbounds') or []:
            if not isinstance(outbound, dict):
                continue
            if outbound.get('protocol') in ('freedom', 'blackhole'):
                continue
            settings = outbound.get('settings') or {}
            for entry in (settings.get('vnext') or []) + (settings.get('servers') or []):
                if isinstance(entry, dict) and entry.get('address'):
                    return entry['address'], entry.get('port')
        return None

    def generate_xray_config(self, node: dict) -> dict:
        """Generate xray configuration JSON."""
        settings = self.config.load_config('settings.json')
        socks_port = settings.get('socks_port', 1080)
        http_port = settings.get('http_port', 1087)

        inbound = {
            'port': socks_port,
            'protocol': 'socks',
            'settings': {
                'auth': 'noauth',
                'udp': True,
            },
            'sniffing': {
                'enabled': True,
                'destOverride': ['http', 'tls'],
            }
        }

        # HTTP inbound
        http_inbound = {
            'port': http_port,
            'protocol': 'http',
            'settings': {}
        }

        # Default outbound
        outbounds = [self._generate_outbound(node)]

        # Routing
        routing = {
            'domainStrategy': 'IPIfNonMatch',
            'rules': []
        }

        if self.routing_manager:
            routing_result = self.routing_manager.generate_xray_routing_rules()
            routing['rules'] = routing_result['rules']

            # Add extra outbounds for domain-to-node mappings
            domain_outbounds = routing_result.get('domain_outbounds', [])
            for domains, node_id, outbound_tag in domain_outbounds:
                try:
                    domain_node = self.node_manager.get_node(node_id)
                    extra_outbound = self._generate_outbound(domain_node, tag=outbound_tag)
                    outbounds.append(extra_outbound)
                except Exception as e:
                    logger.warning(f'Failed to add domain outbound for node {node_id}: {e}')

        # Add direct and block outbound rules
        outbounds.append({
            'tag': 'direct',
            'protocol': 'freedom',
            'settings': {}
        })
        outbounds.append({
            'tag': 'block',
            'protocol': 'blackhole',
            'settings': {}
        })

        config = {
            'log': {
                'loglevel': settings.get('log_level', 'warning'),
            },
            'inbounds': [inbound, http_inbound],
            'outbounds': outbounds,
            'routing': routing,
        }

        return config

    def _generate_outbound(self, node: dict, tag: str = 'proxy') -> dict:
        """Generate xray outbound configuration from node."""
        protocol = node['protocol']

        if protocol == 'vmess':
            settings = {
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
            user = {'id': node.get('uuid', ''), 'encryption': 'none'}
            # flow（如 xtls-rprx-vision）用于 VLESS+Reality / XTLS，属 user 级。
            if node.get('flow'):
                user['flow'] = node['flow']
            settings = {
                'vnext': [{
                    'address': node['address'],
                    'port': node['port'],
                    'users': [user]
                }]
            }
        elif protocol == 'trojan':
            settings = {
                'servers': [{
                    'address': node['address'],
                    'port': node['port'],
                    'password': node.get('password', ''),
                }]
            }
        elif protocol in ('ss', 'shadowsocks'):
            settings = {
                'servers': [{
                    'address': node['address'],
                    'port': node['port'],
                    'password': node.get('password', ''),
                    'method': node.get('security', 'chacha20-ietf-poly1305'),
                }]
            }
        else:
            raise ConfigError(f'Unsupported protocol: {protocol}')

        # Map 'ss' to 'shadowsocks' for xray config
        xray_protocol = 'shadowsocks' if protocol in ('ss', 'shadowsocks') else protocol

        outbound = {
            'protocol': xray_protocol,
            'settings': settings,
            'tag': tag,
        }

        # Stream settings：Reality 优先于普通 TLS。
        security = node.get('security', 'none')
        if security == 'reality':
            outbound['streamSettings'] = {
                'network': node.get('network', 'tcp'),
                'security': 'reality',
                'realitySettings': {
                    'serverName': node.get('servername', node['address']),
                    'fingerprint': node.get('fingerprint') or 'chrome',
                    'publicKey': node.get('reality_public_key', ''),
                    'shortId': node.get('reality_short_id', ''),
                    'spiderX': '',
                    'show': False,
                }
            }
        elif node.get('tls', False) or protocol == 'trojan':
            outbound['streamSettings'] = {
                'network': node.get('network', 'tcp'),
                'security': 'tls',
                'tlsSettings': {
                    'serverName': node.get('servername', node['address']),
                    'allowInsecure': False,
                }
            }

        return outbound

    def set_system_proxy(self, enable: bool) -> None:
        """Set macOS system proxy."""
        from .utils import set_macos_proxy
        settings = self.config.load_config('settings.json')
        socks_port = settings.get('socks_port', 1080)
        http_port = settings.get('http_port', 1087)
        bypass_local = settings.get('system_proxy', {}).get('bypass_local', True)
        set_macos_proxy(enable, socks_port, http_port, bypass_local)

    def is_running(self) -> bool:
        """Check if proxy is running.

        不能只凭 PID 文件判定：/tmp 被清理后 PID 文件消失、而 xray 仍在跑，
        只看 PID 文件会把「运行中」误判成「已停止」（Issue #1 的真实事故）。
        判定交给 :meth:`probe_runtime`：PID 文件失效时用端口监听 + 进程名交叉校验。
        """
        return self.probe_runtime()['running']

    def probe_runtime(self) -> dict:
        """探测 xray 实例的真实运行状态（PID 文件 + 端口监听交叉校验）。

        返回 ``{'running', 'pid', 'pids', 'source', 'warnings'}``：

        - ``source='pidfile'``：PID 文件有效且进程确为 xray；
        - ``source='port'``：PID 文件缺失 / 失效，但本工具端口上有 xray 在监听
          （同时把 PID 回写到 runtime 目录，自愈）；
        - ``source=None``：两条证据都不成立，确实没在跑。
        """
        ports = self._configured_ports()
        warnings = []

        pid = self._read_pid_file()
        if pid is not None:
            if self._pid_alive(pid) and self._process_is_xray(pid):
                listeners = self._xray_pids_listening(ports)
                pids = [pid] + [p for p in listeners if p != pid]
                if len(pids) > 1:
                    warnings.append(self._multiple_instances_warning(pids))
                return {'running': True, 'pid': pid, 'pids': pids,
                        'source': 'pidfile', 'warnings': warnings}
            # PID 文件失效（进程已死 / PID 被别的进程复用）：清掉，继续用端口探测兜底。
            self._remove_pid_file()

        listeners = self._xray_pids_listening(ports)
        if listeners:
            primary = listeners[0]
            self._write_pid_file(primary)
            if len(listeners) > 1:
                warnings.append(self._multiple_instances_warning(listeners))
            logger.info(
                f'PID 文件缺失或失效，已按端口监听恢复运行状态（PID: {primary}）')
            return {'running': True, 'pid': primary, 'pids': list(listeners),
                    'source': 'port', 'warnings': warnings}

        return {'running': False, 'pid': None, 'pids': [],
                'source': None, 'warnings': warnings}

    @staticmethod
    def _multiple_instances_warning(pids) -> str:
        pid_text = ', '.join(str(p) for p in pids)
        return (f'检测到多个 xray 实例同时监听本工具端口（PIDs: {pid_text}），'
                '请执行 xpilot stop 清理后重新 start')

    def _configured_ports(self) -> list:
        """本工具占用的端口（socks / http），用于运行状态交叉校验。"""
        try:
            settings = self.config.load_config('settings.json')
        except Exception:
            settings = {}
        return [settings.get('socks_port', 1080), settings.get('http_port', 1087)]

    def _xray_pids_listening(self, ports) -> list:
        """返回在给定端口上监听、且进程名确为 xray 的 PID 列表。"""
        found = []
        for port in ports:
            for pid in self._listener_pids(port):
                if pid not in found and self._process_is_xray(pid):
                    found.append(pid)
        return found

    @staticmethod
    def _listener_pids(port) -> list:
        """端口上的监听进程 PID（lsof 优先，Linux 退化到 ss）。"""
        pids = []
        try:
            result = subprocess.run(
                ['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                capture_output=True, text=True, timeout=3)
            pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['ss', '-ltnpH', f'sport = :{port}'],
                    capture_output=True, text=True, timeout=3)
                pids = [int(m) for m in re.findall(r'pid=(\d+)', result.stdout or '')]
            except Exception:
                pids = []
        except Exception:
            pids = []
        return pids

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _process_is_xray(pid: int) -> bool:
        """进程名是否为 xray（防 PID 复用把别的进程当成本工具拉起的 xray）。"""
        comm = ''
        try:
            result = subprocess.run(
                ['ps', '-p', str(pid), '-o', 'comm='],
                capture_output=True, text=True, timeout=3)
            comm = (result.stdout or '').strip()
        except Exception:
            comm = ''
        if not comm:
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    comm = f.read().decode(errors='ignore').replace('\0', ' ').strip()
            except OSError:
                return False
        if not comm:
            return False
        first = comm.split()[0]
        return 'xray' in os.path.basename(first).lower()

    def _read_pid_file(self):
        try:
            with open(self.pid_file, 'r') as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _write_pid_file(self, pid: int) -> None:
        try:
            os.makedirs(self.runtime_dir, exist_ok=True)
            tmp_path = f'{self.pid_file}.tmp'
            with open(tmp_path, 'w') as f:
                f.write(str(pid))
            os.replace(tmp_path, self.pid_file)
        except OSError as e:
            logger.warning(f'写 PID 文件失败: {e}')

    def _remove_pid_file(self) -> None:
        try:
            if os.path.exists(self.pid_file):
                os.remove(self.pid_file)
        except OSError:
            pass

    def _get_pid(self) -> int:
        """Get proxy process PID."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        return self.probe_runtime()['pid']
