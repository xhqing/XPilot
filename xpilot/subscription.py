"""Subscription parsing for xpilot."""

import base64
import json
import logging
import re

from . import __version__

logger = logging.getLogger(__name__)


class SubscriptionError(Exception):
    """Subscription related errors."""
    pass


def fetch(url: str) -> str:
    """Fetch subscription content from URL.

    强制直连、不走任何代理。订阅更新最常发生在「代理已坏、需要恢复」时，
    若 requests 走系统代理（xpilot start 会把 macOS 系统代理指向本地 xray），
    会陷入「代理坏 → 拉不到订阅 → 无法恢复代理」的死循环。用 trust_env=False
    的 session 忽略环境变量与 macOS 系统代理配置，确保订阅分发域名直连可达。
    """
    import requests
    session = requests.Session()
    session.trust_env = False  # 忽略 HTTP_PROXY/HTTPS_PROXY 及 macOS 系统代理
    try:
        resp = session.get(
            url, timeout=30,
            headers={'User-Agent': f'xpilot/{__version__}'},
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        raise SubscriptionError(f'Failed to fetch subscription: {e}')


def parse(content: str) -> list:
    """Parse subscription content into node list."""
    # Try Base64 first (most common)
    nodes = _parse_base64(content)
    if nodes:
        return nodes

    # Try JSON
    nodes = _parse_json(content)
    if nodes:
        return nodes

    # Try Clash format
    nodes = _parse_clash(content)
    if nodes:
        return nodes

    return []


def _parse_base64(content: str) -> list:
    """Parse Base64 encoded subscription."""
    try:
        # Remove whitespace and try decode
        cleaned = content.strip()
        # Add padding if needed
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += '=' * (4 - missing_padding)

        decoded = base64.b64decode(cleaned).decode('utf-8', errors='ignore')

        # Each line is a share link
        nodes = []
        for line in decoded.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            node = _parse_share_link(line)
            if node:
                nodes.append(node)
        return nodes
    except Exception as e:
        logger.debug(f'Base64 parsing failed: {e}')
        return []


def _parse_json(content: str) -> list:
    """Parse JSON format subscription."""
    try:
        data = json.loads(content)
        nodes = []
        if isinstance(data, list):
            for item in data:
                node = _convert_json_node(item)
                if node:
                    nodes.append(node)
        elif isinstance(data, dict):
            if 'servers' in data:
                for item in data['servers']:
                    node = _convert_json_node(item)
                    if node:
                        nodes.append(node)
        return nodes
    except json.JSONDecodeError:
        return []


def _parse_clash(content: str) -> list:
    """Parse Clash YAML format subscription."""
    try:
        import yaml
        data = yaml.safe_load(content)
        if not isinstance(data, dict) or 'proxies' not in data:
            return []

        nodes = []
        for item in data['proxies']:
            node = _convert_clash_node(item)
            if node:
                nodes.append(node)
        return nodes
    except Exception as e:
        logger.debug(f'Clash parsing failed: {e}')
        return []


def _parse_share_link(link: str) -> dict:
    """Parse a single share link."""
    try:
        if link.startswith('vmess://'):
            return _parse_vmess_link(link)
        elif link.startswith('vless://'):
            return _parse_vless_link(link)
        elif link.startswith('trojan://'):
            return _parse_trojan_link(link)
        elif link.startswith('ss://'):
            return _parse_ss_link(link)
    except Exception as e:
        logger.debug(f'Failed to parse share link: {e}')
    return None


def _parse_vmess_link(link: str) -> dict:
    """Parse VMess share link."""
    try:
        encoded = link[8:]  # Remove vmess://
        missing_padding = len(encoded) % 4
        if missing_padding:
            encoded += '=' * (4 - missing_padding)
        data = json.loads(base64.b64decode(encoded).decode('utf-8'))

        return {
            'name': data.get('ps', data.get('name', 'Unknown')),
            'protocol': 'vmess',
            'address': data.get('add', ''),
            'port': int(data.get('port', 0)),
            'uuid': data.get('id', ''),
            'alterId': int(data.get('aid', 0)),
            'security': data.get('scy', 'auto'),
            'network': data.get('net', 'tcp'),
            'tls': data.get('tls', '') == 'tls',
            'servername': data.get('sni', ''),
        }
    except Exception:
        return None


def _parse_vless_link(link: str) -> dict:
    """Parse VLESS share link（含 VLESS+Reality 参数）。"""
    try:
        # vless://uuid@host:port?params#name
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        security = params.get('security', ['none'])[0]

        return {
            # fragment 不被 parse_qs 处理，需手动 unquote 解码 %40 %3A 等。
            'name': unquote(parsed.fragment) or 'Unknown',
            'protocol': 'vless',
            'address': parsed.hostname,
            'port': parsed.port,
            'uuid': parsed.username,
            'security': security,
            'network': params.get('type', ['tcp'])[0],
            'tls': security not in ('none', ''),
            'servername': params.get('sni', [''])[0],
            # Reality 专用字段（仅 security=reality 时有意义）
            'reality_public_key': params.get('pbk', [None])[0],
            'reality_short_id': params.get('sid', [None])[0],
            'fingerprint': params.get('fp', [None])[0],
            'flow': params.get('flow', [None])[0],
        }
    except Exception:
        return None


def _parse_trojan_link(link: str) -> dict:
    """Parse Trojan share link."""
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(link)
        params = parse_qs(parsed.query)

        return {
            'name': parsed.fragment or 'Unknown',
            'protocol': 'trojan',
            'address': parsed.hostname,
            'port': parsed.port,
            'password': parsed.username,
            'tls': True,
            'servername': params.get('sni', [''])[0],
        }
    except Exception:
        return None


def _b64decode_loose(s: str) -> str:
    """容错的 base64 解码：兼容 websafe（用 - _ 替代 + /）并自动补齐 padding。"""
    s = s.replace('-', '+').replace('_', '/')
    pad = len(s) % 4
    if pad:
        s += '=' * (4 - pad)
    return base64.b64decode(s).decode('utf-8', errors='ignore')


def _parse_ss_link(link: str) -> dict:
    """Parse Shadowsocks share link.

    支持两种格式：
    - SIP002（机场最常用）：``ss://base64(method:password)@host:port#name``。
      userinfo 是 websafe base64，其中 + / = 等字符会让 urlparse 误把 userinfo
      当成 hostname/port，故改为手动按 ``@`` 与 ``:`` 切分。
    - 旧式整体编码：``ss://base64(method:password@host:port)#name``（无明文 @）。
    """
    from urllib.parse import unquote
    try:
        body = link[len('ss://'):]
        name = 'Unknown'
        if '#' in body:
            body, name_part = body.split('#', 1)
            name = unquote(name_part) or 'Unknown'
        body = body.split('?', 1)[0]  # 去掉 ?plugin=... 等查询串

        if '@' in body:
            # SIP002: base64(method:password)@host:port
            userinfo_b64, hostport = body.rsplit('@', 1)
            method_password = _b64decode_loose(userinfo_b64)
            host, port = hostport.rsplit(':', 1)
        else:
            # 旧式: base64(method:password@host:port)
            decoded = _b64decode_loose(body)
            userinfo, hostport = decoded.rsplit('@', 1)
            method_password = userinfo
            host, port = hostport.rsplit(':', 1)

        method, _, password = method_password.partition(':')
        return {
            'name': name,
            'protocol': 'ss',
            'address': host,
            'port': int(port),
            'password': password,
            'security': method,
        }
    except Exception as e:
        logger.debug(f'Failed to parse ss link: {e}')
        return None


def _convert_json_node(item: dict) -> dict:
    """Convert JSON subscription node to internal format."""
    protocol = item.get('type', item.get('protocol', '')).lower()
    protocol_map = {'vmess': 'vmess', 'vless': 'vless', 'trojan': 'trojan',
                    'shadowsocks': 'ss', 'ss': 'ss'}
    protocol = protocol_map.get(protocol, protocol)

    return {
        'name': item.get('name', 'Unknown'),
        'protocol': protocol,
        'address': item.get('server', item.get('address', '')),
        'port': item.get('port', 0),
        'uuid': item.get('uuid', ''),
        'password': item.get('password', ''),
        'alterId': item.get('alterId', 0),
        'security': item.get('cipher', item.get('security', 'auto')),
        'network': item.get('network', 'tcp'),
        'tls': item.get('tls', False),
        'servername': item.get('servername', item.get('sni', '')),
    }


def _convert_clash_node(item: dict) -> dict:
    """Convert Clash proxy node to internal format."""
    proxy_type = item.get('type', '').lower()
    protocol_map = {'vmess': 'vmess', 'vless': 'vless', 'trojan': 'trojan',
                    'shadowsocks': 'ss'}
    protocol = protocol_map.get(proxy_type, proxy_type)

    return {
        'name': item.get('name', 'Unknown'),
        'protocol': protocol,
        'address': item.get('server', ''),
        'port': item.get('port', 0),
        'uuid': item.get('uuid', ''),
        'password': item.get('password', ''),
        'alterId': item.get('alterId', 0),
        'security': item.get('cipher', item.get('security', 'auto')),
        'network': item.get('network', 'tcp'),
        'tls': item.get('tls', False),
        'servername': item.get('servername', item.get('sni', '')),
    }
