"""陈旧实例自动恢复（Issue #5 第 1 条 / T22 契约 3、4、5）。

真实事故（2026-10）：节点 IP 漂移后，跑着的 xray 继续用内存里的旧地址，
代理显示「在线」但境外连接全部超时。发现两条触发路径：

* ``_check_stale_runtime``：运行实例的配置与当前默认节点明确不一致（三态里的
  只有 ``False`` 才动手，``None`` = 无法判定，不动）；
* ``_track_dead_nodes``：连续 N 轮全部节点真实流量不通。

两条路径统一走 ``_recover_restart``：窗口内限次 + 最小间隔防抖，超限退回只
告警，绝不允许重启死循环。

所有系统接缝都是 MagicMock（不真调 xray、不做网络探测）；``start()`` 的监控
线程被替换成空实现，避免测试里留下睡眠线程。
"""

import logging
import time
from unittest.mock import MagicMock

import pytest

from xpilot.auto_switch import AutoSwitch


def _recover_block(**overrides):
    block = {
        'enabled': True,
        'max_restarts': 3,
        'restart_window': 1800,
        'min_interval': 60,
        'dead_restart_ticks': 2,
    }
    block.update(overrides)
    return block


def _settings(**overrides):
    settings = {
        'auto_switch': {'enabled': False, 'hysteresis': 0.0},
        'watchdog': {'enabled': False},
        'subscriptions': {},
        'auto_recover': _recover_block(),
    }
    settings.update(overrides)
    return settings


def _make_asw(settings=None):
    config = MagicMock()
    config.load_config.return_value = _settings() if settings is None else settings
    asw = AutoSwitch(config, MagicMock(), MagicMock(), MagicMock())
    asw.proxy_manager.is_running.return_value = True
    asw.proxy_manager.running_config_matches_current.return_value = (True, '一致')
    asw.proxy_manager.restart.return_value = True
    return asw


def _start_quietly(asw, monkeypatch):
    """调 start() 装配配置，但不让监控线程真的跑 tick。"""
    monkeypatch.setattr(AutoSwitch, '_monitor_loop', lambda self: None)
    monkeypatch.setattr(AutoSwitch, '_watchdog_check', lambda self: None)
    asw.start()


def _arm(asw, **overrides):
    """直接装配恢复相关的实例属性，绕开 start() 只测决策逻辑。"""
    asw._auto_recover_enabled = True
    asw._recover_max_restarts = 3
    asw._recover_window = 1800
    asw._recover_min_interval = 60
    asw._dead_restart_ticks = 2
    asw._recover_times = []
    asw._dead_ticks = 0
    asw.proxy_manager.is_running.return_value = True
    for name, value in overrides.items():
        setattr(asw, name, value)
    return asw


class TestConfigReading:
    """契约 3：start() 读 auto_recover 配置块，旧配置缺块时用默认值。"""

    def test_start_reads_auto_recover_block(self, monkeypatch):
        asw = _make_asw(_settings(auto_recover=_recover_block(
            enabled=True, max_restarts=5, restart_window=900,
            min_interval=30, dead_restart_ticks=4)))

        _start_quietly(asw, monkeypatch)

        assert asw._auto_recover_enabled is True
        assert asw._recover_max_restarts == 5
        assert asw._recover_window == 900
        assert asw._recover_min_interval == 30
        assert asw._dead_restart_ticks == 4

    def test_start_honours_disabled_flag(self, monkeypatch):
        asw = _make_asw(_settings(auto_recover=_recover_block(enabled=False)))

        _start_quietly(asw, monkeypatch)

        assert asw._auto_recover_enabled is False

    def test_start_defaults_when_block_missing(self, monkeypatch):
        """0.6.4 及更早生成的 settings.json 没有 auto_recover 块，不能抛错。"""
        settings = _settings()
        del settings['auto_recover']
        asw = _make_asw(settings)

        _start_quietly(asw, monkeypatch)

        assert asw._auto_recover_enabled is True
        assert asw._recover_max_restarts == 3
        assert asw._recover_window == 1800
        assert asw._recover_min_interval == 60
        assert asw._dead_restart_ticks == 2
        assert asw._recover_times == []
        assert asw._dead_ticks == 0

    def test_monitor_starts_when_only_auto_recover_enabled(self, monkeypatch):
        settings = _settings(
            watchdog={'enabled': False},
            auto_switch={'enabled': False},
            subscription={'auto_update': False, 'update_interval': 3600},
        )
        asw = _make_asw(settings)

        _start_quietly(asw, monkeypatch)

        assert asw._running is True


