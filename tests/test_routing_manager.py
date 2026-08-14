"""Tests for RoutingManager module."""

import logging

import pytest

from xpilot.config import Config
from xpilot.routing_manager import RoutingManager


@pytest.fixture
def config():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config(tmpdir)
        cfg.init_default_configs()
        yield cfg


@pytest.fixture
def manager(config):
    return RoutingManager(config)


class TestRoutingManager:
    def test_add_proxy_rule(self, manager):
        """测试：添加代理规则到 proxy_list。"""
        manager.add_proxy_rule('geosite:netflix')
        assert 'geosite:netflix' in manager.get_proxy_rules()

    def test_add_direct_rule(self, manager):
        """测试：添加直连规则到 direct_list。"""
        manager.add_direct_rule('geoip:private')
        assert 'geoip:private' in manager.get_direct_rules()

    def test_add_block_rule(self, manager):
        """测试：添加拦截规则到 block_list。"""
        manager.add_block_rule('geosite:ads')
        assert 'geosite:ads' in manager.get_block_rules()

    def test_remove_rule(self, manager):
        """测试：从任意列表中移除规则。"""
        manager.add_proxy_rule('geosite:test')
        assert manager.remove_rule('geosite:test')
        assert 'geosite:test' not in manager.get_proxy_rules()

    def test_remove_nonexistent_rule(self, manager):
        """测试：移除不存在的规则返回 False。"""
        assert not manager.remove_rule('nonexistent')

    # ===== Domain routing tests =====

    def test_add_domain_rule(self, manager):
        """测试：添加域名到节点的映射规则。"""
        manager.add_domain_rule(['github.com', '*.github.io'], 'github_node', 'GitHub')
        rules = manager.get_domain_rules()
        assert len(rules) == 1
        assert rules[0]['node_id'] == 'github_node'
        assert 'github.com' in rules[0]['domains']

    def test_add_domain_rule_duplicate(self, manager):
        """测试：添加相同的域名规则不应重复。"""
        manager.add_domain_rule(['github.com'], 'github_node', 'GitHub')
        manager.add_domain_rule(['github.com'], 'github_node', 'GitHub')
        assert len(manager.get_domain_rules()) == 1

    def test_add_domain_rule_different_node(self, manager):
        """测试：相同域名指向不同节点应创建新规则。"""
        manager.add_domain_rule(['github.com'], 'node_a', 'GitHub A')
        manager.add_domain_rule(['github.com'], 'node_b', 'GitHub B')
        assert len(manager.get_domain_rules()) == 2

    def test_remove_domain_rule(self, manager):
        """测试：通过索引删除域名规则。"""
        manager.add_domain_rule(['github.com'], 'github_node', 'GitHub')
        manager.add_domain_rule(['twitter.com'], 'twitter_node', 'Twitter')
        assert manager.remove_domain_rule(0)
        assert len(manager.get_domain_rules()) == 1
        assert manager.get_domain_rules()[0]['description'] == 'Twitter'

    def test_remove_domain_rule_invalid_index(self, manager):
        """测试：使用无效索引删除域名规则返回 False。"""
        assert not manager.remove_domain_rule(0)
        assert not manager.remove_domain_rule(-1)

    def test_clear_domain_rules(self, manager):
        """测试：清空所有域名规则。"""
        manager.add_domain_rule(['github.com'], 'github_node', 'GitHub')
        manager.add_domain_rule(['twitter.com'], 'twitter_node', 'Twitter')
        manager.clear_domain_rules()
        assert len(manager.get_domain_rules()) == 0

    def test_generate_xray_routing_rules_with_domain(self, manager):
        """测试：生成 xray 路由规则时包含域名映射规则和对应的 outbound 信息。"""
        manager.add_proxy_rule('geosite:google')
        manager.add_direct_rule('geoip:private')
        manager.add_domain_rule(['github.com'], 'github_node', 'GitHub')

        result = manager.generate_xray_routing_rules()
        assert 'rules' in result
        assert 'domain_outbounds' in result

        # Check domain rule is in routing rules
        rule_domains = [r.get('domain', []) for r in result['rules'] if 'domain' in r]
        assert ['github.com'] in rule_domains

        # Check domain outbound mapping
        assert len(result['domain_outbounds']) == 1
        domains, node_id, tag = result['domain_outbounds'][0]
        assert 'github.com' in domains
        assert node_id == 'github_node'
        assert tag == 'proxy_github_node'

    # ===== proxy_all tests =====

    def test_proxy_all_default_global(self, manager):
        """测试：默认 proxy_all=true（全局代理），末尾有兜底走代理规则。"""
        result = manager.generate_xray_routing_rules()
        assert result['rules'][-1] == {
            'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'proxy'}

    def test_proxy_all_missing_field_defaults_to_global(self, manager, config):
        """测试：旧配置缺 proxy_all 字段时按全局代理处理。"""
        routing = config.load_config('routing.json')
        routing.pop('proxy_all')
        config.save_config('routing.json', routing)
        result = manager.generate_xray_routing_rules()
        assert result['rules'][-1] == {
            'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'proxy'}

    def test_proxy_all_false_whitelist(self, manager, config):
        """测试：proxy_all=false（白名单分流），末尾兜底直连。"""
        routing = config.load_config('routing.json')
        routing['proxy_all'] = False
        config.save_config('routing.json', routing)
        result = manager.generate_xray_routing_rules()
        assert result['rules'][-1] == {
            'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'direct'}

    def test_proxy_all_bare_domain_kept(self, manager):
        """测试：裸域名规则不再被静默丢弃，按 domain 规则生效。"""
        manager.add_proxy_rule('openapi.tigerfintech.com')
        result = manager.generate_xray_routing_rules()
        domain_rules = [r for r in result['rules']
                        if r.get('domain') == ['openapi.tigerfintech.com']]
        assert domain_rules, '裸域名规则应被保留为 domain 规则'
        assert domain_rules[0]['outboundTag'] == 'proxy'

    def test_proxy_all_conflict_warning(self, manager, config, caplog):
        """测试：全局代理模式下自定义 0.0.0.0/0 直连兜底规则触发 warning。"""
        routing = config.load_config('routing.json')
        routing['rules'] = [{'type': 'direct', 'ip': ['0.0.0.0/0', '::/0']}]
        config.save_config('routing.json', routing)
        with caplog.at_level(logging.WARNING):
            manager.generate_xray_routing_rules()
        assert any('overrides proxy_all' in r.message for r in caplog.records)
