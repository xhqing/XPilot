#!/usr/bin/env python3
"""Merge nodes.json from another project into xpilot config."""

import json
import os
import sys


def load_json(path: str) -> dict:
    """Load JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path: str, data: dict) -> None:
    """Save JSON file with owner-only permissions."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(path, 0o600)


def generate_node_id(name: str, existing_ids: set) -> str:
    """Generate a unique node ID."""
    import re
    base_id = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower())
    if not base_id:
        base_id = 'node'
    node_id = base_id
    counter = 1
    while node_id in existing_ids:
        node_id = f'{base_id}_{counter}'
        counter += 1
    return node_id


def merge_nodes(source_path: str, target_path: str = None) -> None:
    """Merge nodes from source file into xpilot config."""
    # Source config
    source = load_json(source_path)
    source_nodes = source.get('nodes', {})
    if not source_nodes:
        print('No nodes found in source file.')
        sys.exit(1)

    print(f'Found {len(source_nodes)} nodes in source file.')

    # Target config
    if target_path is None:
        config_dir = os.path.expanduser('~/.config/xpilot')
        target_path = os.path.join(config_dir, 'nodes.json')
    
    if not os.path.exists(target_path):
        print(f'Target config not found: {target_path}')
        print('Run `xpilot init` first.')
        sys.exit(1)

    target = load_json(target_path)
    target_nodes = target.setdefault('nodes', {})
    existing_ids = set(target_nodes.keys())

    # Merge
    imported = 0
    skipped = 0
    for node_id, node_data in source_nodes.items():
        # Ensure required fields
        if 'id' not in node_data:
            node_data['id'] = node_id
        if 'name' not in node_data:
            node_data['name'] = node_id
        
        # Check if node already exists by ID
        if node_id in target_nodes:
            # Check if data is the same
            if target_nodes[node_id] == node_data:
                skipped += 1
                continue
            # Generate new ID for duplicate with different data
            new_id = generate_node_id(node_data.get('name', node_id), existing_ids)
            node_data['id'] = new_id
            existing_ids.add(new_id)
        else:
            existing_ids.add(node_id)

        # Set defaults for missing fields
        node_data.setdefault('alterId', 0)
        node_data.setdefault('security', 'auto')
        node_data.setdefault('network', 'tcp')
        node_data.setdefault('tls', False)
        node_data.setdefault('servername', '')
        node_data.setdefault('status', 'active')
        node_data.setdefault('group', source.get('groups', {}).get(
            node_data.get('group', 'default'), 'default') if 'group' in node_data else 'default')
        node_data.setdefault('latency', 0)
        node_data.setdefault('last_check', '')

        target_nodes[node_data['id']] = node_data
        imported += 1

    # Merge groups
    target_groups = target.setdefault('groups', {})
    for gid, gname in source.get('groups', {}).items():
        if gid not in target_groups:
            target_groups[gid] = gname

    # Preserve default node if set
    if source.get('default_node') and not target.get('default_node'):
        if source['default_node'] in target_nodes:
            target['default_node'] = source['default_node']

    # Save
    save_json(target_path, target)
    print(f'Imported: {imported} nodes')
    print(f'Skipped (duplicate): {skipped} nodes')
    print(f'Total nodes in config: {len(target_nodes)}')
    print(f'Config saved to: {target_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <source_nodes.json>')
        sys.exit(1)
    
    source = sys.argv[1]
    if not os.path.exists(source):
        print(f'Source file not found: {source}')
        sys.exit(1)
    
    merge_nodes(source)
