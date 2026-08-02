"""Node management for xpilot."""

import logging
import yaml

from .utils import generate_node_id, validate_protocol, validate_port, format_timestamp

logger = logging.getLogger(__name__)


class NodeError(Exception):
    """Node related errors."""
    pass


class NodeExistsError(NodeError):
    """Node already exists."""
    pass


class NodeNotFoundError(NodeError):
    """Node not found."""
    pass


class ExportError(NodeError):
    """Export error."""
    pass


class SubscriptionError(NodeError):
    """Subscription import error."""
    pass


class NodeManager:
    """Manage proxy nodes."""

    def __init__(self, config):
        self.config = config

    def add_node(self, node_info: dict) -> str:
        """Add a new node."""
        nodes_config = self.config.load_config('nodes.json')
        existing_ids = set(nodes_config.get('nodes', {}).keys())

        node_id = node_info.get('id') or generate_node_id(node_info['name'], existing_ids)

        if node_id in existing_ids:
            raise NodeExistsError(f'Node already exists: {node_id}')

        node = {
            'id': node_id,
            'name': node_info['name'],
            'protocol': node_info['protocol'].lower(),
            'address': node_info['address'],
            'port': node_info['port'],
            'status': 'active',
            'group': node_info.get('group', 'default'),
        }

        # Protocol-specific fields
        if node['protocol'] in ('vmess', 'vless'):
            node['uuid'] = node_info.get('uuid', '')
        if node['protocol'] in ('trojan', 'ss', 'shadowsocks'):
            node['password'] = node_info.get('password', '')

        # Optional fields
        for key in ('alterId', 'security', 'network', 'tls', 'servername',
                    'reality_public_key', 'reality_short_id', 'fingerprint', 'flow',
                    'source'):
            if key in node_info:
                node[key] = node_info[key]

        # Defaults
        node.setdefault('alterId', 0)
        node.setdefault('security', 'auto')
        node.setdefault('network', 'tcp')
        node.setdefault('tls', False)
        node.setdefault('servername', '')
        node.setdefault('latency', 0)
        node.setdefault('last_check', '')

        # Validate
        if not validate_protocol(node['protocol']):
            raise NodeError(f'Unsupported protocol: {node["protocol"]}')
        if not validate_port(node['port']):
            raise NodeError(f'Invalid port: {node["port"]}')

        nodes_config.setdefault('nodes', {})[node_id] = node
        self.config.save_config('nodes.json', nodes_config)
        logger.info(f'Added node: {node_id} ({node["name"]})')
        return node_id

    def remove_node(self, node_id: str) -> None:
        """Remove a node."""
        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.get('nodes', {})
        if node_id not in nodes:
            raise NodeNotFoundError(f'Node not found: {node_id}')

        del nodes[node_id]
        if nodes_config.get('default_node') == node_id:
            nodes_config['default_node'] = None
        self.config.save_config('nodes.json', nodes_config)
        logger.info(f'Removed node: {node_id}')

    def get_node(self, node_id: str) -> dict:
        """Get node information."""
        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.get('nodes', {})
        if node_id not in nodes:
            raise NodeNotFoundError(f'Node not found: {node_id}')
        return nodes[node_id]

    def resolve_node_ref(self, ref: str) -> str:
        """Resolve a user-facing reference (ID or name) to a node ID.

        The ID is tried first; if it does not exist, an exact name match is
        used. This lets users operate on non-ASCII node names (e.g.
        ``node remove "日本节点"``) whose auto-generated IDs are hashes.

        Raises NodeNotFoundError when nothing matches, or NodeError when the
        name is ambiguous (matches more than one node).
        """
        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.get('nodes', {})
        if ref in nodes:
            return ref
        matches = [nid for nid, n in nodes.items() if n.get('name') == ref]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise NodeError(f'Multiple nodes match name "{ref}": {", ".join(matches)}')
        raise NodeNotFoundError(f'Node not found: {ref}')

    def list_nodes(self, filter_group: str = None) -> list:
        """List all nodes, optionally filtered by group."""
        nodes_config = self.config.load_config('nodes.json')
        nodes = list(nodes_config.get('nodes', {}).values())
        if filter_group:
            nodes = [n for n in nodes if n.get('group') == filter_group]
        return nodes

    def update_node(self, node_id: str, updates: dict) -> None:
        """Update node information."""
        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.get('nodes', {})
        if node_id not in nodes:
            raise NodeNotFoundError(f'Node not found: {node_id}')

        node = nodes[node_id]
        for key, value in updates.items():
            if key in ('id',):
                continue  # Cannot change ID
            if key in ('name', 'protocol', 'address', 'port', 'uuid', 'password',
                       'alterId', 'security', 'network', 'tls', 'servername',
                       'reality_public_key', 'reality_short_id', 'fingerprint', 'flow',
                       'source', 'group', 'status'):
                node[key] = value

        if 'port' in updates and not validate_port(node['port']):
            raise NodeError(f'Invalid port: {node["port"]}')

        self.config.save_config('nodes.json', nodes_config)
        logger.info(f'Updated node: {node_id}')

    # 订阅刷新时覆盖这些「连接相关」字段；id / name / group / status 等
    # 用户态字段保留不变，避免刷新把用户自定义的分组和重命名冲掉。
    SUBSCRIPTION_REFRESH_FIELDS = (
        'address', 'port', 'uuid', 'password', 'alterId',
        'security', 'network', 'tls', 'servername',
        'reality_public_key', 'reality_short_id', 'fingerprint', 'flow',
    )

    def _refresh_node_fields(self, node_id: str, node_data: dict) -> None:
        """用订阅解析出的连接字段刷新已有节点，保留 id/name/group/status。

        用于 update_existing=True 的订阅导入：机场轮换了某节点的密钥或 IP、
        但节点名没变时，按名匹配到已有节点并覆盖连接字段，使旧节点真正被刷新。
        """
        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.get('nodes', {})
        if node_id not in nodes:
            raise NodeNotFoundError(f'Node not found: {node_id}')
        node = nodes[node_id]
        for field in self.SUBSCRIPTION_REFRESH_FIELDS:
            if field in node_data and node_data[field] is not None:
                node[field] = node_data[field]
        # source 是订阅归属标记，刷新时补上，让旧节点也纳入自动清理
        if node_data.get('source'):
            node['source'] = node_data['source']
        node['last_check'] = format_timestamp()
        self.config.save_config('nodes.json', nodes_config)
        logger.info(f'Refreshed node from subscription: {node_id} ({node.get("name")})')

    def import_from_subscription(self, url: str, update_existing: bool = False,
                                 source: str = None) -> int:
        """从订阅 URL 导入节点。

        - update_existing=False（默认）：仅追加新节点，同名节点跳过。
        - update_existing=True：按节点名匹配已有节点，用订阅里的最新连接字段
          覆盖旧值，保留 id、分组与自定义名。
        - source：订阅源名称（如 'JMS'）。给导入/刷新的节点打 source 标记，并
          自动清理该 source 下「订阅不再返回」的旧节点（订阅改名/移除时生效）。
          带骤降保护：本次返回数少于该 source 已有节点数一半时跳过清理，避免
          订阅临时故障返回不全导致误删。手动 ``node add`` 的节点无 source，永不
          参与自动清理。

        返回值：默认模式为新增数；update_existing 模式为「新增 + 刷新 + 清理」总数。
        """
        from .subscription import fetch, parse
        content = fetch(url)
        parsed_nodes = parse(content)

        if not parsed_nodes:
            raise SubscriptionError('No nodes found in subscription')

        sub_names = {n.get('name') for n in parsed_nodes if n.get('name')}
        if source:
            for n in parsed_nodes:
                n['source'] = source

        nodes_config = self.config.load_config('nodes.json')
        nodes = nodes_config.setdefault('nodes', {})

        # ---- 自动清理：该 source 下订阅不再返回的节点（带骤降保护）----
        removed = 0
        cleanup_skipped = False
        if source:
            source_ids = [nid for nid, n in nodes.items() if n.get('source') == source]
            if source_ids:
                if len(sub_names) < len(source_ids) * 0.5:
                    logger.warning(
                        f'订阅 {source} 返回 {len(sub_names)} 个，远少于已有的 '
                        f'{len(source_ids)} 个，疑似订阅故障，跳过自动清理（仍刷新/导入）')
                    cleanup_skipped = True
                else:
                    for nid in source_ids:
                        if nodes[nid].get('name') not in sub_names:
                            del nodes[nid]
                            removed += 1
                    if removed:
                        if nodes_config.get('default_node') not in nodes:
                            nodes_config['default_node'] = None
                        self.config.save_config('nodes.json', nodes_config)
                        logger.info(f'清理 {removed} 个订阅不再返回的旧节点（source={source}）')

        # ---- 刷新已有 + 导入新增 ----
        name_to_id = {n.get('name'): nid for nid, n in nodes.items()}
        existing_ids = set(nodes.keys())

        imported = 0
        updated = 0
        failed = 0
        for node_data in parsed_nodes:
            name = node_data.get('name')
            try:
                if update_existing and name and name in name_to_id:
                    self._refresh_node_fields(name_to_id[name], node_data)
                    updated += 1
                    continue
                node_id = generate_node_id(name, existing_ids)
                node_data['id'] = node_id
                self.add_node(node_data)
                existing_ids.add(node_id)
                name_to_id[name] = node_id
                imported += 1
            except NodeExistsError:
                logger.debug(f'Skipping duplicate node: {name}')
            except NodeError as e:
                failed += 1
                logger.warning(f'Failed to import node: {name} - {e}')

        # ---- 节点数异常监控：该 source 最终节点数应等于订阅返回数 ----
        if source:
            final_nodes = self.config.load_config('nodes.json').get('nodes', {})
            final = sum(1 for n in final_nodes.values() if n.get('source') == source)
            expected = len(sub_names)
            if final != expected:
                logger.warning(
                    f'[订阅节点数异常] {source}：订阅返回 {expected} 个，本地 {final} 个'
                    f'（新增 {imported}、刷新 {updated}、失败 {failed}、清理 {removed}'
                    f'{"、清理被跳过(骤降保护)" if cleanup_skipped else ""}）。'
                    f'常见原因：个别节点解析/校验失败、或订阅返回不全。')

        if update_existing:
            logger.info(f'Imported {imported} new, refreshed {updated}, removed {removed} '
                        f'from subscription ({source or "no source"})')
            return imported + updated + removed
        logger.info(f'Imported {imported} nodes from subscription')
        return imported

    def export_nodes(self, format: str = 'json') -> str:
        """Export nodes in specified format."""
        nodes = self.list_nodes()
        if format == 'json':
            import json
            return json.dumps(nodes, indent=2, ensure_ascii=False)
        elif format == 'yaml':
            return yaml.dump(nodes, allow_unicode=True, default_flow_style=False)
        else:
            raise ExportError(f'Unsupported export format: {format}')

    def get_default_node(self) -> str:
        """Get the default node ID."""
        nodes_config = self.config.load_config('nodes.json')
        return nodes_config.get('default_node')

    def set_default_node(self, node_id: str) -> None:
        """Set the default node."""
        self.get_node(node_id)  # Validate node exists
        nodes_config = self.config.load_config('nodes.json')
        nodes_config['default_node'] = node_id
        self.config.save_config('nodes.json', nodes_config)

    def get_groups(self) -> dict:
        """Get all node groups."""
        nodes_config = self.config.load_config('nodes.json')
        return nodes_config.get('groups', {})

    def get_node_ids(self) -> list:
        """Get all node IDs."""
        nodes_config = self.config.load_config('nodes.json')
        return list(nodes_config.get('nodes', {}).keys())
