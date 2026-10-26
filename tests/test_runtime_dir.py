"""运行时目录与状态文件路径（Issue #1 / T21 契约 1、2、8）。

背景：状态文件原先硬编码在 /tmp 下，被系统清理后出现「进程还在跑、工具显示
Stopped」的状态错位，代理静默失效。本文件锁定修复后的路径契约：状态文件一律
落进 runtime 目录（默认 <config_dir>/run，可用环境变量覆盖），程序日志默认值
不再指向 /tmp，settings.json 里的旧默认日志路径做一次窄迁移。
"""

import os

import pytest

from xpilot import utils
from xpilot.config import Config
from xpilot.node_manager import NodeManager
from xpilot.proxy_manager import ProxyManager
from xpilot.routing_manager import RoutingManager


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / 'config'


@pytest.fixture
def ready_config(config_dir):
    config = Config(str(config_dir))
    config.init_default_configs()
    return config


@pytest.fixture
def proxy_manager(ready_config):
    return ProxyManager(ready_config, NodeManager(ready_config), RoutingManager(ready_config))


def _runtime_dir(config_dir):
    return utils.get_runtime_dir(str(config_dir))


class TestGetRuntimeDir:
    """契约 1：get_runtime_dir(config_dir=None) 的取值优先级与目录属性。"""

    def test_env_runtime_dir_has_highest_priority(self, tmp_path, monkeypatch):
        """PROXY_TOOLKIT_RUNTIME_DIR 优先于 PROXY_TOOLKIT_CONFIG_DIR。"""
        explicit = tmp_path / 'explicit-runtime'
        monkeypatch.setenv('PROXY_TOOLKIT_RUNTIME_DIR', str(explicit))
        monkeypatch.setenv('PROXY_TOOLKIT_CONFIG_DIR', str(tmp_path / 'other-config'))

        assert utils.get_runtime_dir() == str(explicit)
        assert explicit.is_dir()

    def test_config_env_appends_run_subdir(self, tmp_path, monkeypatch):
        """未设 RUNTIME_DIR 时退化为 $PROXY_TOOLKIT_CONFIG_DIR/run。"""
        monkeypatch.delenv('PROXY_TOOLKIT_RUNTIME_DIR', raising=False)
        config_env = tmp_path / 'env-config'
        monkeypatch.setenv('PROXY_TOOLKIT_CONFIG_DIR', str(config_env))

        assert utils.get_runtime_dir() == os.path.join(str(config_env), 'run')

    def test_config_dir_argument_is_respected(self, tmp_path, monkeypatch):
        """两个环境变量都没设时，用传入的 config_dir 拼 run 子目录。"""
        monkeypatch.delenv('PROXY_TOOLKIT_RUNTIME_DIR', raising=False)
        monkeypatch.delenv('PROXY_TOOLKIT_CONFIG_DIR', raising=False)
        given = tmp_path / 'given-config'

        assert utils.get_runtime_dir(str(given)) == os.path.join(str(given), 'run')

    def test_returns_absolute_path(self, tmp_path, monkeypatch):
        """返回值必须是绝对路径（相对入参也要解析成绝对路径）。"""
        monkeypatch.delenv('PROXY_TOOLKIT_RUNTIME_DIR', raising=False)
        monkeypatch.delenv('PROXY_TOOLKIT_CONFIG_DIR', raising=False)
        monkeypatch.chdir(tmp_path)

        resolved = utils.get_runtime_dir('relative-config')

        assert os.path.isabs(resolved)
        assert resolved == os.path.join(str(tmp_path), 'relative-config', 'run')

    def test_directory_is_private(self, tmp_path, monkeypatch):
        """目录不存在时自动创建，权限为 0700（仅属主可读写执行）。"""
        monkeypatch.delenv('PROXY_TOOLKIT_RUNTIME_DIR', raising=False)
        monkeypatch.delenv('PROXY_TOOLKIT_CONFIG_DIR', raising=False)
        runtime = tmp_path / 'perm-config' / 'run'

        utils.get_runtime_dir(str(tmp_path / 'perm-config'))

        assert runtime.is_dir()
        assert os.stat(str(runtime)).st_mode & 0o777 == 0o700


class TestProxyManagerRuntimePaths:
    """契约 2：四个状态文件全部落在 runtime 目录，不再以 /tmp 开头。"""

    def test_runtime_dir_matches_helper(self, proxy_manager, ready_config):
        assert proxy_manager.runtime_dir == utils.get_runtime_dir(ready_config.config_dir)

    def test_all_state_files_live_under_runtime_dir(self, proxy_manager, ready_config):
        runtime = os.path.join(ready_config.config_dir, 'run')
        expected = {
            'pid_file': os.path.join(runtime, 'xpilot.pid'),
            'runtime_config_path': os.path.join(runtime, 'xray-config.json'),
            'stdout_log_path': os.path.join(runtime, 'xray-stdout.log'),
            'stderr_log_path': os.path.join(runtime, 'xray-stderr.log'),
        }

        for attr, path in expected.items():
            actual = getattr(proxy_manager, attr)
            assert actual == path, f'{attr} 应指向 runtime 目录下的 {os.path.basename(path)}'
            assert actual.startswith(runtime + os.sep), f'{attr} 不在 runtime 目录内: {actual}'
            assert not actual.startswith('/tmp/'), f'{attr} 仍落在 /tmp: {actual}'

    def test_no_tmp_pid_constant_left(self):
        """类级 PID_FILE 常量不得再指向 /tmp（已删除或改指 runtime 目录都算通过）。"""
        constant = getattr(ProxyManager, 'PID_FILE', None)
        assert not (isinstance(constant, str) and constant.startswith('/tmp'))


class TestDefaultLogFile:
    """契约 8：默认日志路径迁出 /tmp，并对旧默认值做一次窄迁移。"""

    def test_default_log_file_is_under_config_dir(self, config_dir):
        config = Config(str(config_dir))

        log_file = config.get_default_settings_config()['log_file']

        assert log_file == os.path.join(str(config_dir), 'logs', 'xpilot.log')
        assert not log_file.startswith('/tmp')

    def test_migrates_legacy_default_log_file(self, config_dir):
        """旧默认值 /tmp/xpilot.log 在 Config 初始化时改写成新默认路径。"""
        config = Config(str(config_dir))
        settings = config.get_default_settings_config()
        settings['log_file'] = '/tmp/xpilot.log'
        config.save_config('settings.json', settings)

        reloaded = Config(str(config_dir))

        migrated = reloaded.load_config('settings.json')['log_file']
        assert migrated == os.path.join(str(config_dir), 'logs', 'xpilot.log')

    def test_keeps_custom_log_file(self, config_dir):
        """自定义的非默认路径不得被迁移改写。"""
        config = Config(str(config_dir))
        settings = config.get_default_settings_config()
        settings['log_file'] = '/var/log/xpilot.log'
        config.save_config('settings.json', settings)

        reloaded = Config(str(config_dir))

        assert reloaded.load_config('settings.json')['log_file'] == '/var/log/xpilot.log'

    def test_does_not_create_settings_when_absent(self, config_dir):
        """窄迁移只在 settings.json 已存在时生效，不得凭空创建配置文件。"""
        Config(str(config_dir))

        assert not os.path.exists(os.path.join(str(config_dir), 'settings.json'))
