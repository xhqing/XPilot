"""Routing rule management for xpilot."""

import ipaddress
import logging

logger = logging.getLogger(__name__)


class RoutingManager:
    """Manage routing rules for proxy."""

    def __init__(self, config):
        self.config = config

    def get_rules(self) -> dict:
        """Get current routing rules."""
        return self.config.load_config('routing.json')

    def add_proxy_rule(self, rule: str) -> None:
        """Add a rule to the proxy list."""
        routing = self.config.load_config('routing.json')
        if rule not in routing.get('proxy_list', []):
            routing.setdefault('proxy_list', []).append(rule)
            self.config.save_config('routing.json', routing)
            logger.info(f'Added proxy rule: {rule}')
        else:
            logger.info(f'Proxy rule already exists: {rule}')

    def add_direct_rule(self, rule: str) -> None:
        """Add a rule to the direct list."""
        routing = self.config.load_config('routing.json')
        if rule not in routing.get('direct_list', []):
            routing.setdefault('direct_list', []).append(rule)
            self.config.save_config('routing.json', routing)
            logger.info(f'Added direct rule: {rule}')
        else:
            logger.info(f'Direct rule already exists: {rule}')

    def add_block_rule(self, rule: str) -> None:
        """Add a rule to the block list."""
        routing = self.config.load_config('routing.json')
        if rule not in routing.get('block_list', []):
            routing.setdefault('block_list', []).append(rule)
            self.config.save_config('routing.json', routing)
            logger.info(f'Added block rule: {rule}')
        else:
            logger.info(f'Block rule already exists: {rule}')

    def remove_rule(self, rule: str) -> bool:
        """Remove a rule from all lists."""
        routing = self.config.load_config('routing.json')
        removed = False
        for key in ['proxy_list', 'direct_list', 'block_list']:
            if rule in routing.get(key, []):
                routing[key].remove(rule)
                removed = True
                logger.info(f'Removed rule from {key}: {rule}')
        if removed:
            self.config.save_config('routing.json', routing)
        return removed

    def get_proxy_rules(self) -> list:
        """Get all proxy rules."""
        routing = self.config.load_config('routing.json')
        return routing.get('proxy_list', [])

    def get_direct_rules(self) -> list:
        """Get all direct rules."""
        routing = self.config.load_config('routing.json')
        return routing.get('direct_list', [])

    def get_block_rules(self) -> list:
        """Get all block rules."""
        routing = self.config.load_config('routing.json')
        return routing.get('block_list', [])

    # ===== Domain-to-node mapping rules =====

    def get_domain_rules(self) -> list:
        """Get all domain-to-node mapping rules."""
        routing = self.config.load_config('routing.json')
        return routing.get('domain_rules', [])

    def add_domain_rule(self, domains: list, node_id: str, description: str = '') -> None:
        """Add a domain-to-node mapping rule.
        
        Args:
            domains: List of domain patterns (e.g., ['github.com', '*.github.io'])
            node_id: The node ID to route these domains through
            description: Optional description for the rule
        """
        routing = self.config.load_config('routing.json')
        rule = {
            'domains': domains,
            'node_id': node_id,
            'description': description or ', '.join(domains),
        }
        domain_rules = routing.setdefault('domain_rules', [])
        # Check for duplicate
        for existing in domain_rules:
            if existing['node_id'] == node_id and set(existing['domains']) == set(domains):
                logger.info(f'Domain rule already exists: {rule["description"]}')
                return
        domain_rules.append(rule)
        self.config.save_config('routing.json', routing)
        logger.info(f'Added domain rule: {rule["description"]} -> {node_id}')

    def remove_domain_rule(self, index: int) -> bool:
        """Remove a domain rule by index (0-based)."""
        routing = self.config.load_config('routing.json')
        domain_rules = routing.get('domain_rules', [])
        if 0 <= index < len(domain_rules):
            removed = domain_rules.pop(index)
            self.config.save_config('routing.json', routing)
            logger.info(f'Removed domain rule: {removed["description"]}')
            return True
        return False

    def clear_domain_rules(self) -> None:
        """Clear all domain rules."""
        routing = self.config.load_config('routing.json')
        routing['domain_rules'] = []
        self.config.save_config('routing.json', routing)
        logger.info('Cleared all domain rules')

    def _append_field_rule(self, rules: list, rule: str, outbound_tag: str) -> None:
        """把一条列表规则字符串追加为 Xray field 规则。

        - `geosite:` / `domain:` / `regexp:` 前缀 → domain 字段；
        - `geoip:` 前缀或裸 IP / CIDR → ip 字段；
        - 裸域名（无前缀）：按 domain 处理（Xray 裸域名即主域名匹配，
          含子域名），但打 warning 提示补前缀——避免规则被静默丢弃、
          流量意外落兜底导致「以为走了代理、实际直连」。
        """
        if rule.startswith(('geosite:', 'domain:', 'regexp:')):
            rules.append({'type': 'field', 'domain': [rule], 'outboundTag': outbound_tag})
            return
        if rule.startswith('geoip:'):
            rules.append({'type': 'field', 'ip': [rule], 'outboundTag': outbound_tag})
            return
        try:
            ipaddress.ip_network(rule, strict=False)
            rules.append({'type': 'field', 'ip': [rule], 'outboundTag': outbound_tag})
            return
        except ValueError:
            pass
        logger.warning(
            f'{outbound_tag} rule {rule!r} has no prefix (domain:/geoip:/geosite:); '
            f'treating it as a domain rule, consider adding the "domain:" prefix')
        rules.append({'type': 'field', 'domain': [rule], 'outboundTag': outbound_tag})

    def generate_xray_routing_rules(self) -> dict:
        """Generate xray-compatible routing rules and domain outbound mappings.

        proxy_all=True（默认，全局代理）：除 block_list / direct_list 明确
        列出的流量外，其余全部走代理，末尾显式加「未匹配兜底走代理」规则，
        不依赖 Xray 的默认 outbound 行为，从机制上杜绝直连泄漏；
        proxy_all=False（白名单分流）：只有 proxy_list 列出的走代理，
        未匹配流量兜底直连。

        Returns:
            dict with keys:
                - 'rules': list of xray routing rules
                - 'domain_outbounds': list of (domains, node_id) tuples for extra outbounds
        """
        routing = self.config.load_config('routing.json')
        proxy_all = routing.get('proxy_all', True)
        rules = []

        # Domain-to-node mapping rules (highest priority, placed first)
        domain_outbounds = []
        for rule in routing.get('domain_rules', []):
            node_id = rule['node_id']
            domains = rule['domains']
            outbound_tag = f'proxy_{node_id}'
            rules.append({
                'type': 'field',
                'domain': domains,
                'outboundTag': outbound_tag,
            })
            domain_outbounds.append((domains, node_id, outbound_tag))

        # Block rules
        for rule in routing.get('block_list', []):
            self._append_field_rule(rules, rule, 'block')

        # Proxy rules (must come before direct rules to take priority)
        for rule in routing.get('proxy_list', []):
            self._append_field_rule(rules, rule, 'proxy')

        # Direct rules
        for rule in routing.get('direct_list', []):
            self._append_field_rule(rules, rule, 'direct')

        # Custom rules
        for rule in routing.get('rules', []):
            rule_type = rule.get('type', 'proxy')
            outbound_tag = rule_type if rule_type in ['proxy', 'direct', 'block'] else 'proxy'
            field = {}
            if 'domain' in rule:
                field['domain'] = rule['domain']
            if 'ip' in rule:
                field['ip'] = rule['ip']
            if 'port' in rule:
                field['port'] = rule['port']
            if field:
                field['type'] = 'field'
                field['outboundTag'] = outbound_tag
                rules.append(field)
                # 全局代理模式下，自定义规则里的全量直连兜底（0.0.0.0/0）
                # 会先于代理兜底拦截所有流量，提示用户其与 proxy_all 冲突。
                if proxy_all and outbound_tag == 'direct' and '0.0.0.0/0' in rule.get('ip', []):
                    logger.warning(
                        'Custom direct rule 0.0.0.0/0 overrides proxy_all=true: '
                        'all unmatched traffic goes direct. Remove that rule or '
                        'set proxy_all=false to use whitelist routing')

        # Fallback rule: unmatched traffic goes to proxy (global) or direct (whitelist).
        # Must carry a real condition (network) — Xray rejects rules with no
        # effective fields ("this rule has no effective fields") and fails to start.
        rules.append({
            'type': 'field',
            'network': 'tcp,udp',
            'outboundTag': 'proxy' if proxy_all else 'direct',
        })

        return {
            'rules': rules,
            'domain_outbounds': domain_outbounds,
        }
