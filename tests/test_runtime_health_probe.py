"""探活临时配置的落盘位置（Issue #1 / T21 契约 10）。

不启动真实 xray：Popen 与进程清理都注入假实现，只校验临时配置写进 runtime
目录、且注入给 xray 的 -config 参数指向同一路径。
"""

import io
import os
import sys

from xpilot import utils
from xpilot.config import Config
from xpilot.health_checker import HealthChecker
from xpilot.node_manager import NodeManager
from xpilot.proxy_manager import ProxyManager
from xpilot.routing_manager import RoutingManager

NODE = {
    'protocol': 'vless',
    'address': '203.0.113.7',
    'port': 443,
    'uuid': 'aabbccdd-1234-5678-90ab-cdef01234567',
}


class FakeProc:
    """假 xray 进程：poll() 恒为 None，表示启动后存活。"""

    def __init__(self, pid=4242):
        self.pid = pid
        self.stderr = io.BytesIO(b'')
        self.returncode = None

    def poll(self):
        return None


def _make_checker(tmp_path):
    config = Config(str(tmp_path))
    config.init_default_configs()
    settings = config.load_config('settings.json')
    # 指向一个确实存在的可执行文件（Popen 已被替换，不会真的执行）
    settings['xray_bin'] = sys.executable
    config.save_config('settings.json', settings)
    proxy_manager = ProxyManager(config, NodeManager(config), RoutingManager(config))
    return config, HealthChecker(proxy_manager)


def test_probe_config_lives_in_runtime_dir(tmp_path, monkeypatch):
    config, checker = _make_checker(tmp_path)
    runtime_dir = utils.get_runtime_dir(config.config_dir)
    captured = {}

    def fake_popen(argv, **kwargs):
        captured['argv'] = list(argv)
        return FakeProc()

    monkeypatch.setattr('xpilot.health_checker.subprocess.Popen', fake_popen)
    monkeypatch.setattr(checker, 'check_latency', lambda node, timeout=5: 5.0)
    monkeypatch.setattr(checker, '_find_free_port', lambda: 18080)
    killed = []
    monkeypatch.setattr(checker, '_kill_proc', lambda proc: killed.append(proc))

    info = checker._start_probe(NODE)

    cfg_path = info['cfg_path']
    assert cfg_path is not None
    assert cfg_path.startswith(runtime_dir + os.sep)
    assert not cfg_path.startswith('/tmp/')
    assert os.path.exists(cfg_path)

    argv = captured['argv']
    config_arg = argv[argv.index('-config') + 1]
    assert config_arg.startswith(runtime_dir + os.sep)
    assert config_arg == cfg_path


def test_probe_config_name_is_not_legacy_tmp_pattern(tmp_path, monkeypatch):
    """临时配置文件名不带旧 /tmp 形态的绝对路径前缀，且位于 runtime 目录。"""
    config, checker = _make_checker(tmp_path)
    runtime_dir = utils.get_runtime_dir(config.config_dir)
    captured = {}

    def fake_popen(argv, **kwargs):
        captured['argv'] = list(argv)
        return FakeProc(pid=4343)

    monkeypatch.setattr('xpilot.health_checker.subprocess.Popen', fake_popen)
    monkeypatch.setattr(checker, 'check_latency', lambda node, timeout=5: 5.0)
    monkeypatch.setattr(checker, '_find_free_port', lambda: 18081)
    monkeypatch.setattr(checker, '_kill_proc', lambda proc: None)

    info = checker._start_probe(NODE)

    assert os.path.dirname(info['cfg_path']) == runtime_dir
    assert '/tmp/' not in info['cfg_path']
    assert captured['argv'].count('-config') == 1
