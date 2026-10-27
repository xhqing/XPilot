"""Configuration management for xpilot."""

import os
import json
import stat
import logging

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Configuration related errors."""
    pass


class ValidationError(ConfigError):
    """Configuration validation error."""
    pass


class Config:
    """Configuration manager for xpilot."""

    # 旧版本把程序日志固定写在临时目录里，文件名固定，便于窄迁移识别。
    LEGACY_LOG_FILE = '/tmp/xpilot.log'

    # 0.6.4 及更早的 settings.json 生成过这一组 ``subscription`` 默认值，但那时
    # 没有任何代码读取它（Issue #6 的「dead config」）——所以这里的 ``false``
    # 不是用户的选择，只是无效默认值的残留。
    LEGACY_SUBSCRIPTION_DEFAULTS = {'auto_update': False, 'update_interval': 3600}

    # 新版默认块（`get_default_settings_config` 与旧配置升级共用这一份定义）。
    DEFAULT_SUBSCRIPTION = {
        # 定时同步订阅（Issue #6）：曾经是死配置（生成了却没人读），现在由 monitor
        # 按 update_interval 真正生效。关闭后仍可用手动 `xpilot subscription update`。
        'auto_update': True,
        'update_interval': 3600,
    }

    DEFAULT_AUTO_RECOVER = {
        # 陈旧运行实例自动恢复（Issue #5）：运行配置与当前默认节点不一致、或连续
        # dead_restart_ticks 轮全部节点真实流量不通时，自动重启一次（带窗口限次与
        # 最小间隔，防重启死循环）。
        'enabled': True,
        # 一个 restart_window 秒窗口内最多自动重启几次；到量后退回只告警。
        'max_restarts': 3,
        'restart_window': 1800,
        # 两次自动重启的最小间隔（秒），防抖。
        'min_interval': 60,
        # 连续多少轮「全部节点真实流量不通」才触发重启；<=0 禁用这条路径
        # （配置陈旧仍可触发）。单轮全挂多是订阅端抖动，不该急着重启。
        'dead_restart_ticks': 2,
    }

    def __init__(self, config_dir: str = None):
        from .utils import get_config_dir
        self.config_dir = config_dir or os.environ.get('PROXY_TOOLKIT_CONFIG_DIR') or get_config_dir()
        self._settings_file = os.environ.get('PROXY_TOOLKIT_SETTINGS_FILE', 'settings.json')
        os.makedirs(self.config_dir, exist_ok=True)
        self._migrate_legacy_log_file()
        self._migrate_dead_subscription_flag()

    def get_default_log_file(self) -> str:
        """程序日志的默认路径（跟着配置目录走，不再落临时目录）。"""
        return os.path.join(self.config_dir, 'logs', 'xpilot.log')

    def _migrate_legacy_log_file(self) -> None:
        """把旧默认日志路径改成新默认值（只动恰好等于旧默认值的配置）。

        为什么：0.6.x 生成的 settings.json 把 ``log_file`` 写成
        ``/tmp/xpilot.log``，读时不会自动跟随新的默认值——不迁移的话，
        老用户升级后日志仍然随时随手清空的 /tmp 消失，排障时无据可查。兼容口径：
        只识别旧默认值本身，自定义路径（如 ``/var/log/xpilot.log``）一律
        不动；settings.json 不存在时不凭空创建。
        """
        path = self._get_file_path('settings.json')
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r') as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(settings, dict) or settings.get('log_file') != self.LEGACY_LOG_FILE:
            return
        settings['log_file'] = self.get_default_log_file()
        try:
            self.save_config('settings.json', settings)
            logger.info(f'已将 log_file 从 {self.LEGACY_LOG_FILE} 迁移到 {settings["log_file"]}')
        except (OSError, ConfigError) as e:
            logger.warning(f'log_file 迁移失败（保持原值）: {e}')

    def _migrate_dead_subscription_flag(self) -> None:
        """旧版 settings.json 升级：补 ``auto_recover`` 块 + 修正从未生效过的订阅开关。

        两件事一起做，判据是「文件里有没有 ``auto_recover`` 块」：

        * **旧文件**（0.6.4 及更早生成）必然没有这个块——它是本次升级新增的。
          此时补上默认块，并顺手把 ``subscription.auto_update`` 那个 ``false``
          改成 ``true``：那个值写于这个键还没人读的年代，是无效默认值残留、
          不表达用户意图（不迁的话所有存量安装升级后定时同步依旧是关的）。
        * **新文件**（``xpilot init`` 生成、或已经迁移过）一定有这个块——此时
          一个字节都不动。用户把 ``auto_update`` 改成 ``false`` 就是真的关掉了。

        为什么不能只看 subscription 块的内容：新默认的 ``update_interval`` 也是
        3600，而用户关闭定时同步的自然写法恰恰是「``auto_update: false`` + 间隔
        保持 3600」——那与旧默认值逐字相同。只看块内容的话，用户每次启动都会被
        迁回 ``true``，等于关不掉。
        """
        path = self._get_file_path('settings.json')
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r') as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(settings, dict):
            return
        # 已有 auto_recover 块 = 新版配置（或已迁移过），不再碰用户的设置。
        if 'auto_recover' in settings:
            return

        settings['auto_recover'] = dict(self.DEFAULT_AUTO_RECOVER)
        upgraded_flag = self._is_legacy_subscription_block(settings.get('subscription'))
        if upgraded_flag:
            settings['subscription']['auto_update'] = True

        try:
            self.save_config('settings.json', settings)
        except OSError as e:
            logger.warning(f'旧配置升级失败: {e}')
            return
        logger.info(
            'settings.json 已升级：补 auto_recover 默认块'
            + ('，并将 subscription.auto_update 从旧版无效默认值 false 改为 true'
               if upgraded_flag else ''))

    @classmethod
    def _is_legacy_subscription_block(cls, block) -> bool:
        """``subscription`` 块是否逐字等于旧版本生成的无效默认值。

        类型比较用 ``is False`` 与 ``isinstance(..., int)``（排除 bool）而不是
        ``==``：Python 里 ``0 == False``、``3600.0 == 3600``，用 ``==`` 会把
        明显不是旧默认值的写法（手写成 ``auto_update: 0`` 之类）也当成旧默认值。
        """
        if not isinstance(block, dict):
            return False
        if set(block) != set(cls.LEGACY_SUBSCRIPTION_DEFAULTS):
            return False
        if block['auto_update'] is not False:
            return False
        interval = block['update_interval']
        return (isinstance(interval, int) and not isinstance(interval, bool)
                and interval == 3600)

    def load_config(self, file_name: str) -> dict:
        """Load a configuration file."""
        file_path = self._get_file_path(file_name)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'Configuration file not found: {file_name}')
        with open(file_path, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError as e:
                raise ConfigError(f'Invalid JSON in {file_name}: {e}')

    def save_config(self, file_name: str, data: dict) -> None:
        """Save a configuration file."""
        file_path = self._get_file_path(file_name)
        # Validate path to prevent directory traversal
        abs_path = os.path.abspath(file_path)
        abs_config = os.path.abspath(self.config_dir)
        if not abs_path.startswith(abs_config):
            raise ConfigError('Invalid file path')

        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Set file permissions to owner-only read/write
        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)

    def validate_config(self, schema: dict, data: dict) -> bool:
        """Validate configuration against schema."""
        for field, rules in schema.items():
            required = rules.get('required', False)
            field_type = rules.get('type', None)
            enum_values = rules.get('enum', None)

            if required and field not in data:
                raise ValidationError(f'Missing required field: {field}')

            if field in data:
                value = data[field]
                if field_type and not isinstance(value, field_type):
                    raise ValidationError(
                        f'Invalid type for {field}: expected {field_type.__name__}, '
                        f'got {type(value).__name__}')
                if enum_values and value not in enum_values:
                    raise ValidationError(
                        f'Invalid value for {field}: must be one of {enum_values}')
        return True

    def get_default_nodes_config(self) -> dict:
        """Get default nodes configuration."""
        return {
            'default_node': None,
            'groups': {
                'default': '默认分组'
            },
            'nodes': {}
        }

    def get_default_routing_config(self) -> dict:
        """Get default routing configuration.

        proxy_all=True 为全局代理模式（默认）：除 direct_list / block_list
        明确列出的流量外，其余全部走代理，未匹配的流量显式兜底到代理，
        避免「以为走了代理、实际直连」；proxy_all=False 为白名单分流模式，
        只有 proxy_list 列出的走代理，其余全部直连。
        """
        return {
            'proxy_all': True,
            'proxy_list': [
                'domain:google.com',
                'domain:github.com',
                'domain:x.com',
                'domain:z.ai',
                'domain:youtube.com'
            ],
            'direct_list': [
                'geoip:private'
            ],
            'block_list': [],
            'domain_rules': [],
            'rules': []
        }

    def get_default_settings_config(self) -> dict:
        """Get default settings configuration."""
        return {
            'xray_bin': '/usr/local/bin/xray',
            'socks_port': 1080,
            'http_port': 1087,
            'log_level': 'warning',
            'log_file': self.get_default_log_file(),
            'auto_switch': {
                'enabled': True,
                'interval': 300,
                # 选优策略：latency=纯延迟最低（默认，保守）/ hybrid=带宽主导、
                # 延迟兜底（排除延迟超限节点后按带宽选，兼顾视频流畅与交互体验）/
                # speed=纯带宽最优（延迟完全不参与，可能选到高带宽高延迟节点）。
                'strategy': 'latency',
                # hybrid 模式：经节点延迟超过该值（毫秒）的节点直接排除——
                # 超过该量级的延迟连视频拖动都会崩溃，带宽再高也无意义。
                'latency_threshold_ms': 1500,
                # 带宽测速缓存有效期（秒）：hybrid/speed 模式下全节点测速的间隔。
                # 带宽抖动大（深夜限速 / 高峰拥堵），无需每轮（interval）都测，
                # 避免频繁下载烧套餐流量。
                'speed_cache_ttl': 3600,
                # 带宽测速下载大小（字节）：5MB 才能在快节点上越过 TCP 慢启动
                # 收敛到真实带宽（2MB 对 27Mbps 节点只有 0.6s 窗口、严重低估），
                # 也比 CLI 测速默认的 10MB 省流量。
                'speed_test_size': 5000000,
                # 切换迟滞比例：最优节点延迟需比当前低该比例以上才切换，防抖；
                # 0 = 最优不是当前就切（默认，最积极选优）。
                # hybrid/speed 模式下语义变为带宽比例：当前带宽 >= 最优带宽的
                # (1-hysteresis) 时不切。
                'hysteresis': 0,
                # 所有节点真实流量都不通时，自动拉订阅按名字刷新已有节点的连接字段。
                'auto_update_subscription': True,
                # 订阅自动刷新的退避秒数，避免订阅端临时故障时高频请求。
                'subscription_refresh_cooldown': 3600
            },
            'watchdog': {
                'enabled': True,
                'interval': 30,
                'max_retries': 3,
                'retry_delay': 5
            },
            # 订阅与自动恢复两块共用类上的常量定义（迁移逻辑也用同一份，避免两处走离）。
            'subscription': dict(self.DEFAULT_SUBSCRIPTION),
            'auto_recover': dict(self.DEFAULT_AUTO_RECOVER),
            'system_proxy': {
                'enabled': True,
                'bypass_local': True
            }
        }

    def init_default_configs(self, force: bool = False) -> dict:
        """Initialize default configuration files."""
        created = []
        configs = {
            'nodes.json': self.get_default_nodes_config,
            'routing.json': self.get_default_routing_config,
            'settings.json': self.get_default_settings_config
        }

        for file_name, default_func in configs.items():
            file_path = self._get_file_path(file_name)
            if os.path.exists(file_path) and not force:
                logger.info(f'{file_name} already exists, skipping')
                continue
            self.save_config(file_name, default_func())
            created.append(file_name)

        return created

    def _get_file_path(self, file_name: str) -> str:
        """Get absolute path for a config file."""
        return os.path.join(self.config_dir, file_name)

    def get_setting(self, key: str, default=None):
        """Get a setting value using dot notation."""
        settings = self.load_config('settings.json')
        parts = key.split('.')
        value = settings
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set_setting(self, key: str, value) -> None:
        """Set a setting value using dot notation."""
        settings = self.load_config('settings.json')
        parts = key.split('.')
        current = settings
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
        self.save_config('settings.json', settings)
