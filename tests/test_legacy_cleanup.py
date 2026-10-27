"""遗留 /tmp 状态文件清理（Issue #5 第 2 条 / T22 契约 1、2）。

0.6.3 及更早版本把 PID 文件、运行配置、xray 日志写在 /tmp；新版本不读它们，
所以它们是惰性的——但事故排查时极具误导性（看起来像「旧实例还在跑」）。
清理的行为约定：只认固定的几个名字；PID 文件先确认对应进程已死才删；
单个文件删不掉不影响其余文件、也不向上抛；全程 tmp_dir 可注入。

本文件同时覆盖 ProxyManager 的接入点：start() 第一步、stop() 返回前各清理一次。
绝不触碰真实 /tmp：所有用例的 tmp_dir 都是 pytest 的 tmp_path。
"""

import logging
import os
from unittest.mock import MagicMock

import pytest

import xpilot.proxy_manager as proxy_manager_module
from xpilot import legacy_cleanup
from xpilot.legacy_cleanup import (
    LEGACY_FILES,
    LEGACY_LOG_GLOBS,
    LEGACY_PID_FILES,
    cleanup_legacy_state,
    find_legacy_paths,
)

ALIVE_PID = os.getpid()
# macOS 的 PID 上限是 5 位数，6 位数不可能存在，用来构造「确定已死」的 PID。
DEAD_PID = 999999


class _LogCollector:
    """注入用的日志收集器：签名固定为 log(level, message)。"""

    def __init__(self):
        self.entries = []

    def __call__(self, level, message):
        self.entries.append((level, message))

    @property
    def messages(self):
        return [message for _, message in self.entries]

    @property
    def warnings(self):
        return [message for level, message in self.entries if level == 'warning']


def _write(tmp_dir, name, content=''):
    path = os.path.join(str(tmp_dir), name)
    with open(path, 'w') as f:
        f.write(content)
    return path


@pytest.fixture
def collector():
    return _LogCollector()


class TestConstants:
    """契约 1：遗留文件名单固定，记录哪些路径属于「旧版本留下的」。"""

    def test_file_lists_match_contract(self):
        assert LEGACY_PID_FILES == ('xpilot.pid', 'xpilot-auto-switch.pid')
        assert LEGACY_FILES == ('xpilot-xray.json', 'xpilot.log')
        assert LEGACY_LOG_GLOBS == ('xpilot-xray-*.log',)


class TestFindLegacyPaths:
    """契约 1：find_legacy_paths 只报实际存在的遗留路径，且顺序稳定。"""

    def test_returns_existing_absolute_paths_sorted(self, tmp_path):
        names = ['xpilot.pid', 'xpilot-auto-switch.pid', 'xpilot-xray.json',
                 'xpilot.log', 'xpilot-xray-20260101.log']
        for name in names:
            _write(tmp_path, name)
        _write(tmp_path, 'unrelated.txt')

        found = find_legacy_paths(str(tmp_path))

        assert found == sorted(os.path.join(str(tmp_path), n) for n in names)
        assert all(os.path.isabs(p) for p in found)

    def test_empty_when_nothing_exists(self, tmp_path):
        assert find_legacy_paths(str(tmp_path)) == []

    def test_ignores_unrelated_files(self, tmp_path):
        _write(tmp_path, 'other.pid')
        _write(tmp_path, 'xpilot.log.bak')
        _write(tmp_path, 'xpilot-xray-notes.txt')

        assert find_legacy_paths(str(tmp_path)) == []

    def test_expands_every_log_glob_match(self, tmp_path):
        first = _write(tmp_path, 'xpilot-xray-2026.log')
        second = _write(tmp_path, 'xpilot-xray-2025-12.log')

        assert find_legacy_paths(str(tmp_path)) == sorted([first, second])


