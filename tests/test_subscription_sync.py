"""定时订阅同步（Issue #6 / T22 契约 3、4、5）。

``settings.json`` 里的 ``subscription.auto_update`` / ``update_interval`` 曾经是
死配置：生成了默认值、README 也列着，但没有代码读取它。方案 A（实现它）：
monitor 按间隔定时拉订阅刷新节点，与 Issue #5 的自动重启配套——拉下来不生效
等于白拉。

验收要点：间隔真正生效；失败不收紧重试节奏（失败也推进时间戳）；刷新失败只
记日志不影响其它源；定时同步与「全节点挂掉时的应急刷新」并存互补。

所有网络入口（``node_manager.import_from_subscription``）都是假的，测试不联网。
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
        'subscriptions': {'订阅甲': 'https://example.com/sub-a'},
        'auto_recover': _recover_block(),
        'subscription': {'auto_update': True, 'update_interval': 3600},
    }
    settings.update(overrides)
    return settings


def _make_asw(settings=None):
    config = MagicMock()
    config.load_config.return_value = _settings() if settings is None else settings
    asw = AutoSwitch(config, MagicMock(), MagicMock(), MagicMock())
    asw.proxy_manager.is_running.return_value = True
    asw.proxy_manager.running_config_matches_current.return_value = (True, '一致')
    return asw


def _start_quietly(asw, monkeypatch):
    """调 start() 装配配置，但不让监控线程真的跑 tick。"""
    monkeypatch.setattr(AutoSwitch, '_monitor_loop', lambda self: None)
    monkeypatch.setattr(AutoSwitch, '_watchdog_check', lambda self: None)
    asw.start()


class TestSubscriptionConfig:
    """契约 3：start() 读 subscription 配置块，非法间隔回退默认值。"""

    def test_start_reads_subscription_block(self, monkeypatch):
        asw = _make_asw(_settings(
            subscription={'auto_update': True, 'update_interval': 900}))

        _start_quietly(asw, monkeypatch)

        assert asw._subscription_sync_enabled is True
        assert asw._subscription_sync_interval == 900

    def test_start_honours_auto_update_false(self, monkeypatch):
        asw = _make_asw(_settings(
            subscription={'auto_update': False, 'update_interval': 900}))

        _start_quietly(asw, monkeypatch)

        assert asw._subscription_sync_enabled is False
        assert asw._subscription_sync_interval == 900

    def test_start_defaults_when_block_missing(self, monkeypatch):
        """旧 settings.json 没有 subscription 块：不抛错，默认开启 + 间隔 3600。"""
        settings = _settings()
        del settings['subscription']
        asw = _make_asw(settings)

        _start_quietly(asw, monkeypatch)

        assert asw._subscription_sync_enabled is True
        assert asw._subscription_sync_interval == 3600

    @pytest.mark.parametrize('interval', [0, -60, 'abc', None, '900s'])
    def test_invalid_interval_falls_back_to_default(self, interval, monkeypatch):
        asw = _make_asw(_settings(
            subscription={'auto_update': True, 'update_interval': interval}))

        _start_quietly(asw, monkeypatch)

        assert asw._subscription_sync_interval == 3600

    def test_start_marks_sync_clock_to_avoid_immediate_fetch(self, monkeypatch):
        asw = _make_asw(_settings(
            subscription={'auto_update': True, 'update_interval': 3600}))
        monkeypatch.setattr(time, 'time', lambda: 1234.5)

        _start_quietly(asw, monkeypatch)

        assert asw._last_sub_sync == 1234.5

    def test_tick_interval_never_exceeds_30_seconds(self, monkeypatch):
        asw = _make_asw(_settings(
            watchdog={'enabled': False},
            auto_switch={'enabled': False},
            subscription={'auto_update': True, 'update_interval': 3600}))

        _start_quietly(asw, monkeypatch)

        assert asw._tick_interval == 30

    def test_tick_interval_uses_smaller_enabled_interval(self, monkeypatch):
        asw = _make_asw(_settings(
            watchdog={'enabled': True, 'interval': 10},
            auto_switch={'enabled': False},
            subscription={'auto_update': True, 'update_interval': 3600}))

        _start_quietly(asw, monkeypatch)

        assert asw._tick_interval == 10

    def test_monitor_starts_when_only_subscription_enabled(self, monkeypatch):
        asw = _make_asw(_settings(
            watchdog={'enabled': False},
            auto_switch={'enabled': False},
            subscription={'auto_update': True, 'update_interval': 3600},
            auto_recover=_recover_block(enabled=False)))

        _start_quietly(asw, monkeypatch)

        assert asw._running is True

    def test_monitor_not_started_when_everything_disabled(self, monkeypatch):
        asw = _make_asw(_settings(
            watchdog={'enabled': False},
            auto_switch={'enabled': False},
            subscription={'auto_update': False, 'update_interval': 3600},
            auto_recover=_recover_block(enabled=False)))

        _start_quietly(asw, monkeypatch)

        assert asw._running is False


class TestRefreshAllSubscriptions:
    """契约 4：逐个源刷新，失败只记日志不中断，无源返回 0。"""

    def test_imports_every_source_and_sums_counts(self):
        asw = _make_asw(_settings(
            subscriptions={'甲': 'https://example.com/a', '乙': 'https://example.com/b'}))
        asw.node_manager.import_from_subscription.side_effect = [5, 3]

        assert asw._refresh_all_subscriptions() == 8

        calls = asw.node_manager.import_from_subscription.call_args_list
        assert calls[0].args == ('https://example.com/a',)
        assert calls[0].kwargs == {'update_existing': True, 'source': '甲'}
        assert calls[1].args == ('https://example.com/b',)
        assert calls[1].kwargs == {'update_existing': True, 'source': '乙'}

    def test_single_source_failure_does_not_stop_the_rest(self, caplog):
        asw = _make_asw(_settings(
            subscriptions={'甲': 'https://example.com/a', '乙': 'https://example.com/b'}))
        asw.node_manager.import_from_subscription.side_effect = [
            RuntimeError('订阅端 502'), 4]
        caplog.set_level(logging.ERROR)

        assert asw._refresh_all_subscriptions() == 4

        assert asw.node_manager.import_from_subscription.call_count == 2
        assert any('502' in record.getMessage() for record in caplog.records)

    def test_returns_zero_without_sources(self):
        asw = _make_asw(_settings(subscriptions={}))

        assert asw._refresh_all_subscriptions() == 0
        asw.node_manager.import_from_subscription.assert_not_called()


class TestSubscriptionSyncTick:
    """契约 4：间隔到了才拉，失败也推进时间戳（不收紧重试节奏）。"""

    def _armed(self, **attrs):
        asw = _make_asw()
        asw._subscription_sync_enabled = True
        asw._subscription_sync_interval = 3600
        asw._last_sub_sync = 0.0
        asw._refresh_all_subscriptions = MagicMock(return_value=6)
        for name, value in attrs.items():
            setattr(asw, name, value)
        return asw

    def test_disabled_returns_false_without_refreshing(self):
        asw = self._armed(_subscription_sync_enabled=False)

        assert asw._subscription_sync_tick() is False
        asw._refresh_all_subscriptions.assert_not_called()

    def test_waits_for_the_interval(self, monkeypatch):
        asw = self._armed(_last_sub_sync=1000.0)
        monkeypatch.setattr(time, 'time', lambda: 4599.0)  # 3599s < 3600s

        assert asw._subscription_sync_tick() is False
        asw._refresh_all_subscriptions.assert_not_called()

    def test_refreshes_when_due(self, monkeypatch, caplog):
        asw = self._armed(_last_sub_sync=1000.0)
        monkeypatch.setattr(time, 'time', lambda: 4600.0)  # 恰好一轮
        caplog.set_level(logging.INFO)

        assert asw._subscription_sync_tick() is True

        asw._refresh_all_subscriptions.assert_called_once_with()
        assert asw._last_sub_sync == 4600.0
        assert caplog.records, '刷新成功应记一条 info 日志'

    def test_empty_refresh_still_advances_the_clock(self, monkeypatch):
        asw = self._armed(_last_sub_sync=1000.0)
        asw._refresh_all_subscriptions.return_value = 0
        monkeypatch.setattr(time, 'time', lambda: 4600.0)

        assert asw._subscription_sync_tick() is False
        assert asw._last_sub_sync == 4600.0

    def test_clock_advances_before_refreshing(self, monkeypatch):
        """先推进时间戳再拉订阅：刷新中途出问题也不会立刻重试。"""
        asw = self._armed(_last_sub_sync=1000.0)
        seen = []

        def fake_refresh():
            seen.append(asw._last_sub_sync)
            return 3

        asw._refresh_all_subscriptions = MagicMock(side_effect=fake_refresh)
        monkeypatch.setattr(time, 'time', lambda: 4600.0)

        assert asw._subscription_sync_tick() is True
        assert seen == [4600.0]


class TestDeadNodeHookInSwitch:
    """契约 5：_check_and_switch 把「全挂轮次」喂给 _track_dead_nodes。"""

    def _switch_asw(self, results):
        config = MagicMock()
        config.load_config.return_value = _settings(
            auto_switch={'enabled': True, 'hysteresis': 0.0},
            subscriptions={})
        node_manager = MagicMock()
        node_manager.list_nodes.return_value = [
            {'id': 'a', 'status': 'active'},
            {'id': 'b', 'status': 'active'},
        ]
        node_manager.get_default_node.return_value = 'a'
        health_checker = MagicMock()
        health_checker.batch_check_real.return_value = results
        asw = AutoSwitch(config, node_manager, health_checker, MagicMock())
        asw._track_dead_nodes = MagicMock(return_value=False)
        return asw

    @staticmethod
    def _dead(node_id):
        return {'id': node_id, 'ok': False, 'latency_ms': None, 'name': node_id}

    @staticmethod
    def _ok(node_id, latency):
        return {'id': node_id, 'ok': True, 'latency_ms': latency, 'name': node_id}

    def test_reports_zero_when_refresh_cannot_help(self):
        asw = self._switch_asw([self._dead('a'), self._dead('b')])
        asw._try_refresh_subscription = MagicMock(return_value=False)

        asw._check_and_switch()

        asw._track_dead_nodes.assert_called_once_with(0)

    def test_reports_zero_when_retest_still_dead(self):
        asw = self._switch_asw([self._dead('a'), self._dead('b')])
        asw._try_refresh_subscription = MagicMock(return_value=True)
        asw.health_checker.batch_check_real.side_effect = [
            [self._dead('a'), self._dead('b')],
            [self._dead('a'), self._dead('b')],
        ]

        asw._check_and_switch()

        asw._track_dead_nodes.assert_called_once_with(0)

    def test_reports_recovered_count_after_refresh(self):
        asw = self._switch_asw([self._dead('a'), self._dead('b')])
        asw._try_refresh_subscription = MagicMock(return_value=True)
        asw.health_checker.batch_check_real.side_effect = [
            [self._dead('a'), self._dead('b')],
            [self._dead('a'), self._ok('b', 120)],
        ]

        asw._check_and_switch()

        asw._track_dead_nodes.assert_called_once_with(1)

    def test_reports_usable_count_and_resets(self):
        asw = self._switch_asw([self._ok('a', 100), self._ok('b', 500)])

        asw._check_and_switch()

        asw._track_dead_nodes.assert_called_once_with(2)
        asw.proxy_manager.start.assert_not_called()
