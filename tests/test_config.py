"""Tests for Config module."""

import json
import os
import tempfile
import pytest

from xpilot.config import Config, ConfigError, ValidationError


@pytest.fixture
def config_dir():
    """Create a temporary config directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def config(config_dir):
    """Create a Config instance with temporary directory."""
    return Config(config_dir)


class TestConfig:
    def test_load_config_not_found(self, config):
        """测试：加载不存在的配置文件时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            config.load_config('nonexistent.json')

    def test_save_and_load_config(self, config):
        """测试：配置文件保存后能正确读取，数据完整无误。"""
        data = {'key': 'value', 'number': 42}
        config.save_config('test.json', data)
        loaded = config.load_config('test.json')
        assert loaded == data

    def test_save_config_sets_permissions(self, config):
        """测试：保存配置文件时自动设置文件权限为 0600（仅所有者可读写）。"""
        config.save_config('test.json', {'key': 'value'})
        file_path = os.path.join(config.config_dir, 'test.json')
        stat = os.stat(file_path)
        assert stat.st_mode & 0o777 == 0o600

    def test_validate_config_missing_required(self, config):
        """测试：校验配置时缺少必填字段应抛出 ValidationError。"""
        schema = {'name': {'required': True, 'type': str}}
        with pytest.raises(ValidationError):
            config.validate_config(schema, {})

    def test_validate_config_wrong_type(self, config):
        """测试：校验配置时字段类型不匹配应抛出 ValidationError。"""
        schema = {'port': {'required': True, 'type': int}}
        with pytest.raises(ValidationError):
            config.validate_config(schema, {'port': 'not_a_number'})

    def test_validate_config_invalid_enum(self, config):
        """测试：校验配置时字段值不在允许的枚举列表中应抛出 ValidationError。"""
        schema = {'protocol': {'required': True, 'type': str, 'enum': ['vmess', 'vless']}}
        with pytest.raises(ValidationError):
            config.validate_config(schema, {'protocol': 'invalid'})

    def test_validate_config_success(self, config):
        """测试：校验配置时数据完全符合 schema 应返回 True。"""
        schema = {'name': {'required': True, 'type': str}}
        assert config.validate_config(schema, {'name': 'test'})

    def test_get_default_nodes_config(self, config):
        """测试：获取默认节点配置，应包含 nodes、groups、default_node 三个字段。"""
        nodes = config.get_default_nodes_config()
        assert 'nodes' in nodes
        assert 'groups' in nodes
        assert 'default_node' in nodes

    def test_get_default_routing_config(self, config):
        """测试：获取默认路由配置，应包含 proxy_list、direct_list、block_list、domain_rules 四个字段。"""
        routing = config.get_default_routing_config()
        assert 'proxy_list' in routing
        assert 'direct_list' in routing
        assert 'block_list' in routing
        assert 'domain_rules' in routing

    def test_get_default_settings_config(self, config):
        """测试：获取默认全局设置，应包含 xray_bin、socks_port、auto_switch 等字段。"""
        settings = config.get_default_settings_config()
        assert 'xray_bin' in settings
        assert 'socks_port' in settings
        assert 'auto_switch' in settings

    def test_init_default_configs(self, config):
        """测试：首次初始化时应创建全部 3 个默认配置文件。"""
        created = config.init_default_configs()
        assert len(created) == 3
        assert 'nodes.json' in created
        assert 'routing.json' in created
        assert 'settings.json' in created

    def test_init_default_configs_no_force(self, config):
        """测试：配置文件已存在且未指定 force 时，不应重复创建。"""
        config.init_default_configs()
        created = config.init_default_configs()
        assert len(created) == 0

    def test_init_default_configs_force(self, config):
        """测试：配置文件已存在但指定 force=True 时，应覆盖全部 3 个配置文件。"""
        config.init_default_configs()
        created = config.init_default_configs(force=True)
        assert len(created) == 3

    def test_get_setting(self, config):
        """测试：使用点符号路径读取顶层和嵌套配置值，不存在时返回默认值。"""
        config.init_default_configs()
        assert config.get_setting('socks_port') == 1080
        assert config.get_setting('auto_switch.enabled') is True
        assert config.get_setting('nonexistent', 'default') == 'default'

    def test_set_setting(self, config):
        """测试：使用点符号路径修改顶层配置值后能正确读取。"""
        config.init_default_configs()
        config.set_setting('log_level', 'debug')
        assert config.get_setting('log_level') == 'debug'

    def test_set_setting_nested(self, config):
        """测试：使用点符号路径修改嵌套配置值（如 auto_switch.enabled）后能正确读取。"""
        config.init_default_configs()
        config.set_setting('auto_switch.enabled', True)
        assert config.get_setting('auto_switch.enabled') is True

    def test_invalid_json(self, config):
        """测试：加载格式错误的 JSON 文件时应抛出 ConfigError。"""
        file_path = os.path.join(config.config_dir, 'bad.json')
        with open(file_path, 'w') as f:
            f.write('{invalid json}')
        with pytest.raises(ConfigError):
            config.load_config('bad.json')


BASE_SETTINGS = {
    'socks_port': 1080,
    'auto_switch': {'enabled': False},
    'subscription': {'auto_update': False, 'update_interval': 3600},
}


