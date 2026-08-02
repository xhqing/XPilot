"""Auto switch and watchdog for proxy nodes based on health checks."""

import logging
import time
import threading

logger = logging.getLogger(__name__)


class AutoSwitch:
    """Automatically switch to better nodes based on health checks, with an
    independent watchdog that keeps the proxy process alive.

    The monitor loop runs both subsystems on their own intervals:
      * watchdog  -- restarts the proxy process if it dies (independent of
                     auto_switch.enabled, controlled by watchdog.enabled).
      * auto_switch -- switches to a better node based on latency.

    Either subsystem can be enabled/disabled independently via settings.json.
    The loop tick interval is the shorter of the two so each subsystem can
    run on its own schedule.
    """

    def __init__(self, config, node_manager, health_checker, proxy_manager):
        self.config = config
        self.node_manager = node_manager
        self.health_checker = health_checker
        self.proxy_manager = proxy_manager
        self._running = False
        self._thread = None
        # Subsystem enable flags and intervals (populated in start())
        self._watchdog_enabled = False
        self._auto_switch_enabled = False
        self._watchdog_interval = 30
        self._auto_switch_interval = 300
        self._tick_interval = 30
        self._last_watchdog = 0.0
        self._last_auto_switch = 0.0
        # 订阅自动刷新的退避时间戳：全节点失效时拉订阅，cooldown 内不重复。
        self._last_sub_refresh = 0.0

    def start(self) -> None:
        """Start monitoring (watchdog and/or auto-switch).

        The monitor starts as long as at least one subsystem is enabled.
        The watchdog is independent of auto_switch, so the proxy will be
        kept alive even when auto-switch is disabled.
        """
        if self._running:
            logger.warning('Monitor is already running')
            return

        settings = self.config.load_config('settings.json')
        auto_switch_cfg = settings.get('auto_switch', {})
        watchdog_cfg = settings.get('watchdog', {})

        self._auto_switch_enabled = auto_switch_cfg.get('enabled', False)
        # Watchdog defaults to enabled for safety
        self._watchdog_enabled = watchdog_cfg.get('enabled', True)

        if not self._auto_switch_enabled and not self._watchdog_enabled:
            logger.info('Both auto-switch and watchdog are disabled, skipping monitor')
            return

        self._auto_switch_interval = auto_switch_cfg.get('interval', 300)
        self._watchdog_interval = watchdog_cfg.get('interval', 30)

        # The loop tick is the shorter interval so each subsystem can still
        # honour its own (longer) schedule.
        intervals = []
        if self._watchdog_enabled:
            intervals.append(self._watchdog_interval)
        if self._auto_switch_enabled:
            intervals.append(self._auto_switch_interval)
        self._tick_interval = min(intervals) if intervals else 30

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(
            f'Monitor started (tick: {self._tick_interval}s, '
            f'watchdog: {self._watchdog_enabled}@{self._watchdog_interval}s, '
            f'auto-switch: {self._auto_switch_enabled}@{self._auto_switch_interval}s)'
        )

        # Eagerly run a watchdog check on the main thread so the proxy is
        # (re)started immediately on launch/launchd boot, without depending
        # on the background thread's first tick. Update _last_watchdog so the
        # loop does not re-trigger it right away.
        if self._watchdog_enabled:
            try:
                self._watchdog_check()
                self._last_watchdog = time.time()
            except Exception as e:
                logger.error(f'Initial watchdog check failed: {e}')

        # 同样立即跑一次 auto-switch 选优：启动后马上切到延迟最低的可用节点，
        # 不必等第一个 tick（默认 300s）才生效。
        if self._auto_switch_enabled:
            try:
                self._check_and_switch()
                self._last_auto_switch = time.time()
            except Exception as e:
                logger.error(f'Initial auto-switch check failed: {e}')

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info('Monitor stopped')

    def _monitor_loop(self) -> None:
        """Main monitoring loop running watchdog and auto-switch on their own intervals."""
        while self._running:
            now = time.time()
            try:
                # Watchdog tick
                if self._watchdog_enabled and (now - self._last_watchdog) >= self._watchdog_interval:
                    self._watchdog_check()
                    self._last_watchdog = time.time()

                # Auto-switch tick
                if self._auto_switch_enabled and (now - self._last_auto_switch) >= self._auto_switch_interval:
                    self._check_and_switch()
                    self._last_auto_switch = time.time()
            except Exception as e:
                logger.error(f'Monitor loop error: {e}')
            time.sleep(self._tick_interval)

    def _watchdog_check(self) -> None:
        """Watchdog: ensure the proxy process is alive, restart with retries if not."""
        if self.proxy_manager.is_running():
            return

        logger.warning('Proxy process is not running, watchdog restarting...')

        settings = self.config.load_config('settings.json')
        watchdog_cfg = settings.get('watchdog', {})
        max_retries = watchdog_cfg.get('max_retries', 3)
        retry_delay = watchdog_cfg.get('retry_delay', 5)

        node_id = self.node_manager.get_default_node()
        if not node_id:
            logger.error('Watchdog: no default node available for restart')
            return

        for attempt in range(1, max_retries + 1):
            try:
                self.proxy_manager.start(node_id)
                logger.info(f'Watchdog restarted proxy (node: {node_id}, attempt: {attempt}/{max_retries})')
                return
            except Exception as e:
                logger.error(f'Watchdog restart attempt {attempt}/{max_retries} failed: {e}')
                if attempt < max_retries:
                    time.sleep(retry_delay)
        logger.error(f'Watchdog exhausted {max_retries} retries; will try again next interval')

    def _check_and_switch(self) -> None:
        """在所有真实流量可用的节点中选延迟最优的，最优不是当前节点就切换。

        每 interval 秒检测全部 active 节点的真实流量，在「能用的」节点里取延迟
        最低者为最优；最优不是当前节点即切换。hysteresis（迟滞比例，默认 0）是
        防抖手段：大于 0 时，仅当最优延迟比当前低 hysteresis 比例以上才切，避免
        节点延迟接近时来回抖动；当前节点不可用时无视迟滞直接切到最优。全部节点
        都不通时刷新订阅后重测。
        """
        settings = self.config.load_config('settings.json')
        auto_switch_cfg = settings.get('auto_switch', {})
        hysteresis = auto_switch_cfg.get('hysteresis', 0.0)

        nodes = self.node_manager.list_nodes()
        active_nodes = [n for n in nodes if n.get('status') == 'active']
        if not active_nodes:
            return
        node_ids = [n['id'] for n in active_nodes]

        self.health_checker.set_node_manager(self.node_manager)
        results = self.health_checker.batch_check_real(node_ids)

        usable = self._sort_usable([r for r in results if r.get('ok')])
        self._log_usable_status(results, usable)

        # 全部节点真实流量都不通：先尝试刷新订阅，再重测一次。
        if not usable:
            logger.warning('所有节点真实流量均不通，尝试刷新订阅')
            if self._try_refresh_subscription(auto_switch_cfg):
                # 订阅可能增删了节点，重新拉一次活跃列表。
                nodes = self.node_manager.list_nodes()
                node_ids = [n['id'] for n in nodes if n.get('status') == 'active']
                if node_ids:
                    results = self.health_checker.batch_check_real(node_ids)
                    usable = self._sort_usable([r for r in results if r.get('ok')])
            if not usable:
                logger.error('刷新订阅后仍无可用节点')
                return

        best = usable[0]
        current_node = self.node_manager.get_default_node()
        if best['id'] == current_node:
            return  # 当前已是最优节点

        # 最优不是当前节点。hysteresis（默认 0 = 最优不是当前就切）大于 0 时，
        # 仅当最优延迟比当前低 hysteresis 比例以上才切，防止延迟接近时来回抖动；
        # 当前节点不可用时无视迟滞直接切到最优。
        current_result = (next((r for r in results if r['id'] == current_node), None)
                          if current_node else None)
        if not current_result or not current_result.get('ok'):
            reason = f'当前节点 {current_node} 流量不通，切到最优'
        else:
            cur_lat = current_result.get('latency_ms') or float('inf')
            best_lat = best.get('latency_ms') or float('inf')
            if (hysteresis > 0 and cur_lat != float('inf')
                    and best_lat >= cur_lat * (1 - hysteresis)):
                return  # 最优在迟滞范围内，保持当前避免抖动
            reason = (f'最优 {best["name"]} {best_lat}ms 快于当前 {current_node} {cur_lat}ms')
        logger.info(f'Auto switching from {current_node} to {best["id"]}：'
                    f'{reason}；目标延迟 {best.get("latency_ms")}ms')
        self._switch_to(best['id'])

    @staticmethod
    def _sort_usable(usable: list) -> list:
        """真实流量可用的节点按延迟升序排列，延迟缺失的排后面。"""
        return sorted(usable, key=lambda r: r.get('latency_ms') or float('inf'))

    @staticmethod
    def _log_usable_status(results: list, usable: list) -> None:
        """可用节点数不足时记录日志（写入 log_file，便于排查）。

        订阅套餐应有 N 个节点，若实际可用的少于总数，列出不通的节点名与
        可能原因，让用户知道「为什么可用节点少了」。
        """
        if len(usable) < len(results):
            dead = [str(r.get('name') or r.get('id')) for r in results if not r.get('ok')]
            logger.warning(
                f'[可用节点不足] {len(usable)}/{len(results)} 个节点流量可用；'
                f'不通：{", ".join(dead)}。可能原因：密钥/IP 过期、服务器故障、'
                f'或协议被针对；可手动 `xpilot subscription update` 刷新。')

    def _switch_to(self, node_id: str) -> None:
        """切换到指定节点：stop → start → 设默认节点。manual_switch 复用。"""
        self.proxy_manager.stop()
        self.proxy_manager.start(node_id)
        self.node_manager.set_default_node(node_id)

    def _try_refresh_subscription(self, auto_switch_cfg: dict) -> bool:
        """所有节点流量都不通时，尝试拉订阅按名字刷新已有节点。

        带 cooldown 退避（默认 3600s）避免订阅端临时故障时高频请求；需要
        settings.subscriptions 已配置。返回是否真正执行了刷新（不代表一定
        引入了可用节点）。
        """
        if not auto_switch_cfg.get('auto_update_subscription', True):
            return False
        cooldown = auto_switch_cfg.get('subscription_refresh_cooldown', 3600)
        now = time.time()
        if now - self._last_sub_refresh < cooldown:
            logger.debug(f'订阅刷新在 cooldown（{cooldown}s）内，跳过')
            return False

        subs = self.config.load_config('settings.json').get('subscriptions', {})
        if not subs:
            logger.warning('未配置订阅源（settings.subscriptions 为空），无法自动刷新；'
                           '请用 `xpilot subscription add <url> <name>` 添加')
            self._last_sub_refresh = now
            return False

        self._last_sub_refresh = now
        refreshed_total = 0
        for name, url in subs.items():
            try:
                count = self.node_manager.import_from_subscription(url, update_existing=True, source=name)
                logger.info(f'自动刷新订阅 {name}：处理 {count} 个节点（按名字刷新连接字段）')
                refreshed_total += count
            except Exception as e:
                logger.error(f'自动刷新订阅 {name} 失败: {e}')
        return refreshed_total > 0

    def manual_switch(self, node_id: str) -> bool:
        """Manually trigger a switch to a specific node."""
        if not self.proxy_manager.is_running():
            logger.warning('Proxy is not running')
            return False

        self._switch_to(node_id)
        logger.info(f'Manually switched to {node_id}')
        return True