class TestCleanupLegacyState:
    """契约 1：cleanup_legacy_state 的删除、跳过、容错与幂等。"""

    def test_deletes_all_legacy_files_and_returns_paths(self, tmp_path, collector):
        paths = [
            _write(tmp_path, 'xpilot.pid', str(DEAD_PID)),
            _write(tmp_path, 'xpilot-auto-switch.pid', str(DEAD_PID)),
            _write(tmp_path, 'xpilot-xray.json', '{}'),
            _write(tmp_path, 'xpilot.log', 'old log'),
            _write(tmp_path, 'xpilot-xray-2026.log', 'xray log'),
        ]

        removed = cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: False,
                                       log=collector)

        assert removed == sorted(paths)
        assert all(not os.path.exists(p) for p in paths)

    def test_is_idempotent(self, tmp_path, collector):
        _write(tmp_path, 'xpilot.log', 'old log')
        cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: False, log=collector)

        assert cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: False,
                                    log=collector) == []

    def test_skips_pid_file_whose_process_is_alive(self, tmp_path, collector):
        alive = _write(tmp_path, 'xpilot.pid', '4242')
        dead = _write(tmp_path, 'xpilot-auto-switch.pid', '4243')
        dead_log = _write(tmp_path, 'xpilot-xray-2026.log', 'log')

        removed = cleanup_legacy_state(
            str(tmp_path), pid_alive=lambda pid: pid == 4242, log=collector)

        assert os.path.exists(alive)
        assert removed == sorted([dead, dead_log])
        assert not os.path.exists(dead)
        assert any(alive in message for message in collector.warnings)
        assert any(level == 'warning' for level, _ in collector.entries)

    @pytest.mark.parametrize('content', ['', 'not-a-pid', '4242abc', '  '])
    def test_pid_file_with_invalid_content_is_deleted(self, tmp_path, content, collector):
        path = _write(tmp_path, 'xpilot.pid', content)

        removed = cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: True,
                                       log=collector)

        assert removed == [path]
        assert not os.path.exists(path)

    def test_uses_os_kill_by_default(self, tmp_path, collector):
        """不注入 pid_alive 时：活进程的 PID 文件保留，死 PID 的文件删除。"""
        alive = _write(tmp_path, 'xpilot.pid', str(ALIVE_PID))
        dead = _write(tmp_path, 'xpilot-auto-switch.pid', str(DEAD_PID))

        removed = cleanup_legacy_state(str(tmp_path), log=collector)

        assert os.path.exists(alive)
        assert removed == [dead]
        assert any(alive in message for message in collector.warnings)

    def test_delete_failure_is_logged_and_skipped(self, tmp_path, collector, monkeypatch):
        broken = _write(tmp_path, 'xpilot-xray.json', '{}')
        healthy = _write(tmp_path, 'xpilot.log', 'old log')
        real_remove = os.remove

        def failing_remove(path, *args, **kwargs):
            if os.path.abspath(str(path)) == broken:
                raise OSError('permission denied')
            return real_remove(path, *args, **kwargs)

        monkeypatch.setattr(os, 'remove', failing_remove)
        monkeypatch.setattr(os, 'unlink', failing_remove)

        removed = cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: False,
                                       log=collector)

        assert removed == [healthy]
        assert not os.path.exists(healthy)
        assert os.path.exists(broken)
        assert any(broken in message for message in collector.warnings)

    def test_default_log_forwards_to_module_logger(self, tmp_path, caplog):
        alive = _write(tmp_path, 'xpilot.pid', str(ALIVE_PID))
        caplog.set_level(logging.INFO)

        cleanup_legacy_state(str(tmp_path), pid_alive=lambda pid: True)

        assert any(alive in record.getMessage() and record.levelno >= logging.WARNING
                   for record in caplog.records)


class TestProxyManagerIntegration:
    """契约 2：ProxyManager 委托清理，并挂在 start / stop 上。"""

    @pytest.fixture
    def pm(self, tmp_path):
        from xpilot.config import Config
        from xpilot.node_manager import NodeManager
        from xpilot.proxy_manager import ProxyManager
        from xpilot.routing_manager import RoutingManager

        config = Config(str(tmp_path / 'config'))
        config.init_default_configs()
        return ProxyManager(config, NodeManager(config), RoutingManager(config))

    def _patch_cleanup(self, monkeypatch):
        """把清理函数（模块内已导入的符号与源模块）一起替换成 spy。"""
        spy = MagicMock(return_value=[])
        monkeypatch.setattr(legacy_cleanup, 'cleanup_legacy_state', spy)
        monkeypatch.setattr(proxy_manager_module, 'cleanup_legacy_state', spy,
                            raising=False)
        return spy

    def test_method_delegates_with_log(self, pm, monkeypatch):
        spy = self._patch_cleanup(monkeypatch)
        spy.return_value = ['/tmp/xpilot.pid']

        assert pm.cleanup_legacy_state() == ['/tmp/xpilot.pid']
        spy.assert_called_once()
        assert callable(spy.call_args.kwargs.get('log'))

    def test_start_cleans_up_before_probing(self, pm, monkeypatch):
        spy = self._patch_cleanup(monkeypatch)
        seen = []

        def fake_probe():
            seen.append(spy.call_count)
            return {'running': True, 'pid': 4242, 'pids': [4242],
                    'source': 'pidfile', 'warnings': []}

        monkeypatch.setattr(pm, 'probe_runtime', fake_probe)
        monkeypatch.setattr(pm, 'set_system_proxy', lambda enabled: None)

        pm.start()

        assert spy.call_count == 1
        assert seen == [1], 'start() 应在探测运行状态之前完成遗留清理'

    def test_stop_cleans_up_before_returning(self, pm, monkeypatch):
        spy = self._patch_cleanup(monkeypatch)
        monkeypatch.setattr(pm, 'probe_runtime', lambda: {
            'running': False, 'pid': None, 'pids': [], 'source': None,
            'warnings': [],
        })
        monkeypatch.setattr(pm, '_kill_xray_on_ports', lambda: None)
        monkeypatch.setattr(pm, 'set_system_proxy', lambda enabled: None)

        pm.stop()

        spy.assert_called_once()

    def test_restart_covers_both_hooks(self, pm, monkeypatch):
        """restart() = stop() + start()，两次清理都会被触发。"""
        import types

        spy = self._patch_cleanup(monkeypatch)
        monkeypatch.setattr(pm, 'probe_runtime', lambda: {
            'running': False, 'pid': None, 'pids': [], 'source': None,
            'warnings': [],
        })
        monkeypatch.setattr(pm, '_kill_xray_on_ports', lambda: None)
        monkeypatch.setattr(pm, 'set_system_proxy', lambda enabled: None)
        monkeypatch.setattr(pm.node_manager, 'get_default_node', lambda: 'node-a')
        monkeypatch.setattr(pm.node_manager, 'get_node',
                            lambda node_id: {'name': 'node-a'})
        monkeypatch.setattr(pm.node_manager, 'set_default_node', lambda node_id: None)
        monkeypatch.setattr(pm, '_start_xray', lambda *args, **kwargs: setattr(
            pm, '_process', types.SimpleNamespace(pid=4242)))
        monkeypatch.setattr('xpilot.utils.is_port_available', lambda port: True)

        pm.restart()

        assert spy.call_count == 2