def _write_settings(tmp_dir, settings):
    path = os.path.join(str(tmp_dir), 'settings.json')
    with open(path, 'w') as f:
        json.dump(settings, f, indent=2)
    return path


def _read_settings(tmp_dir):
    with open(os.path.join(str(tmp_dir), 'settings.json')) as f:
        return json.load(f)


class TestSubscriptionFlagMigration:
    """Issue #6 存量迁移：把旧版「从未生效过」的 subscription 默认值迁到新默认。

    0.6.4 及更早生成的 settings.json 写着 ``auto_update: false``，但那时没人读
    这个键，那个 false 不表达用户意图。不迁移的话，存量安装升级后定时同步全是
    关的，Issue #6 等于白修。判据保守：只有整个块恰好等于旧默认值才动。
    """

    def test_legacy_constant_matches_old_defaults(self):
        assert Config.LEGACY_SUBSCRIPTION_DEFAULTS == {
            'auto_update': False, 'update_interval': 3600}

    def test_legacy_default_block_is_migrated(self, tmp_path):
        _write_settings(tmp_path, BASE_SETTINGS)

        Config(str(tmp_path))

        saved = _read_settings(tmp_path)
        assert saved['subscription']['auto_update'] is True
        assert saved['subscription']['update_interval'] == 3600
        assert saved['socks_port'] == 1080, '迁移只动 subscription 块，其余配置保持不变'

    @pytest.mark.parametrize('subscription', [
        {'auto_update': False, 'update_interval': 900},
        {'auto_update': True, 'update_interval': 3600},
        {'auto_update': False, 'update_interval': 3600, 'x': 1},
    ])
    def test_customized_block_is_left_alone(self, tmp_path, subscription):
        """不是「恰好等于旧默认值」就一律不动——用户配置过就尊重用户配置。"""
        settings = dict(BASE_SETTINGS)
        settings['subscription'] = subscription
        _write_settings(tmp_path, settings)

        Config(str(tmp_path))

        assert _read_settings(tmp_path)['subscription'] == subscription

    def test_missing_block_is_left_alone(self, tmp_path):
        settings = dict(BASE_SETTINGS)
        del settings['subscription']
        _write_settings(tmp_path, settings)

        Config(str(tmp_path))

        assert 'subscription' not in _read_settings(tmp_path)

    def test_missing_settings_file_is_not_created(self, tmp_path):
        Config(str(tmp_path))

        assert not os.path.exists(os.path.join(str(tmp_path), 'settings.json'))

    def test_broken_json_is_ignored(self, tmp_path):
        path = os.path.join(str(tmp_path), 'settings.json')
        with open(path, 'w') as f:
            f.write('{not json')

        Config(str(tmp_path))  # 不抛错

        with open(path) as f:
            assert f.read() == '{not json', '损坏的文件保持原样，不覆盖'

    def test_migration_happens_only_once(self, tmp_path):
        """核心回归锁：迁移后用户手动关闭，再启动必须保持关闭（不会被迁回 true）。"""
        _write_settings(tmp_path, BASE_SETTINGS)

        Config(str(tmp_path))
        upgraded = _read_settings(tmp_path)
        assert upgraded['subscription']['auto_update'] is True
        assert upgraded['auto_recover'] == Config.DEFAULT_AUTO_RECOVER

        upgraded['subscription']['auto_update'] = False  # 用户主动关闭定时同步
        _write_settings(tmp_path, upgraded)

        Config(str(tmp_path))

        saved = _read_settings(tmp_path)
        assert saved['subscription']['auto_update'] is False
        assert saved['auto_recover'] == Config.DEFAULT_AUTO_RECOVER

    def test_existing_auto_recover_block_skips_migration(self, tmp_path):
        """有 auto_recover 块 = 新配置：subscription 逐字等于旧默认值也不动。"""
        settings = dict(BASE_SETTINGS)
        settings['auto_recover'] = {'enabled': False, 'dead_restart_ticks': 0}
        _write_settings(tmp_path, settings)

        Config(str(tmp_path))

        saved = _read_settings(tmp_path)
        assert saved['subscription'] == {'auto_update': False, 'update_interval': 3600}
        assert saved['auto_recover'] == {'enabled': False, 'dead_restart_ticks': 0}

    def test_upgrade_installs_default_auto_recover_block(self, tmp_path):
        """旧配置升级后 auto_recover 块就位，内容等于默认值（单一来源常量）。"""
        _write_settings(tmp_path, BASE_SETTINGS)

        Config(str(tmp_path))

        assert _read_settings(tmp_path)['auto_recover'] == Config.DEFAULT_AUTO_RECOVER

    @pytest.mark.parametrize('block', [
        {'auto_update': 0, 'update_interval': 3600},
        {'auto_update': False, 'update_interval': 3600.0},
    ])
    def test_strict_type_check_does_not_treat_as_legacy(self, tmp_path, block):
        """0 与 False、3600.0 与 3600 在 Python 里相等，但不该被判成旧默认值。"""
        settings = dict(BASE_SETTINGS)
        settings['subscription'] = block
        _write_settings(tmp_path, settings)

        Config(str(tmp_path))

        saved = _read_settings(tmp_path)['subscription']
        assert json.dumps(saved, sort_keys=True) == json.dumps(block, sort_keys=True)
