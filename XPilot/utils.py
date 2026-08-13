"""Utility functions for xpilot."""

import os
import sys
import socket
import subprocess
import platform
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_config_dir() -> str:
    """Get the default config directory path.

    Follows XDG conventions: ``$XDG_CONFIG_HOME/xpilot`` when set, falling
    back to ``~/.config/xpilot`` on macOS/Linux and
    ``%APPDATA%/xpilot`` on Windows. Keeping user configuration out of the
    source tree means it survives ``pip install -e .`` and project upgrades,
    and matches what the README documents. The directory is still overridable
    via the ``PROXY_TOOLKIT_CONFIG_DIR`` environment variable handled in
    :class:`xpilot.config.Config`.
    """
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg:
        base = xdg
    elif sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~/.config')
    else:
        base = os.path.expanduser('~/.config')
    config_dir = os.path.join(base, 'xpilot')
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def sanitize_input(value: str, max_length: int = 256) -> str:
    """Sanitize user input string."""
    if not isinstance(value, str):
        raise ValueError('Input must be a string')
    value = value.strip()[:max_length]
    dangerous_chars = [';', '&', '|', '`', '$', '(', ')', '{', '}', '<', '>', '\n', '\r']
    for char in dangerous_chars:
        if char in value:
            raise ValueError(f'Invalid character in input: {char}')
    return value


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """Mask sensitive information in logs."""
    if len(value) <= visible_chars:
        return '*' * len(value)
    return '*' * (len(value) - visible_chars) + value[-visible_chars:]


def is_port_available(port: int) -> bool:
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def ping_host(host: str, port: int, timeout: int = 5) -> float:
    """Ping a host and return latency in milliseconds."""
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.time() - start) * 1000
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.debug(f'Ping failed for {host}:{port}: {e}')
        return float('inf')


def check_connectivity(url: str, proxy: str = None, timeout: int = 10) -> bool:
    """Check connectivity to a URL."""
    try:
        import requests
        proxies = {'http': proxy, 'https': proxy} if proxy else None
        resp = requests.get(url, proxies=proxies, timeout=timeout)
        return resp.status_code < 400
    except Exception as e:
        logger.debug(f'Connectivity check failed for {url}: {e}')
        return False


def set_macos_proxy(enable: bool, socks_port: int = 1080, http_port: int = 1087,
                    bypass_local: bool = True) -> None:
    """Set macOS system proxy for all enabled network services."""
    network_services = _get_all_network_services()
    if not network_services:
        logger.warning('Could not detect any network services')
        return

    for network_service in network_services:
        if enable:
            bypass_domains = _get_bypass_domains(bypass_local)
            subprocess.run(['networksetup', '-setsocksfirewallproxy', network_service,
                            '127.0.0.1', str(socks_port)], check=False)
            subprocess.run(['networksetup', '-setwebproxy', network_service,
                            '127.0.0.1', str(http_port)], check=False)
            subprocess.run(['networksetup', '-setsecurewebproxy', network_service,
                            '127.0.0.1', str(http_port)], check=False)
            if bypass_domains:
                subprocess.run(['networksetup', '-setproxybypassdomains', network_service]
                               + bypass_domains, check=False)
        else:
            subprocess.run(['networksetup', '-setsocksfirewallproxystate', network_service, 'off'],
                           check=False)
            subprocess.run(['networksetup', '-setwebproxystate', network_service, 'off'],
                           check=False)
            subprocess.run(['networksetup', '-setsecurewebproxystate', network_service, 'off'],
                           check=False)


def _get_all_network_services() -> list:
    """Get all enabled network service names."""
    result = subprocess.run(['networksetup', '-listallnetworkservices'],
                            capture_output=True, text=True, check=False)
    lines = result.stdout.strip().split('\n')
    services = []
    for line in lines[1:]:
        line = line.strip()
        if line and '*' not in line:
            services.append(line)
    return services


def _get_bypass_domains(bypass_local: bool) -> list:
    """Get proxy bypass domains."""
    domains = ['*.local', '169.254/16']
    if bypass_local:
        domains.extend(['*.cn', 'localhost', '127.0.0.1', '192.168.0.0/16',
                        '10.0.0.0/8', '172.16.0.0/12'])
    return domains


def generate_node_id(name: str, existing_ids: set = None) -> str:
    """Generate a unique node ID from name.

    Non-ASCII characters (e.g. Chinese) are turned into a single separator,
    consecutive separators are collapsed, and leading/trailing ones stripped.
    When the name contains no ASCII alphanumerics at all (so the cleaned
    result would be empty), a short deterministic hash of the original name is
    used instead — e.g. ``日本节点`` becomes ``node_3f2a1b`` rather than an
    unreadable ``____``. The hash is derived from the name, so re-adding the
    same name yields a stable, predictable ID.
    """
    import re
    import hashlib
    base_id = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower())
    base_id = re.sub(r'_+', '_', base_id).strip('_')
    if not base_id:
        digest = hashlib.md5(name.encode('utf-8')).hexdigest()[:6]
        base_id = f'node_{digest}'
    if existing_ids is None:
        return base_id
    node_id = base_id
    counter = 1
    while node_id in existing_ids:
        node_id = f'{base_id}_{counter}'
        counter += 1
    return node_id


def format_timestamp(ts: str = None) -> str:
    """Format current time or given timestamp as ISO format."""
    if ts:
        return ts
    return datetime.now().isoformat()


def validate_protocol(protocol: str) -> bool:
    """Validate protocol type."""
    valid_protocols = {'vmess', 'vless', 'trojan', 'ss', 'shadowsocks'}
    return protocol.lower() in valid_protocols


def validate_port(port: int) -> bool:
    """Validate port number."""
    return 1 <= port <= 65535
