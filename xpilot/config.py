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

    def __init__(self, config_dir: str = None):
        from .utils import get_config_dir
        self.config_dir = config_dir or os.environ.get('PROXY_TOOLKIT_CONFIG_DIR') or get_config_dir()
        self._settings_file = os.environ.get('PROXY_TOOLKIT_SETTINGS_FILE', 'settings.json')
        os.makedirs(self.config_dir, exist_ok=True)

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
        """Get default routing configuration."""
        return {
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
            'log_file': '/tmp/xpilot.log',
            'auto_switch': {
                'enabled': True,
                'interval': 300,
                'strategy': 'latency',
                # 切换迟滞比例：最优节点延迟需比当前低该比例以上才切换，防抖；
                # 0 = 最优不是当前就切（默认，最积极选优）。
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
            'subscription': {
                'auto_update': False,
                'update_interval': 3600
            },
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
