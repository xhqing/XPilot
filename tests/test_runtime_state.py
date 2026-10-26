"""状态交叉校验与孤儿实例处置（Issue #1 / T21 契约 3、4、5、6、7）。

真实事故：/tmp 被系统清理后 PID 文件与运行配置一起消失，xray 进程仍在跑、
端口仍在监听，但状态判定只看 PID 文件 → 显示 Stopped，监控与自动切换不干预。

本文件不真起 xray：进程存活判定、xray 进程识别、端口监听探测三个系统接缝
（_process_is_xray / _xray_pids_listening）一律 monkeypatch 注入假实现；
start() 路径下 subprocess.Popen 被调用即视为失败。
"""

import json
import os

import pytest

from xpilot.config import Config
from xpilot.node_manager import NodeManager
from xpilot.proxy_manager import ProxyManager, ProxyStartError
from xpilot.routing_manager import RoutingManager

ALIVE_PID = os.getpid()
# macOS 的 PID 上限是 5 位数，6 位数不可能存在，用作确定已死的 PID。
DEAD_PID = 999999
ALPHA_ADDR, ALPHA_PORT = '203.0.113.7', 443
BETA_ADDR, BETA_PORT = '198.51.100.9', 8443
PROBE_KEYS = {'running', 'pid', 'pids', 'source', 'warnings'}


@pytest.fixture
def pm(tmp_path):
    """带两个节点（默认 alpha）的 ProxyManager，配置目录在临时目录。"""
    config = Config(str(tmp_path))
    config.init_default_configs()
    nodes = NodeManager(config)
    nodes.add_node({
        'name': 'alpha', 'protocol': 'vless', 'address': ALPHA_ADDR,
        'port': ALPHA_PORT, 'uuid': 'aabbccdd-1234-5678-90ab-cdef01234567',
    })
    nodes.add_node({
        'name': 'beta', 'protocol': 'vless', 'address': BETA_ADDR,
        'port': BETA_PORT, 'uuid': 'aabbccdd-1234-5678-90ab-cdef01234568',
    })
    nodes.set_default_node('alpha')
    return ProxyManager(config, nodes, RoutingManager(config))


def _patch_probe(pm, monkeypatch, *, is_xray=True, listeners=None, seen_ports=None):
    """注入两个系统接缝：_process_is_xray(pid) 与 _xray_pids_listening(ports)。"""

    def fake_is_xray(pid):
        return is_xray

    def fake_listeners(ports):
        if seen_ports is not None:
            seen_ports.append(list(ports))
        return list(listeners or [])

    monkeypatch.setattr(pm, '_process_is_xray', fake_is_xray, raising=False)
    monkeypatch.setattr(pm, '_xray_pids_listening', fake_listeners, raising=False)


def _write_pidfile(pm, pid):
    path = pm.pid_file
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(str(pid))
    return path


def _write_runtime_config(pm, outbounds):
    path = pm.runtime_config_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump({'outbounds': outbounds}, f, indent=2)
    return path


def _vless_outbound(address, port, tag='proxy'):
    return {
        'protocol': 'vless',
        'tag': tag,
        'settings': {'vnext': [{'address': address, 'port': port, 'users': []}]},
    }


def _forbid_spawn(pm, monkeypatch):
    """已运行场景下任何拉起 xray / 改系统代理的动作都视为失败。"""

    def explode(*args, **kwargs):
        raise AssertionError('已运行场景不得再次拉起 xray 或改系统代理')

    monkeypatch.setattr('xpilot.proxy_manager.subprocess.Popen', explode)
    monkeypatch.setattr('xpilot.utils.is_port_available', lambda port: True)
    monkeypatch.setattr(pm, 'set_system_proxy', explode)