class TestRecoverRestart:
    """契约 4：统一重启执行器——限次、防抖、失败不抛、原因落日志。"""

    def test_does_nothing_when_disabled(self):
        asw = _arm(_make_asw(), _auto_recover_enabled=False)

        assert asw._recover_restart('陈旧') is False
        asw.proxy_manager.restart.assert_not_called()

    def test_does_not_start_a_dead_proxy(self):
        """不负责拉起：watchdog 的活。代理没在跑时不动手。"""
        asw = _arm(_make_asw())
        asw.proxy_manager.is_running.return_value = False

        assert asw._recover_restart('陈旧') is False
        asw.proxy_manager.restart.assert_not_called()

    def test_restarts_once_and_logs_reason(self, caplog):
        asw = _arm(_make_asw())
        caplog.set_level(logging.INFO)

        assert asw._recover_restart('配置陈旧（旧地址）') is True

        asw.proxy_manager.restart.assert_called_once_with()
        assert len(asw._recover_times) == 1
        assert any('配置陈旧（旧地址）' in record.getMessage()
                   for record in caplog.records)

    def test_gives_up_after_max_restarts(self, caplog):
        now = time.time()
        asw = _arm(_make_asw(),
                   _recover_times=[now - 5, now - 10, now - 15],
                   _recover_min_interval=0)
        caplog.set_level(logging.WARNING)

        assert asw._recover_restart('反复失败') is False

        asw.proxy_manager.restart.assert_not_called()
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING]
        assert any('上限' in message and '反复失败' in message
                   for message in warnings)

    def test_prunes_timestamps_outside_window(self):
        now = time.time()
        asw = _arm(_make_asw(),
                   _recover_times=[now - 4000, now - 100],
                   _recover_min_interval=0)

        assert asw._recover_restart('窗口外记录不该计数') is True

        asw.proxy_manager.restart.assert_called_once_with()
        assert len(asw._recover_times) == 2

    def test_debounces_rapid_restarts(self):
        now = time.time()
        asw = _arm(_make_asw(), _recover_times=[now - 5])

        assert asw._recover_restart('刚重启过') is False
        asw.proxy_manager.restart.assert_not_called()
        assert len(asw._recover_times) == 1

    def test_restart_failure_does_not_propagate(self):
        asw = _arm(_make_asw())
        asw.proxy_manager.restart.side_effect = RuntimeError('端口被占用')

        asw._recover_restart('重启失败')  # 不得抛出

        asw.proxy_manager.restart.assert_called_once_with()

    def test_restart_failure_returns_false_and_consumes_quota(self):
        """失败也算一次尝试：返回值是 False，但时间戳照样入账（防重启死循环）。"""
        asw = _arm(_make_asw())
        asw.proxy_manager.restart.side_effect = RuntimeError('端口被占用')

        assert asw._recover_restart('重启失败') is False
        assert len(asw._recover_times) == 1

    def test_repeated_failures_hit_the_limit(self, caplog):
        asw = _arm(_make_asw(), _recover_max_restarts=3, _recover_min_interval=0)
        asw.proxy_manager.restart.side_effect = RuntimeError('端口被占用')
        caplog.set_level(logging.WARNING)

        results = [asw._recover_restart(f'第 {i} 次') for i in range(1, 5)]

        assert results == [False, False, False, False]
        assert asw.proxy_manager.restart.call_count == 3
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING]
        assert any('上限' in message for message in warnings)


class TestCheckStaleRuntime:
    """契约 4：只有明确不一致（False）才重启，三态里的 None 不动手。"""

    def test_skips_when_proxy_not_running(self):
        asw = _arm(_make_asw())
        asw.proxy_manager.is_running.return_value = False

        assert asw._check_stale_runtime() is False
        asw.proxy_manager.running_config_matches_current.assert_not_called()
        asw.proxy_manager.restart.assert_not_called()

    @pytest.mark.parametrize('matched,detail', [(True, '一致'), (None, '无法判定')])
    def test_ignores_consistent_or_unknown(self, matched, detail):
        asw = _arm(_make_asw())
        asw.proxy_manager.running_config_matches_current.return_value = (matched, detail)

        assert asw._check_stale_runtime() is False
        asw.proxy_manager.restart.assert_not_called()

    def test_restarts_and_logs_reason_on_mismatch(self, caplog):
        detail = '运行实例仍使用 203.0.113.7:443'
        asw = _arm(_make_asw())
        asw.proxy_manager.running_config_matches_current.return_value = (False, detail)
        caplog.set_level(logging.INFO)

        assert asw._check_stale_runtime() is True

        asw.proxy_manager.restart.assert_called_once_with()
        assert any('配置陈旧' in record.getMessage() and detail in record.getMessage()
                   for record in caplog.records)

    def test_mismatch_reason_is_forwarded_to_recover(self):
        asw = _arm(_make_asw())
        asw.proxy_manager.running_config_matches_current.return_value = (False, '旧地址')
        asw._recover_restart = MagicMock(return_value=False)

        assert asw._check_stale_runtime() is False

        asw._recover_restart.assert_called_once()
        reason = asw._recover_restart.call_args.args[0]
        assert '陈旧' in reason and '旧地址' in reason

    def test_does_not_restart_when_recover_disabled(self):
        asw = _arm(_make_asw(), _auto_recover_enabled=False)
        asw.proxy_manager.running_config_matches_current.return_value = (False, '旧地址')

        assert asw._check_stale_runtime() is False
        asw.proxy_manager.restart.assert_not_called()


