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