class TestProbeRuntime:
    """契约 3：probe_runtime() 的判定表与返回值形状。"""

    def test_live_pidfile_reported_as_running(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, is_xray=True, listeners=[])
        _write_pidfile(pm, ALIVE_PID)

        probe = pm.probe_runtime()

        assert PROBE_KEYS <= set(probe)
        assert probe['running'] is True
        assert probe['pid'] == ALIVE_PID
        assert probe['source'] == 'pidfile'
        assert probe['warnings'] == []
        assert isinstance(probe['pids'], list)

    def test_dead_pidfile_is_discarded(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[])
        pid_path = _write_pidfile(pm, DEAD_PID)

        probe = pm.probe_runtime()

        assert probe['running'] is False
        assert probe['pid'] is None
        assert probe['source'] is None
        assert not os.path.exists(pid_path)

    def test_reused_pid_pidfile_is_discarded(self, pm, monkeypatch):
        """PID 复用（进程活着但不是 xray）同样按失效处理并删除 PID 文件。"""
        _patch_probe(pm, monkeypatch, is_xray=False, listeners=[])
        pid_path = _write_pidfile(pm, ALIVE_PID)

        probe = pm.probe_runtime()

        assert probe['running'] is False
        assert probe['pid'] is None
        assert probe['source'] is None
        assert not os.path.exists(pid_path)

    def test_stale_pidfile_falls_through_to_port_detection(self, pm, monkeypatch):
        """PID 文件失效后继续按端口探测，并以端口结果回写 PID 文件。"""
        _patch_probe(pm, monkeypatch, is_xray=False, listeners=[4242])
        _write_pidfile(pm, ALIVE_PID)

        probe = pm.probe_runtime()

        assert probe['running'] is True
        assert probe['pid'] == 4242
        assert probe['source'] == 'port'
        with open(pm.pid_file) as f:
            assert f.read().strip() == '4242'

    def test_port_detection_self_heals_pidfile(self, pm, monkeypatch):
        """核心回归：PID 文件不存在但端口上有 xray → 判定运行并自愈写回 PID。"""
        _patch_probe(pm, monkeypatch, listeners=[4242])
        assert not os.path.exists(pm.pid_file)

        probe = pm.probe_runtime()

        assert probe['running'] is True
        assert probe['pid'] == 4242
        assert probe['source'] == 'port'
        with open(pm.pid_file) as f:
            assert f.read().strip() == '4242'

    def test_stopped_when_no_pidfile_and_no_listener(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[])

        probe = pm.probe_runtime()

        assert probe['running'] is False
        assert probe['pid'] is None
        assert probe['source'] is None
        assert probe['warnings'] == []

    def test_multiple_instances_produce_warning(self, pm, monkeypatch):
        """端口上有 ≥2 个 xray 进程 → 运行中，且 warnings 提示多个实例。"""
        _patch_probe(pm, monkeypatch, listeners=[4242, 4343])

        probe = pm.probe_runtime()

        assert probe['running'] is True
        assert sorted(probe['pids']) == [4242, 4343]
        assert any('多个' in w and 'xray' in w for w in probe['warnings'])

    def test_scans_configured_ports(self, pm, monkeypatch):
        """端口探测覆盖 settings 里的 socks_port 与 http_port（默认 1080 / 1087）。"""
        seen = []
        _patch_probe(pm, monkeypatch, listeners=[], seen_ports=seen)

        pm.probe_runtime()

        assert seen, '未调用 _xray_pids_listening'
        assert {port for call in seen for port in call} == {1080, 1087}

    def test_scans_custom_ports_from_settings(self, pm, monkeypatch):
        pm.config.set_setting('socks_port', 2080)
        pm.config.set_setting('http_port', 2081)
        seen = []
        _patch_probe(pm, monkeypatch, listeners=[], seen_ports=seen)

        pm.probe_runtime()

        assert {port for call in seen for port in call} == {2080, 2081}


class TestIsRunningCrossCheck:
    """契约 4：is_running() / _get_pid() 改由 probe_runtime() 支撑。"""

    def test_running_when_pidfile_missing_but_xray_listens(self, pm, monkeypatch):
        """核心回归断言：PID 文件不存在但端口上有 xray，必须判定为运行中。"""
        _patch_probe(pm, monkeypatch, listeners=[4242])
        assert not os.path.exists(pm.pid_file)

        assert pm.is_running() is True

    def test_get_pid_returns_probe_pid(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[4242])

        assert pm._get_pid() == 4242

    def test_not_running_without_pidfile_and_listener(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[])

        assert pm.is_running() is False

    def test_get_pid_keeps_live_process_fast_path(self, pm):
        """self._process 存活时保留快速路径，不依赖 PID 文件。"""

        class FakeProcess:
            pid = 7777

            def poll(self):
                return None

        pm._process = FakeProcess()

        assert pm._get_pid() == 7777


