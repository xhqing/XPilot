"""Proxy service management (xray process control) for xpilot."""

import os
import json
import signal
import subprocess
import logging
import time
import platform

logger = logging.getLogger(__name__)


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

    PID_FILE = '/tmp/xpilot.pid'

    def __init__(self, config, node_manager, routing_manager=None):
        self.config = config
        self.node_manager = node_manager
        self.routing_manager = routing_manager
        self._process = None

    def start(self, node_id: str = None) -> bool:
        """Start proxy service with specified node."""
        if self.is_running():
            logger.warning('Proxy is already running')
            return True

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

        # Write xray config to temp file
        xray_config_path = '/tmp/xpilot-xray.json'
        with open(xray_config_path, 'w') as f:
            json.dump(xray_config, f, indent=2)

        xray_bin = settings.get('xray_bin', '/usr/local/bin/xray')
        if not os.path.exists(xray_bin):
            raise ProxyStartError(f'Xray binary not found: {xray_bin}')

        # Write stdout and stderr to temp files for error capture
        stdout_file = '/tmp/xpilot-xray-stdout.log'
        stderr_file = '/tmp/xpilot-xray-stderr.log'

        with open(stdout_file, 'w') as stdout_f, open(stderr_file, 'w') as stderr_f:
            self._process = subprocess.Popen(
                [xray_bin, 'run', '-config', xray_config_path],
                stdout=stdout_f,
                stderr=stderr_f,
                start_new_session=True,
                close_fds=False
            )

        # Save PID
        with open(self.PID_FILE, 'w') as f:
            f.write(str(self._process.pid))

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
        if self.is_running():
            pid = self._get_pid()
            if pid:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    time.sleep(1)
                    # Force kill if still running
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                except OSError as e:
                    logger.error(f'Failed to stop xray: {e}')
                    raise ProxyStopError(f'Failed to stop proxy: {e}')

            # Clear PID file
            if os.path.exists(self.PID_FILE):
                os.remove(self.PID_FILE)
            self._process = None

        # Also kill any xray process that might be holding our ports
        if force:
            self._kill_xray_on_ports()

        # Disable system proxy
        try:
            self.set_system_proxy(False)
        except Exception as e:
            logger.warning(f'Failed to disable system proxy: {e}')

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
        running = self.is_running()
        status = {
            'running': running,
            'pid': self._get_pid() if running else None,
            'current_node': self.node_manager.get_default_node(),
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

        return status

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
        """Check if proxy is running."""
        pid = self._get_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            # Clean up stale PID file
            if os.path.exists(self.PID_FILE):
                os.remove(self.PID_FILE)
            return False

    def _get_pid(self) -> int:
        """Get proxy process PID."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        if os.path.exists(self.PID_FILE):
            try:
                with open(self.PID_FILE, 'r') as f:
                    return int(f.read().strip())
            except (ValueError, IOError):
                pass
        return None
