"""Tests for NodeManager module."""

import pytest

from xpilot.config import Config
from xpilot.node_manager import (
    NodeManager, NodeExistsError, NodeNotFoundError, ExportError
)


@pytest.fixture
def config():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Config(tmpdir)
        cfg.init_default_configs()
        yield cfg


@pytest.fixture
def manager(config):
    return NodeManager(config)


class TestNodeManager:
    def test_add_vmess_node(self, manager):
        """测试：添加 VMess 协议节点，验证节点 ID 自动生成及字段正确保存。"""
        node_info = {
            'name': 'Test VMess',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'test-uuid-1234',
        }
        node_id = manager.add_node(node_info)
        assert node_id == 'test_vmess'

        node = manager.get_node(node_id)
        assert node['name'] == 'Test VMess'
        assert node['protocol'] == 'vmess'
        assert node['uuid'] == 'test-uuid-1234'

    def test_add_trojan_node(self, manager):
        """测试：添加 Trojan 协议节点，验证 password 字段正确保存。"""
        node_info = {
            'name': 'Test Trojan',
            'protocol': 'trojan',
            'address': 'trojan.example.com',
            'port': 443,
            'password': 'secret',
        }
        node_id = manager.add_node(node_info)
        assert 'test_trojan' in node_id

    def test_add_duplicate_node(self, manager):
        """测试：添加已存在 ID 的节点时应抛出 NodeExistsError。"""
        node_info = {
            'id': 'my_node',
            'name': 'My Node',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-1',
        }
        manager.add_node(node_info)
        with pytest.raises(NodeExistsError):
            manager.add_node(node_info)

    def test_remove_node(self, manager):
        """测试：删除已存在的节点，删除后再次获取应抛出 NodeNotFoundError。"""
        node_info = {
            'name': 'To Remove',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-remove',
        }
        node_id = manager.add_node(node_info)
        manager.remove_node(node_id)
        with pytest.raises(NodeNotFoundError):
            manager.get_node(node_id)

    def test_remove_nonexistent_node(self, manager):
        """测试：删除不存在的节点时应抛出 NodeNotFoundError。"""
        with pytest.raises(NodeNotFoundError):
            manager.remove_node('nonexistent')

    def test_list_nodes(self, manager):
        """测试：列出所有节点，验证添加的节点全部出现在列表中。"""
        for i in range(3):
            manager.add_node({
                'name': f'Node {i}',
                'protocol': 'vmess',
                'address': 'example.com',
                'port': 443 + i,
                'uuid': f'uuid-{i}',
            })
        nodes = manager.list_nodes()
        assert len(nodes) == 3

    def test_list_nodes_filter_group(self, manager):
        """测试：按分组名称过滤节点，只返回匹配分组的节点。"""
        manager.add_node({
            'name': 'Work Node',
            'protocol': 'vmess',
            'address': 'work.example.com',
            'port': 443,
            'uuid': 'uuid-work',
            'group': 'work',
        })
        manager.add_node({
            'name': 'Default Node',
            'protocol': 'vmess',
            'address': 'default.example.com',
            'port': 443,
            'uuid': 'uuid-default',
        })
        work_nodes = manager.list_nodes(filter_group='work')
        assert len(work_nodes) == 1
        assert work_nodes[0]['group'] == 'work'

    def test_update_node(self, manager):
        """测试：修改节点的名称和地址，验证更新后读取到新值。"""
        node_info = {
            'name': 'Update Me',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-update',
        }
        node_id = manager.add_node(node_info)
        manager.update_node(node_id, {'name': 'Updated Name', 'address': 'new.example.com'})
        node = manager.get_node(node_id)
        assert node['name'] == 'Updated Name'
        assert node['address'] == 'new.example.com'

    def test_update_nonexistent_node(self, manager):
        """测试：修改不存在的节点时应抛出 NodeNotFoundError。"""
        with pytest.raises(NodeNotFoundError):
            manager.update_node('nonexistent', {'name': 'New'})

    def test_set_default_node(self, manager):
        """测试：设置默认节点后，get_default_node 应返回该节点 ID。"""
        node_info = {
            'name': 'Default',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-default',
        }
        node_id = manager.add_node(node_info)
        manager.set_default_node(node_id)
        assert manager.get_default_node() == node_id

    def test_export_json(self, manager):
        """测试：将节点导出为 JSON 格式，验证可正确解析且数量正确。"""
        manager.add_node({
            'name': 'Export Test',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-export',
        })
        output = manager.export_nodes(format='json')
        import json
        nodes = json.loads(output)
        assert len(nodes) == 1

    def test_export_yaml(self, manager):
        """测试：将节点导出为 YAML 格式，验证输出包含节点名称。"""
        manager.add_node({
            'name': 'Export Test',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-export',
        })
        output = manager.export_nodes(format='yaml')
        assert 'Export Test' in output

    def test_export_invalid_format(self, manager):
        """测试：使用不支持的格式（如 xml）导出时应抛出 ExportError。"""
        with pytest.raises(ExportError):
            manager.export_nodes(format='xml')

    def test_get_node_ids(self, manager):
        """测试：获取所有节点 ID 列表，验证添加的节点 ID 在列表中。"""
        manager.add_node({
            'name': 'ID Test',
            'protocol': 'vmess',
            'address': 'example.com',
            'port': 443,
            'uuid': 'uuid-id',
        })
        ids = manager.get_node_ids()
        assert 'id_test' in ids

    def test_get_groups(self, manager):
        """测试：获取所有分组信息，验证默认分组 default 存在。"""
        groups = manager.get_groups()
        assert 'default' in groups

    def test_import_subscription_refreshes_existing(self, manager, monkeypatch):
        """update_existing=True：同名节点的连接字段被刷新，group/id/name 保留。

        模拟机场轮换密钥/IP：本地旧节点 uuid=OLD，订阅返回同名节点 uuid=NEW。
        刷新后节点数量不变、id 不变，但 uuid/address/port 已更新，分组保留。
        """
        old_id = manager.add_node({
            'name': 'JMS-c56s1', 'protocol': 'vmess',
            'address': 'old.example.com', 'port': 443,
            'uuid': 'OLD-UUID', 'group': 'mygroup',
        })
        assert manager.get_node(old_id)['uuid'] == 'OLD-UUID'

        from xpilot import subscription
        monkeypatch.setattr(subscription, 'fetch', lambda url: 'fake-content')
        monkeypatch.setattr(subscription, 'parse', lambda content: [{
            'name': 'JMS-c56s1', 'protocol': 'vmess',
            'address': 'new.example.com', 'port': 8443,
            'uuid': 'NEW-UUID',
        }])

        count = manager.import_from_subscription('http://example.com/sub',
                                                 update_existing=True)
        assert count == 1
        ids = manager.get_node_ids()
        assert len(ids) == 1                      # 没有新增，仍是原来那一个
        assert old_id in ids                      # id 不变
        node = manager.get_node(old_id)
        assert node['uuid'] == 'NEW-UUID'         # 连接字段被刷新
        assert node['address'] == 'new.example.com'
        assert node['port'] == 8443
        assert node['group'] == 'mygroup'         # 用户态字段保留
        assert node['name'] == 'JMS-c56s1'

    def test_import_subscription_append_only_creates_duplicate(self, manager, monkeypatch):
        """update_existing=False（默认旧行为）：同名节点会被加后缀建成新节点，原节点不刷新。

        这正暴露了默认导入的缺陷——对同名节点既不刷新、也不跳过，而是不断
        创建重复项（jms-c56s1、jms-c56s1_1、jms-c56s1_2 ...）。update_existing=True
        既刷新密钥又避免重复堆积。
        """
        old_id = manager.add_node({
            'name': 'JMS-c56s1', 'protocol': 'vmess',
            'address': 'old.example.com', 'port': 443,
            'uuid': 'OLD-UUID',
        })
        from xpilot import subscription
        monkeypatch.setattr(subscription, 'fetch', lambda url: 'fake-content')
        monkeypatch.setattr(subscription, 'parse', lambda content: [{
            'name': 'JMS-c56s1', 'protocol': 'vmess',
            'address': 'new.example.com', 'port': 8443,
            'uuid': 'NEW-UUID',
        }])
        count = manager.import_from_subscription('http://example.com/sub')
        assert count == 1                          # 新建了一个加后缀的节点
        assert manager.get_node(old_id)['uuid'] == 'OLD-UUID'   # 原节点未被刷新
        assert len(manager.get_node_ids()) == 2     # 现在有两个节点（重复了）

    def test_import_with_source_tags_and_cleans(self, manager, monkeypatch):
        """source 模式：导入打 source 标记，并清理该 source 下订阅不再返回的旧节点。"""
        from xpilot import subscription
        # source=JMS 的旧节点（订阅不再返回它，应被清理）
        manager.add_node({'name': 'OldJMS', 'protocol': 'vmess',
                          'address': 'old.com', 'port': 443, 'uuid': 'u1', 'source': 'JMS'})
        # 手动节点（无 source，不应被清理）
        manager.add_node({'name': 'Manual', 'protocol': 'vmess',
                          'address': 'manual.com', 'port': 443, 'uuid': 'u2'})

        monkeypatch.setattr(subscription, 'fetch', lambda url: 'fake')
        monkeypatch.setattr(subscription, 'parse', lambda content: [
            {'name': 'NewJMS1', 'protocol': 'vmess', 'address': 'n1.com', 'port': 443, 'uuid': 'n1'},
            {'name': 'NewJMS2', 'protocol': 'vmess', 'address': 'n2.com', 'port': 443, 'uuid': 'n2'},
        ])
        manager.import_from_subscription('http://x', update_existing=True, source='JMS')

        names = {n['name'] for n in manager.list_nodes()}
        assert 'OldJMS' not in names               # source=JMS 且订阅不返回 → 已清理
        assert 'NewJMS1' in names and 'NewJMS2' in names
        assert 'Manual' in names                   # 无 source → 保留
        new = next(n for n in manager.list_nodes() if n['name'] == 'NewJMS1')
        assert new.get('source') == 'JMS'          # 新节点带上 source 标记

    def test_import_cleanup_skipped_on_sharp_drop(self, manager, monkeypatch):
        """骤降保护：订阅返回数 < 已有的一半时跳过清理，避免订阅故障误删。"""
        from xpilot import subscription
        for i in range(4):
            manager.add_node({'name': f'JMS{i}', 'protocol': 'vmess',
                              'address': f'h{i}.com', 'port': 443,
                              'uuid': f'u{i}', 'source': 'JMS'})
        # 订阅只返回 1 个（1 < 4*0.5=2）→ 触发保护
        monkeypatch.setattr(subscription, 'fetch', lambda url: 'fake')
        monkeypatch.setattr(subscription, 'parse', lambda content: [
            {'name': 'JMS0', 'protocol': 'vmess', 'address': 'new.com',
             'port': 443, 'uuid': 'new'},
        ])
        manager.import_from_subscription('http://x', update_existing=True, source='JMS')
        names = {n['name'] for n in manager.list_nodes()}
        # JMS1-3 未被清理（保护生效），JMS0 被刷新
        assert {'JMS0', 'JMS1', 'JMS2', 'JMS3'} <= names