class TestTrackDeadNodes:
    """契约 4：连续 N 轮全部节点不通才重启，恢复即归零。"""

    def test_usable_nodes_reset_counter(self):
        asw = _arm(_make_asw(), _dead_ticks=2)

        assert asw._track_dead_nodes(1) is False
        assert asw._dead_ticks == 0

    def test_counts_until_threshold(self):
        asw = _arm(_make_asw(), _dead_ticks=0, _dead_restart_ticks=2)
        asw._recover_restart = MagicMock(return_value=False)

        assert asw._track_dead_nodes(0) is False
        assert asw._dead_ticks == 1
        asw._recover_restart.assert_not_called()

        assert asw._track_dead_nodes(0) is False
        asw._recover_restart.assert_called_once()
        reason = asw._recover_restart.call_args.args[0]
        assert '连续 2 轮' in reason
        assert asw._dead_ticks == 0

    def test_resets_counter_before_calling_recover(self):
        asw = _arm(_make_asw(), _dead_ticks=1, _dead_restart_ticks=2)
        seen = {}

        def fake_recover(reason):
            seen['dead_ticks'] = asw._dead_ticks
            return True

        asw._recover_restart = fake_recover

        assert asw._track_dead_nodes(0) is True
        assert seen['dead_ticks'] == 0
        assert asw._dead_ticks == 0

    @pytest.mark.parametrize('ticks', [0, -1])
    def test_non_positive_threshold_disables_dead_restart(self, ticks, monkeypatch):
        asw = _make_asw(_settings(auto_recover=_recover_block(dead_restart_ticks=ticks)))
        _start_quietly(asw, monkeypatch)
        asw._recover_restart = MagicMock(return_value=True)

        for _ in range(10):
            assert asw._track_dead_nodes(0) is False

        asw._recover_restart.assert_not_called()

    @pytest.mark.parametrize('ticks', [0, -1])
    def test_disabled_threshold_does_not_accumulate_state(self, ticks, monkeypatch):
        """禁用就彻底不记账：不能留着计数让下次启用时提前触发。"""
        asw = _make_asw(_settings(auto_recover=_recover_block(dead_restart_ticks=ticks)))
        _start_quietly(asw, monkeypatch)

        for _ in range(10):
            asw._track_dead_nodes(0)

        assert asw._dead_ticks == 0


class TestMonitorLoopWiring:
    """契约 5：每个 tick 都查陈旧配置；订阅同步由自身间隔判定。"""

    def _one_tick(self, asw, monkeypatch):
        asw._running = True
        asw._watchdog_enabled = False
        asw._auto_switch_enabled = False
        asw._tick_interval = 0
        asw._check_stale_runtime = MagicMock(return_value=False)
        asw._subscription_sync_tick = MagicMock(return_value=False)

        def stop_after_one_tick(_seconds):
            asw._running = False

        monkeypatch.setattr(time, 'sleep', stop_after_one_tick)
        asw._monitor_loop()

    def test_tick_runs_stale_check_every_cycle(self, monkeypatch):
        asw = _arm(_make_asw(), _subscription_sync_enabled=False)

        self._one_tick(asw, monkeypatch)

        asw._check_stale_runtime.assert_called_once_with()

    def test_tick_runs_subscription_sync_when_enabled(self, monkeypatch):
        asw = _arm(_make_asw(), _subscription_sync_enabled=True)

        self._one_tick(asw, monkeypatch)

        asw._subscription_sync_tick.assert_called_once_with()

    def test_tick_skips_stale_check_when_recover_disabled(self, monkeypatch):
        asw = _arm(_make_asw(), _auto_recover_enabled=False,
                   _subscription_sync_enabled=False)

        self._one_tick(asw, monkeypatch)

        asw._check_stale_runtime.assert_not_called()

    def test_tick_skips_subscription_sync_when_disabled(self, monkeypatch):
        asw = _arm(_make_asw(), _subscription_sync_enabled=False)

        self._one_tick(asw, monkeypatch)

        asw._subscription_sync_tick.assert_not_called()
