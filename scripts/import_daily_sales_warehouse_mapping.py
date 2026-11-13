"""Import the business-approved workbook; validate names against read-only master data."""
import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_pairs(path):
    """Read the two business columns only, never execute workbook formulas or notes."""
    with zipfile.ZipFile(path) as book:
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//s:t', NS))
                       for si in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        sheet = workbook.find('s:sheets/s:sheet', NS)
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        pairs = []
        header = None
        for row in ET.fromstring(book.read(member)).findall('s:sheetData/s:row', NS):
            values = {}
            for cell in row.findall('s:c', NS):
                col = re.sub(r'\d', '', cell.get('r'))
                if col not in {'A', 'B'}:
                    continue
                if cell.find('s:f', NS) is not None:
                    raise ValueError('Mapping cells must contain explicit business values, not formulas')
                raw = cell.findtext('s:v', '', NS)
                value = strings[int(raw)] if cell.get('t') == 's' else (
                    ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    if cell.get('t') == 'inlineStr' else raw)
                values[col] = value.strip()
            if header is None:
                if not any(values.values()):
                    continue
                if values != {'A': '多多后台名称', 'B': '对应云仓'}:
                    raise ValueError('Expected columns: 多多后台名称, 对应云仓')
                header = values
                continue
            platform, cloud = values.get('A', ''), values.get('B', '')
            if pairs and int(row.get('r')) > pairs[-1][2] + 1:
                break  # Empty separator rows may be omitted from the sheet XML.
            if not platform and not cloud:
                break  # Explanatory notes after the business table are not mappings.
            if not platform or not cloud or not platform.endswith('仓'):
                raise ValueError(f'Incomplete or unexpected mapping at Excel row {row.get("r")}')
            pairs.append((platform, cloud, int(row.get('r'))))
        if not pairs:
            raise ValueError('The workbook contains no warehouse mappings')
        return pairs, sheet.get('name')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook', type=Path)
    args = parser.parse_args()
    raw = args.workbook.read_bytes()
    pairs, sheet = read_pairs(args.workbook)
    from scripts import query_inventory_web as inventory
    inventory._load_env_file(inventory.AGENT_ENV_PATH)
    connection = inventory._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT wms_code,wms_desc FROM ywzt_ods.master_wms WHERE wms_type=12 AND wms_status<>12')
            names = defaultdict(list)
            for row in cursor.fetchall():
                names[row['wms_desc']].append(str(row['wms_code']))
    finally:
        connection.close()
    aliases = {'北京通州': '北京', '上海浦东': '上海', '成都青白江': '成都', '长沙开福': '长沙', '德州齐河': '德州'}
    entries, seen = [], set()
    cities = defaultdict(set)
    for platform, cloud, row in pairs:
        if platform in seen:
            raise ValueError(f'Duplicate platform warehouse: {platform}')
        seen.add(platform)
        if len(names[cloud]) != 1:
            raise ValueError(f'Cloud name must have exactly one active code: {cloud}')
        city = re.sub(r'(?:中心|物流)*[0-9０-９]+仓$', '', platform)
        city = aliases.get(city, city)
        code = names[cloud][0]
        entries.append({'platform_warehouse': platform, 'cloud_code': code, 'cloud_name': cloud, 'excel_row': row})
        cities[code].add(city)
    for entry in entries:
        labels = sorted(cities[entry['cloud_code']], key=lambda c: (c not in entry['cloud_name'], c))
        entry['group_name'] = ' / '.join(labels)
    config = {'version': 3, 'mode': 'explicit-excel-cloud-code',
              'source': {'file': str(args.workbook.resolve()), 'sheet': sheet,
                         'sha256': hashlib.sha256(raw).hexdigest(),
                         'imported_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')},
              'entries': entries}
    dest = ROOT / 'config/daily_sales_warehouse_mapping.json'
    backup = ROOT / 'output/daily-sales/warehouse-mapping-before-excel.json'
    if dest.exists() and not backup.exists():
        backup.write_bytes(dest.read_bytes())
    temp = dest.with_suffix('.json.tmp')
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(dest)
    print(json.dumps({'imported': len(entries), 'cloud_warehouses': len(cities),
                      'source_sha256': config['source']['sha256'], 'config': str(dest)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
