"""Tests for ProxyManager outbound 生成 —— VLESS+Reality 与普通 TLS。"""

import tempfile

from xpilot.config import Config
from xpilot.node_manager import NodeManager
from xpilot.routing_manager import RoutingManager
from xpilot.proxy_manager import ProxyManager


def _make_pm():
    tmp = tempfile.mkdtemp()
    cfg = Config(tmp)
    cfg.init_default_configs()
    return ProxyManager(cfg, NodeManager(cfg), RoutingManager(cfg))


class TestProxyManager:
    def test_reality_outbound(self):
        """reality 节点应生成 realitySettings，flow 写到 vless user。"""
        pm = _make_pm()
        node = {
            'protocol': 'vless', 'address': '1.2.3.4', 'port': 443,
            'uuid': 'aabbccdd-1234-5678-90ab-cdef01234567',
            'security': 'reality', 'network': 'tcp',
            'servername': 'portal.example.com', 'fingerprint': 'chrome',
            'reality_public_key': 'PubKeyABC', 'reality_short_id': 'deadbeef',
            'flow': 'xtls-rprx-vision',
        }
        ob = pm._generate_outbound(node)
        assert ob['protocol'] == 'vless'
        stream = ob['streamSettings']
        assert stream['security'] == 'reality'
        rs = stream['realitySettings']
        assert rs['publicKey'] == 'PubKeyABC'
        assert rs['shortId'] == 'deadbeef'
        assert rs['serverName'] == 'portal.example.com'
        assert rs['fingerprint'] == 'chrome'
        user = ob['settings']['vnext'][0]['users'][0]
        assert user['flow'] == 'xtls-rprx-vision'
        assert user['encryption'] == 'none'

    def test_tls_outbound_unaffected_by_reality_change(self):
        """普通 TLS 节点仍生成 tlsSettings（reality 改动不影响旧逻辑）。"""
        pm = _make_pm()
        node = {
            'protocol': 'vless', 'address': '1.2.3.4', 'port': 443,
            'uuid': 'aabbccdd-1234-5678-90ab-cdef01234567',
            'security': 'tls', 'tls': True, 'servername': 'a.example.com',
        }
        ob = pm._generate_outbound(node)
        assert ob['streamSettings']['security'] == 'tls'
        assert 'tlsSettings' in ob['streamSettings']
        assert 'realitySettings' not in ob['streamSettings']
