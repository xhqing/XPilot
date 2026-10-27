"""Auto switch and watchdog for proxy nodes based on health checks."""

import logging
import time
import threading

logger = logging.getLogger(__name__)


def _coerce_positive_int(value, default: int) -> int:
    """把配置值转成正整数；非法（≤0 / 非数字 / None）时回退默认值。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _coerce_int(value, default: int) -> int:
    """把配置值转成整数；非法时回退默认值（允许 0 与负数，用于「禁用」语义）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        # 选优策略（populated in start()）：
        #   latency  -- 纯延迟最低（现状）
        #   hybrid   -- 排除延迟超限节点后按带宽选（推荐，带宽主导、延迟兜底）
        #   speed    -- 纯带宽最优
        self._strategy = 'latency'
        # hybrid：经节点延迟超过该值(ms)的节点排除。
        self._latency_threshold_ms = 1500
        # 带宽测速缓存有效期（秒）与测速下载大小（字节）。
        self._speed_cache_ttl = 1800
        self._speed_test_size = 2000000
        # 带宽缓存：node_id -> {speed_mbps, ts}；全组到 ttl 后整组重测。
        self._speed_cache = {}
        self._last_speed_test = 0.0
        # 定时订阅同步（Issue #6）：subscription.auto_update / update_interval。
        # 与 auto_switch.auto_update_subscription（全挂应急刷新，带 cooldown）
        # 是两条互补的路径：这条管「例行同步」，那条管「出事补救」。
        self._subscription_sync_enabled = False
        self._subscription_sync_interval = 3600
        self._last_sub_sync = 0.0
        # 陈旧实例自动恢复（Issue #5）：运行配置与默认节点不一致、或连续多轮
        # 全部节点流量不通时，自动重启一次（带窗口限次与最小间隔，防重启死循环）。
        self._auto_recover_enabled = False
        self._recover_max_restarts = 3
        self._recover_window = 1800
        self._recover_min_interval = 60
        self._dead_restart_ticks = 2
        self._recover_times = []
        self._dead_ticks = 0

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
        auto_switch_cfg = settings.get('auto_switch', {}) or {}
        watchdog_cfg = settings.get('watchdog', {}) or {}
        subscription_cfg = settings.get('subscription', {}) or {}
        recover_cfg = settings.get('auto_recover', {}) or {}

        self._auto_switch_enabled = auto_switch_cfg.get('enabled', False)
        # Watchdog defaults to enabled for safety
        self._watchdog_enabled = watchdog_cfg.get('enabled', True)
        # 定时订阅同步（Issue #6）：曾经是死配置（生成了默认值却没人读），
        # 现在真正生效——服务端轮换密钥 / IP 后，节点列表在下一个周期内跟上。
        self._subscription_sync_enabled = bool(subscription_cfg.get('auto_update', True))
        self._subscription_sync_interval = _coerce_positive_int(
            subscription_cfg.get('update_interval'), 3600)
        # 陈旧实例自动恢复（Issue #5）。旧 settings.json 没有这个块，缺块按默认开。
        self._auto_recover_enabled = bool(recover_cfg.get('enabled', True))
        self._recover_max_restarts = _coerce_positive_int(recover_cfg.get('max_restarts'), 3)
        self._recover_window = _coerce_positive_int(recover_cfg.get('restart_window'), 1800)
        self._recover_min_interval = _coerce_int(recover_cfg.get('min_interval'), 60)
        # <=0 表示禁用「全挂重启」这条触发路径（配置陈旧仍可触发）。
        self._dead_restart_ticks = _coerce_int(recover_cfg.get('dead_restart_ticks'), 2)

        if (not self._auto_switch_enabled and not self._watchdog_enabled
                and not self._subscription_sync_enabled
                and not self._auto_recover_enabled):
            logger.info('All subsystems are disabled, skipping monitor')
            return

        self._auto_switch_interval = auto_switch_cfg.get('interval', 300)
        self._watchdog_interval = watchdog_cfg.get('interval', 30)

        strategy = auto_switch_cfg.get('strategy', 'latency')
        if strategy not in ('latency', 'hybrid', 'speed'):
            logger.warning(f'未知选优策略 {strategy!r}，回退到 latency')
            strategy = 'latency'
        self._strategy = strategy
        self._latency_threshold_ms = auto_switch_cfg.get('latency_threshold_ms', 1500)
        self._speed_cache_ttl = auto_switch_cfg.get('speed_cache_ttl', 1800)
        self._speed_test_size = auto_switch_cfg.get('speed_test_size', 2000000)

        # The loop tick is the shorter interval so each subsystem can still
        # honour its own (longer) schedule. 自动恢复的检查锁在 30 秒粒度内
        # （只读一次运行配置，开销很小），所以 tick 永不超过 30 秒。
        intervals = []
        if self._watchdog_enabled:
            intervals.append(self._watchdog_interval)
        if self._auto_switch_enabled:
            intervals.append(self._auto_switch_interval)
        if self._subscription_sync_enabled:
            intervals.append(self._subscription_sync_interval)
        if self._auto_recover_enabled:
            intervals.append(30)
        self._tick_interval = min(min(intervals), 30) if intervals else 30

        # 定时订阅同步的起点定在 start 时刻：首次拉取发生在一个 interval 之后，
        # 不让 `xpilot start` 因为等订阅服务器而变慢（手动 `subscription update`
        # 随时可用）。
        self._last_sub_sync = time.time()

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

                # 陈旧实例自愈（Issue #5）：每个 tick 都查——运行配置与当前默认
                # 节点不一致时，重启一次就能修好（正是 2026-10 静默断网那个场景）。
                if self._auto_recover_enabled:
                    self._check_stale_runtime()

                # Auto-switch tick
                if self._auto_switch_enabled and (now - self._last_auto_switch) >= self._auto_switch_interval:
                    self._check_and_switch()
                    self._last_auto_switch = time.time()

                # 定时订阅同步（Issue #6）：间隔由方法内部判定，每个 tick 问一句。
                if self._subscription_sync_enabled:
                    self._subscription_sync_tick()
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
                # 连续多轮全挂才说明本地状态真的坏了（单轮更可能是订阅端抖动）。
                self._track_dead_nodes(0)
                return

        # 有可用节点即视为正常：把「连续全挂」计数归零。
        self._track_dead_nodes(len(usable))

        best = self._pick_best(usable)
        current_node = self.node_manager.get_default_node()
        if best['id'] == current_node:
            return  # 当前已是最优节点

        # 最优不是当前节点。hysteresis（默认 0 = 最优不是当前就切）大于 0 时，
        # 仅当最优比当前好 hysteresis 比例以上才切，防止接近时来回抖动；
        # 当前节点不可用时无视迟滞直接切到最优。
        # latency 策略按延迟比例；hybrid/speed 策略按带宽比例（当前带宽 >=
        # 最优带宽的 (1-hysteresis) 时不切）。
        current_result = (next((r for r in results if r['id'] == current_node), None)
                          if current_node else None)
        if not current_result or not current_result.get('ok'):
            reason = f'当前节点 {current_node} 流量不通，切到最优'
        else:
            cur_lat = current_result.get('latency_ms') or float('inf')
            best_lat = best.get('latency_ms') or float('inf')
            if self._strategy == 'latency':
                if (hysteresis > 0 and cur_lat != float('inf')
                        and best_lat >= cur_lat * (1 - hysteresis)):
                    return  # 最优在迟滞范围内，保持当前避免抖动
                reason = (f'最优 {best["name"]} {best_lat}ms 快于当前 '
                          f'{current_node} {cur_lat}ms')
            else:
                cur_speed = self._speed_cache.get(current_node, {}).get('speed_mbps')
                best_speed = self._speed_cache.get(best['id'], {}).get('speed_mbps')
                if (hysteresis > 0 and cur_speed is not None and best_speed
                        and cur_speed >= best_speed * (1 - hysteresis)):
                    return  # 当前带宽在迟滞范围内，保持当前避免抖动
                reason = (f'最优 {best["name"]} {best_lat}ms/{self._fmt_speed(best_speed)} '
                          f'优于当前 {current_node} {cur_lat}ms/{self._fmt_speed(cur_speed)}')
        logger.info(f'Auto switching from {current_node} to {best["id"]}：'
                    f'{reason}；目标延迟 {best.get("latency_ms")}ms')
        self._switch_to(best['id'])

    @staticmethod
    def _sort_usable(usable: list) -> list:
        """真实流量可用的节点按延迟升序排列，延迟缺失的排后面。"""
        return sorted(usable, key=lambda r: r.get('latency_ms') or float('inf'))

    def _pick_best(self, usable: list) -> dict:
        """按 strategy 从真实流量可用的节点里选最优。

        latency：延迟最低（_sort_usable 已按延迟排序，取第一个）。
        hybrid：先排除经节点延迟超过 latency_threshold_ms 的节点——延迟差到
            视频拖动都会崩溃的，带宽再高也无意义；全部超限时回退全部
            （宁可慢、不可断），再按带宽降序选。
        speed：纯按带宽降序选（延迟不参与）。
        """
        if self._strategy == 'latency':
            return self._sort_usable(usable)[0]

        if self._strategy == 'hybrid':
            threshold = self._latency_threshold_ms
            filtered = [r for r in usable
                        if (r.get('latency_ms') or float('inf')) <= threshold]
            if not filtered:
                logger.warning(
                    f'hybrid：全部可用节点延迟均超过 {threshold}ms，回退按延迟选')
                filtered = usable
            usable = filtered

        # hybrid / speed 需要带宽数据：缓存过期则整组重测一次。
        node_ids = [r['id'] for r in usable]
        self._refresh_speed_cache(node_ids)
        return self._sort_by_speed(usable)[0]

    def _refresh_speed_cache(self, node_ids: list) -> None:
        """带宽缓存过期时并发测速刷新（整组），失败不阻塞选优。

        带宽抖动大，按 speed_cache_ttl（默认 30 分钟）周期整组重测即可；
        单轮测速失败记 None 排最后，下一轮 ttl 到期前不重复烧流量。
        """
        now = time.time()
        if now - self._last_speed_test < self._speed_cache_ttl:
            return
        self._last_speed_test = now
        try:
            results = self.health_checker.batch_check_speed(
                node_ids, size_bytes=self._speed_test_size, timeout=30)
            for r in results:
                self._speed_cache[r['id']] = {
                    'speed_mbps': r.get('speed_mbps'),
                    'ts': now,
                }
            speeds = ', '.join(
                f'{r["id"]}={self._fmt_speed(r.get("speed_mbps"))}'
                for r in results)
            logger.info(f'测速刷新完成：{speeds}')
        except Exception as e:
            logger.error(f'测速刷新失败: {e}')

    def _sort_by_speed(self, usable: list) -> list:
        """按带宽降序排：测速缺失/失败的排最后，同带宽按延迟升序兜底。"""
        def key(r):
            cache = self._speed_cache.get(r['id'])
            speed = cache.get('speed_mbps') if cache else None
            return (speed is None, -(speed or 0),
                    r.get('latency_ms') or float('inf'))
        return sorted(usable, key=key)

    @staticmethod
    def _fmt_speed(speed) -> str:
        """带宽数值格式化：None -> 'N/A'，否则 'X.X Mbps'。"""
        if speed is None:
            return 'N/A'
        return f'{speed:.1f}Mbps'

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
        return self._refresh_all_subscriptions() > 0

    def _refresh_all_subscriptions(self) -> int:
        """拉取全部订阅源并按名字刷新已有节点，返回处理节点总数。

        定时同步与「全挂应急刷新」共用这一段：单个源失败只记 error、继续下
        一个——一个机场挂了不该拖累其它订阅。无订阅源或全部失败时返回 0。
        """
        subs = self.config.load_config('settings.json').get('subscriptions', {}) or {}
        total = 0
        for name, url in subs.items():
            try:
                count = self.node_manager.import_from_subscription(
                    url, update_existing=True, source=name)
                logger.info(f'刷新订阅 {name}：处理 {count} 个节点（按名字刷新连接字段）')
                total += count
            except Exception as e:
                logger.error(f'刷新订阅 {name} 失败: {e}')
        return total

    def _subscription_sync_tick(self) -> bool:
        """按 ``subscription.update_interval`` 定时刷新订阅（Issue #6）。

        与 :meth:`_try_refresh_subscription` 的区别：那条是「所有节点都不通」
        时的应急补救（带独立 cooldown），这条是例行同步——服务端轮换密钥 / IP
        后，本地节点列表在一个周期内自动跟上，不用等出事。

        返回本轮是否真的拉到了节点。时间戳**先推进再拉**：订阅端故障时不该把
        重试节奏收紧成每个 tick 一次。
        """
        if not self._subscription_sync_enabled:
            return False

        now = time.time()
        if now - self._last_sub_sync < self._subscription_sync_interval:
            return False

        self._last_sub_sync = now
        count = self._refresh_all_subscriptions()
        if count > 0:
            logger.info(f'定时同步订阅完成：本轮处理 {count} 个节点')
            return True
        return False

    def _recover_restart(self, reason: str) -> bool:
        """执行一次受限的自动重启（Issue #5），返回是否真的重启成功。

        三道闸门保证不会重启死循环：

        * 窗口内次数上限（``max_restarts`` / ``restart_window``）：连续重启仍
          修不好时（例如根本没有可达节点），到量后**退回只告警**；
        * 两次重启最小间隔（``min_interval``）：防抖，不因相邻 tick 反复启停；
        * 代理没在跑时不动手——拉起进程是 watchdog 的职责，不归这里。

        重启失败同样计入配额：修不好的状态不该每轮都重新启停一次。
        """
        if not self._auto_recover_enabled:
            return False
        if not self.proxy_manager.is_running():
            return False

        now = time.time()
        self._recover_times = [t for t in self._recover_times
                               if now - t < self._recover_window]

        if len(self._recover_times) >= self._recover_max_restarts:
            logger.warning(
                f'自动恢复已达上限（{self._recover_max_restarts} 次 / '
                f'{self._recover_window}s），仅告警不再重启：{reason}')
            return False

        if (self._recover_times
                and now - self._recover_times[-1] < self._recover_min_interval):
            logger.debug(
                f'距上次自动重启不足 {self._recover_min_interval}s，跳过：{reason}')
            return False

        ok = True
        try:
            self.proxy_manager.restart()
        except Exception as e:
            ok = False
            logger.error(f'自动恢复重启失败：{reason}；{e}')
        self._recover_times.append(now)

        if ok:
            logger.info(
                f'自动恢复：{reason} → 已重启代理'
                f'（本窗口第 {len(self._recover_times)}/{self._recover_max_restarts} 次）')
        return ok

    def _check_stale_runtime(self) -> bool:
        """运行实例的配置与当前默认节点不一致时自动重启（Issue #5 第 1 条）。

        只处理 ``running_config_matches_current()`` **明确返回 False** 的情况：
        ``True`` 是一致、``None`` 是无法判定（运行配置缺失 / 无默认节点），
        后者重启也解决不了问题，只会白抖一次。
        """
        if not self.proxy_manager.is_running():
            return False

        matched, detail = self.proxy_manager.running_config_matches_current()
        if matched is not False:
            return False

        return self._recover_restart(f'运行实例配置陈旧（{detail}）')

    def _track_dead_nodes(self, usable_count: int) -> bool:
        """记录「全部节点真实流量不通」的连续轮次，达阈值触发恢复重启。

        单轮全挂很可能是订阅端抖动；连续多轮才说明本地状态真的坏了（2026-10
        事故：节点 IP 漂移，跑着的 xray 还咬内存里的旧地址）。恢复即归零。
        ``dead_restart_ticks <= 0`` 时禁用这条路径（不累加计数）。
        """
        if usable_count > 0:
            self._dead_ticks = 0
            return False

        if self._dead_restart_ticks <= 0:
            return False

        self._dead_ticks += 1
        if self._dead_ticks < self._dead_restart_ticks:
            return False

        rounds = self._dead_ticks
        self._dead_ticks = 0
        return self._recover_restart(f'连续 {rounds} 轮全部节点真实流量不通')

    def manual_switch(self, node_id: str) -> bool:
        """Manually trigger a switch to a specific node."""
        if not self.proxy_manager.is_running():
            logger.warning('Proxy is not running')
            return False

        self._switch_to(node_id)
        logger.info(f'Manually switched to {node_id}')
        return True