class TestStartOrphanHandling:
    """契约 5：start() 对已运行实例幂等、对多实例拒启。"""

    def test_start_is_idempotent_when_single_instance_runs(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, is_xray=True, listeners=[])
        _write_pidfile(pm, ALIVE_PID)
        _forbid_spawn(pm, monkeypatch)

        assert pm.start() == 'alpha'

    def test_start_refuses_multiple_instances(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[4242, 4343])
        _forbid_spawn(pm, monkeypatch)

        with pytest.raises(ProxyStartError) as excinfo:
            pm.start()

        message = str(excinfo.value)
        assert '多个' in message
        assert 'xray' in message
        assert 'xpilot stop' in message


class TestRunningConfigMatch:
    """契约 6：运行中实例的配置与当前默认节点是否一致。"""

    def test_matching_config_with_vnext(self, pm):
        """跳过 freedom / blackhole，取第一个出站比对且一致 → True。"""
        _write_runtime_config(pm, [
            {'protocol': 'freedom', 'tag': 'direct', 'settings': {}},
            _vless_outbound(ALPHA_ADDR, ALPHA_PORT),
            {'protocol': 'blackhole', 'tag': 'block', 'settings': {}},
        ])

        matched, _detail = pm.running_config_matches_current()

        assert matched is True

    def test_mismatched_config_reports_both_endpoints(self, pm):
        _write_runtime_config(pm, [_vless_outbound(BETA_ADDR, BETA_PORT)])

        matched, detail = pm.running_config_matches_current()

        assert matched is False
        assert f'{ALPHA_ADDR}:{ALPHA_PORT}' in detail
        assert f'{BETA_ADDR}:{BETA_PORT}' in detail

    def test_matching_config_with_servers_shape(self, pm):
        """trojan / ss 用 settings.servers 形态，同样要能解析比对。"""
        _write_runtime_config(pm, [{
            'protocol': 'trojan',
            'tag': 'proxy',
            'settings': {'servers': [{'address': ALPHA_ADDR, 'port': ALPHA_PORT}]},
        }])

        matched, _detail = pm.running_config_matches_current()

        assert matched is True

    def test_missing_runtime_config_returns_none(self, pm):
        assert not os.path.exists(pm.runtime_config_path)

        matched, detail = pm.running_config_matches_current()

        assert matched is None
        assert isinstance(detail, str)


class TestGetStatus:
    """契约 7：get_status() 新增 source / warnings / runtime_dir / config_stale。"""

    def test_stopped_status_reports_new_fields(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, listeners=[])

        info = pm.get_status()

        assert info['running'] is False
        assert info['source'] is None
        assert info['warnings'] == []
        assert info['runtime_dir'] == pm.runtime_dir
        assert 'config_stale' in info
        assert info['config_stale'] is None
        assert 'config_stale_detail' in info

    def test_running_status_reports_mismatched_config(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, is_xray=True, listeners=[])
        _write_pidfile(pm, ALIVE_PID)
        _write_runtime_config(pm, [_vless_outbound(BETA_ADDR, BETA_PORT)])

        info = pm.get_status()

        assert info['running'] is True
        assert info['pid'] == ALIVE_PID
        assert info['current_node'] == 'alpha'
        assert info['node_name'] == 'alpha'
        assert info['source'] == 'pidfile'
        # 字段命名沿用契约：config_stale 直接取 running_config_matches_current 的布尔值，
        # False 表示运行实例的配置与当前默认节点不一致（CLI 据此提示 restart）。
        assert info['config_stale'] is False
        assert f'{ALPHA_ADDR}:{ALPHA_PORT}' in info['config_stale_detail']
        assert f'{BETA_ADDR}:{BETA_PORT}' in info['config_stale_detail']

    def test_running_status_with_matching_config(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, is_xray=True, listeners=[])
        _write_pidfile(pm, ALIVE_PID)
        _write_runtime_config(pm, [_vless_outbound(ALPHA_ADDR, ALPHA_PORT)])

        info = pm.get_status()

        assert info['running'] is True
        assert info['config_stale'] is True

    def test_running_status_without_runtime_config(self, pm, monkeypatch):
        _patch_probe(pm, monkeypatch, is_xray=True, listeners=[])
        _write_pidfile(pm, ALIVE_PID)

        info = pm.get_status()

        assert info['running'] is True
        assert info['config_stale'] is None
