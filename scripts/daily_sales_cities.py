"""Business-approved Excel mappings; one group per unique cloud warehouse code."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def policy():
    rules = json.loads((ROOT / 'config/daily_sales_warehouse_mapping.json').read_text(encoding='utf-8'))
    if rules.get('mode') != 'explicit-excel-cloud-code' or not rules.get('entries'):
        raise ValueError('An approved explicit warehouse mapping is required')
    platforms, groups, codes = set(), {}, {}
    for entry in rules['entries']:
        platform = entry['platform_warehouse']
        group, code, name = entry['group_name'], entry['cloud_code'], entry['cloud_name']
        if not all(isinstance(v, str) and v.strip() for v in (platform, group, code, name)) or platform in platforms:
            raise ValueError('Invalid or duplicate warehouse mapping')
        platforms.add(platform)
        if group in groups and groups[group] != (code, name):
            raise ValueError('One group cannot use multiple cloud warehouses')
        if code in codes and codes[code] != group:
            raise ValueError('One cloud warehouse cannot supply multiple independent alert groups')
        groups[group], codes[code] = (code, name), group
    return rules


def warehouse_city(name, rules=None):
    rules = rules or policy()
    text = (name or '').strip()
    for entry in rules['entries']:
        if entry['platform_warehouse'] == text:
            return entry['group_name']
    # A new numbered warehouse is not implicitly authorized by a similar name.
    return f'{text}（未配置）'


def cloud_city(name, cities, rules=None):
    rules = rules or policy()
    matches = {e['group_name'] for e in rules['entries'] if e['cloud_name'] == name and e['group_name'] in cities}
    return next(iter(matches)) if len(matches) == 1 else None


def configured_clouds(groups, rules=None):
    rules = rules or policy()
    by_group = {e['group_name']: (e['cloud_code'], e['cloud_name']) for e in rules['entries']}
    return {group: by_group.get(group) for group in groups}
