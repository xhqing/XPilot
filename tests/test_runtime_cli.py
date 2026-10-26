"""CLI 侧运行时路径与配置新鲜度提示（Issue #1 / T21 契约 9）。

三件事：自动切换的 PID 文件与 monitor 日志不再落在 /tmp；status 在运行实例
用的是旧配置时提示 restart；subscription update 成功后若代理在跑也提示 restart。
全部用假 managers 调用 Click 命令，不触碰真实 xray / launchd / 网络。
"""

import os
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from xpilot import cli, utils
from xpilot.config import Config


def _runtime_dir(config_dir=None):
    return utils.get_runtime_dir(config_dir)


class TestRuntimePaths:
    """契约 9（路径部分）：模块级常量与 launchd plist 都不再指向 /tmp。"""

    def test_auto_switch_pid_file_leaves_tmp(self):
        assert not cli.AUTO_SWITCH_PID_FILE.startswith('/tmp')
        assert cli.AUTO_SWITCH_PID_FILE.startswith(_runtime_dir())
        assert cli.AUTO_SWITCH_PID_FILE.endswith('.pid')

    def test_launchd_plist_logs_leave_tmp(self):
        plist = cli._build_launchd_plist()
        runtime_dir = _runtime_dir()

        assert 'xpilot-monitor.log' in plist
        for key in ('StandardOutPath', 'StandardErrorPath'):
            marker = f'<key>{key}</key>'
            assert marker in plist
            value = plist[plist.index(marker):].split('<string>', 1)[1].split('</string>', 1)[0]
            assert value.startswith(runtime_dir + os.sep), f'{key} 仍在临时目录: {value}'


def _status_managers(tmp_path, *, config_stale, detail=''):
    config = Config(str(tmp_path))
    config.init_default_configs()
    info = {
        'running': True,
        'pid': 4242,
        'current_node': 'alpha',
        'node_name': 'alpha',
        'socks_port': 1080,
        'http_port': 1087,
        'source': 'pidfile',
        'warnings': [],
        'runtime_dir': utils.get_runtime_dir(config.config_dir),
        'config_stale': config_stale,
        'config_stale_detail': detail,
    }
    proxy_manager = SimpleNamespace(get_status=lambda: dict(info))
    health_checker = SimpleNamespace(_curl_through_socks=lambda port, timeout=8: (True, 204, 5))
    node_manager = SimpleNamespace(get_default_node=lambda: 'alpha',
                                   get_node=lambda node_id: {'name': 'Alpha'})
    return (config, node_manager, None, proxy_manager, health_checker, None)


def _invoke_status(monkeypatch, managers):
    monkeypatch.setattr(cli, 'get_managers', lambda: managers)
    return CliRunner().invoke(cli.cli, ['status'])


class TestStatusRestartHint:
    """契约 9（status 提示部分）：config_stale 为 False 时提示 restart。"""

    def test_hints_restart_when_running_config_differs(self, tmp_path, monkeypatch):
        managers = _status_managers(
            tmp_path, config_stale=False,
            detail='运行中: 203.0.113.7:443，当前节点: 198.51.100.9:8443')

        result = _invoke_status(monkeypatch, managers)

        assert result.exit_code == 0, result.output
        assert 'restart' in result.output

    def test_no_hint_when_config_matches(self, tmp_path, monkeypatch):
        managers = _status_managers(tmp_path, config_stale=True)

        result = _invoke_status(monkeypatch, managers)

        assert result.exit_code == 0, result.output
        assert 'restart' not in result.output

    def test_no_hint_when_freshness_unknown(self, tmp_path, monkeypatch):
        """运行时配置缺失（None）时无法确认，不得凭空提示 restart。"""
        managers = _status_managers(tmp_path, config_stale=None)

        result = _invoke_status(monkeypatch, managers)

        assert result.exit_code == 0, result.output
        assert 'restart' not in result.output


def _subscription_managers(tmp_path, *, running):
    config = Config(str(tmp_path))
    config.init_default_configs()
    settings = config.load_config('settings.json')
    settings['subscriptions'] = {'demo': 'https://example.com/sub.txt'}
    config.save_config('settings.json', settings)

    node_manager = SimpleNamespace(
        import_from_subscription=lambda url, update_existing=False, source=None: 0)
    proxy_manager = SimpleNamespace(
        is_running=lambda: running,
        get_status=lambda: {'running': running},
    )
    return (config, node_manager, None, proxy_manager, None, None)


class TestSubscriptionUpdateRestartHint:
    """契约 9（订阅提示部分）：刷新成功后运行中的实例需要 restart。"""

    def test_hints_restart_when_proxy_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, 'get_managers',
                            lambda: _subscription_managers(tmp_path, running=True))

        result = CliRunner().invoke(cli.cli, ['subscription', 'update'])

        assert result.exit_code == 0, result.output
        assert 'restart' in result.output

    def test_no_hint_when_proxy_stopped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, 'get_managers',
                            lambda: _subscription_managers(tmp_path, running=False))

        result = CliRunner().invoke(cli.cli, ['subscription', 'update'])

        assert result.exit_code == 0, result.output
        assert 'restart' not in result.output


@pytest.fixture(autouse=True)
def _no_real_managers(monkeypatch):
    """兜底：任何未显式注入 managers 的命令都不许真的初始化管理器。"""

    def explode():
        raise AssertionError('测试不得初始化真实 managers')

    monkeypatch.setattr(cli, 'get_managers', explode)
