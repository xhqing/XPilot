"""Tests for HealthChecker module."""

import pytest

from xpilot.health_checker import HealthChecker


@pytest.fixture
def checker():
    return HealthChecker()


class TestHealthChecker:
    def test_check_latency_timeout(self, checker):
        """测试：对不可达主机进行延迟检测，超时后应返回 float('inf')。"""
        node = {'address': '192.0.2.1', 'port': 12345}
        latency = checker.check_latency(node, timeout=1)
        assert latency == float('inf')

    def test_check_connectivity_timeout(self, checker):
        """测试：对不可达 URL 进行连通性检测，超时后应返回 False。"""
        node = {'address': '192.0.2.1', 'port': 12345}
        result = checker.check_connectivity(node, url='http://192.0.2.1', timeout=1)
        assert result is False

    def test_sort_by_latency(self, checker):
        """测试：按延迟从小到大排序节点，连通失败的节点排在最后。"""
        results = [
            {'id': 'c', 'latency': 150, 'connected': True},
            {'id': 'a', 'latency': 50, 'connected': True},
            {'id': 'b', 'latency': -1, 'connected': False},
            {'id': 'd', 'latency': 100, 'connected': True},
        ]
        sorted_results = checker.sort_by_latency(results)
        assert sorted_results[0]['id'] == 'a'
        assert sorted_results[1]['id'] == 'd'
        assert sorted_results[2]['id'] == 'c'
        assert sorted_results[3]['id'] == 'b'

    def test_sort_by_latency_empty(self, checker):
        """测试：对空列表进行延迟排序，应返回空列表。"""
        assert checker.sort_by_latency([]) == []

    def test_check_real_traffic_without_proxy_manager(self, checker):
        """无 proxy_manager 时，真实流量检测应返回 ok=False 并带错误说明。"""
        result = checker.check_real_traffic({'address': '127.0.0.1', 'port': 12345})
        assert result['ok'] is False
        assert result['error']

    def test_find_free_port_returns_usable_port(self, checker):
        """_find_free_port 返回的端口应可被 bind（确实空闲）。"""
        import socket
        port = checker._find_free_port()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', port))
        finally:
            s.close()

    def test_curl_through_socks_unreachable(self, checker):
        """对无人监听的端口探测，应返回 (False, 0, None)。

        端口空闲（无 xray 监听）时 curl 立即连接被拒，三个 URL 都失败。
        """
        port = checker._find_free_port()
        ok, code, _lat = checker._curl_through_socks(port, timeout=3)
        assert ok is False
        assert code == 0

    def test_curl_download_parses_speed(self, checker, monkeypatch):
        """_curl_download 应把 curl 的 speed_download|time_total 解析成 Mbps。"""

        class FakeProc:
            stdout = '1250000|8.0'  # 1.25 MB/s = 10 Mbps，8 秒

        monkeypatch.setattr('xpilot.health_checker.subprocess.run',
                            lambda *a, **k: FakeProc())
        speed, elapsed = checker._curl_download(1080, 10_000_000, 30)
        assert speed == 10.0      # 1250000 bytes/s * 8 / 1e6 = 10 Mbps
        assert elapsed == 8.0

    def test_check_speed_without_proxy_manager(self, checker):
        """无 proxy_manager 时 check_speed 返回 speed_mbps=None + error。"""
        result = checker.check_speed({'address': '127.0.0.1', 'port': 12345})
        assert result['speed_mbps'] is None
        assert result['error']

    def test_check_direct_speed_handles_failure(self, checker, monkeypatch):
        """直连测速 curl 无有效输出 → speed_mbps=None + error。"""

        class FakeProc:
            stdout = ''  # curl 失败（连接被拒/超时）无 speed_download 输出

        monkeypatch.setattr('xpilot.health_checker.subprocess.run',
                            lambda *a, **k: FakeProc())
        result = checker.check_direct_speed(size_bytes=1000, timeout=5)
        assert result['speed_mbps'] is None
        assert result['error']

    def test_check_direct_latency_returns_ms(self, checker, monkeypatch):
        """check_direct_latency 应把 curl 的 http_code|time_total 解析成毫秒。"""

        class FakeProc:
            stdout = '204|0.350'  # 204 OK，350ms

        monkeypatch.setattr('xpilot.health_checker.subprocess.run',
                            lambda *a, **k: FakeProc())
        assert checker.check_direct_latency(timeout=5) == 350

    def test_check_direct_latency_failure_returns_none(self, checker, monkeypatch):
        """直连延迟 curl 无有效输出（端点不可达）→ 返回 None。"""

        class FakeProc:
            stdout = ''

        monkeypatch.setattr('xpilot.health_checker.subprocess.run',
                            lambda *a, **k: FakeProc())
        assert checker.check_direct_latency(timeout=5) is None
