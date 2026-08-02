"""Tests for AutoSwitch module —— 真实流量选优与切换决策。"""

from unittest.mock import MagicMock

import pytest

from xpilot.auto_switch import AutoSwitch


@pytest.fixture
def asw():
    """AutoSwitch，依赖全部 MagicMock 隔离。默认 hysteresis=0（最优不是当前就切）。"""
    config = MagicMock()
    config.load_config.return_value = {
        'auto_switch': {'enabled': True, 'hysteresis': 0.0},
        'subscriptions': {},
    }
    nm = MagicMock()
    nm.list_nodes.return_value = [
        {'id': 'a', 'status': 'active'},
        {'id': 'b', 'status': 'active'},
    ]
    nm.get_default_node.return_value = 'a'
    hc = MagicMock()
    pm = MagicMock()
    return AutoSwitch(config, nm, hc, pm)


class TestAutoSwitch:
    def test_switches_to_optimal_when_better(self, asw):
        """最优节点延迟低于当前、且不是当前节点 → 切到最优（hysteresis=0）。"""
        asw.health_checker.batch_check_real.return_value = [
            {'id': 'a', 'ok': True, 'latency_ms': 500, 'name': 'A'},
            {'id': 'b', 'ok': True, 'latency_ms': 300, 'name': 'B'},
        ]
        asw._check_and_switch()
        asw.proxy_manager.start.assert_called_once_with('b')
        asw.node_manager.set_default_node.assert_called_once_with('b')

    def test_no_switch_when_current_is_best(self, asw):
        """当前节点就是延迟最低的可用节点 → 不切换。"""
        asw.health_checker.batch_check_real.return_value = [
            {'id': 'a', 'ok': True, 'latency_ms': 300, 'name': 'A'},
            {'id': 'b', 'ok': True, 'latency_ms': 500, 'name': 'B'},
        ]
        asw._check_and_switch()
        asw.proxy_manager.start.assert_not_called()

    def test_hysteresis_prevents_jitter(self, asw):
        """hysteresis=0.2：最优仅略优于当前（差距 <20%）→ 不切，防抖。"""
        asw.config.load_config.return_value = {
            'auto_switch': {'enabled': True, 'hysteresis': 0.2},
            'subscriptions': {},
        }
        asw.health_checker.batch_check_real.return_value = [
            {'id': 'a', 'ok': True, 'latency_ms': 500, 'name': 'A'},  # 当前
            {'id': 'b', 'ok': True, 'latency_ms': 450, 'name': 'B'},  # 仅好 10% < 20%
        ]
        asw._check_and_switch()
        asw.proxy_manager.start.assert_not_called()

    def test_switches_when_current_unreachable(self, asw):
        """当前节点流量不通 → 无视 hysteresis 切到最优。"""
        asw.health_checker.batch_check_real.return_value = [
            {'id': 'a', 'ok': False, 'latency_ms': None, 'name': 'A'},
            {'id': 'b', 'ok': True, 'latency_ms': 300, 'name': 'B'},
        ]
        asw._check_and_switch()
        asw.proxy_manager.start.assert_called_once_with('b')

    def test_triggers_subscription_refresh_when_all_dead(self, asw):
        """所有节点流量都不通 → 触发订阅刷新；刷新无效时不切换。"""
        asw.health_checker.batch_check_real.return_value = [
            {'id': 'a', 'ok': False, 'latency_ms': None, 'name': 'A'},
            {'id': 'b', 'ok': False, 'latency_ms': None, 'name': 'B'},
        ]
        asw._try_refresh_subscription = MagicMock(return_value=False)
        asw._check_and_switch()
        asw._try_refresh_subscription.assert_called_once()
        asw.proxy_manager.start.assert_not_called()

    def test_switches_after_successful_refresh(self, asw):
        """刷新订阅使某节点恢复可用 → 重测后切到它。"""
        asw.health_checker.batch_check_real.side_effect = [
            [{'id': 'a', 'ok': False, 'latency_ms': None, 'name': 'A'},
             {'id': 'b', 'ok': False, 'latency_ms': None, 'name': 'B'}],
            [{'id': 'a', 'ok': False, 'latency_ms': None, 'name': 'A'},
             {'id': 'b', 'ok': True, 'latency_ms': 120, 'name': 'B'}],
        ]
        asw._try_refresh_subscription = MagicMock(return_value=True)
        asw._check_and_switch()
        asw.proxy_manager.start.assert_called_once_with('b')

    def test_subscription_refresh_respects_cooldown(self, asw):
        """cooldown 内不重复刷新；无订阅源时记录时间戳并返回 False。"""
        cfg = {'auto_update_subscription': True, 'subscription_refresh_cooldown': 3600}
        assert asw._try_refresh_subscription(cfg) is False
        before = asw._last_sub_refresh
        import time
        time.sleep(0.01)
        assert asw._try_refresh_subscription(cfg) is False
        assert asw._last_sub_refresh >= before
