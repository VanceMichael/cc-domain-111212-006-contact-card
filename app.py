import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile

from source import inspect_bytes
from sampledata import samples
from storage import Archive, Ledger
from transport import ReplaySource, fetch
from vcard import parse_cards, card_to_dict

MAX_INPUT = 16*1024*1024

def read_input(path):
    with Path(path).open('rb') as handle:
        data = handle.read(MAX_INPUT+1)
    if len(data) > MAX_INPUT:
        raise ValueError('输入超过接收限制')
    return data

def demo():
    fixtures = samples()
    name = next(key for key in fixtures if key.startswith('sample.'))
    data = fixtures[name]
    routes = {'GET /sample':[{'base64':base64.b64encode(data).decode()}]}
    with tempfile.TemporaryDirectory() as directory, ReplaySource(routes) as server:
        status, headers, raw = fetch(server.url + '/sample')
        if status != 200:
            raise ValueError('来源请求失败')
        rows = inspect_bytes(raw)
        with Archive(Path(directory)/'archive.sqlite3') as archive:
            first = archive.save('local-demo', raw, rows)
            repeated = archive.save('local-demo', raw, rows)
            return dict(input=name, bytes=len(raw), captures=len(archive.list()),
                        same_capture=first==repeated, rows=rows, requests=len(server.requests))

def demo_merge():
    """完整演练新接收链路：回环来源 -> 字节解析 -> 合并账本 -> 屏蔽与陈旧卡保护。"""
    fixtures = samples()
    routes = {
        'GET /group': [{'base64': base64.b64encode(fixtures['forward-group.vcf']).decode()}],
        'GET /bilingual': [
            {'base64': base64.b64encode(fixtures['bilingual.vcf']).decode(),
             'chunk_bytes': 7},
        ],
        'GET /person3': [{'base64': base64.b64encode(fixtures['forward-person.vcf']).decode()}],
        'GET /source-b-old': [{'base64': base64.b64encode(fixtures['source-b.vcf']).decode()}],
        'GET /source-b-new': [{'base64': base64.b64encode(fixtures['source-b-new.vcf']).decode()}],
        'GET /v3': [{'base64': base64.b64encode(fixtures['vcard3.vcf']).decode()}],
        'GET /unsupported': [{'base64': base64.b64encode(fixtures['unsupported-encoding.vcf']).decode()}],
    }
    with tempfile.TemporaryDirectory() as directory, ReplaySource(routes) as server:
        database = Path(directory)/'ledger.sqlite3'
        order = [
            ('group', 'GET /group', 'service-desk'),
            ('bilingual', 'GET /bilingual', 'service-desk'),
            ('person3', 'GET /person3', 'service-desk'),
            ('source-b-old', 'GET /source-b-old', 'exporter-b'),
            ('v3', 'GET /v3', 'exporter-c'),
            ('unsupported', 'GET /unsupported', 'exporter-b'),
        ]
        receipts = []
        with Ledger(database) as ledger:
            for label, route, source in order:
                status, _, raw = fetch(server.url + route.split(' ', 1)[1])
                if status != 200:
                    raise ValueError('来源请求失败: ' + route)
                receipts.append({'input': label, **ledger.ingest(source, raw)})
            # 重导同一来源的旧 REV 卡：任何覆盖都必须被陈旧保护拒绝。
            stale_receipt = ledger.ingest('exporter-b', fixtures['source-b.vcf'])
            # 同来源较新 REV 先恢复到可接收状态，再人工屏蔽一个手机号。
            ledger.ingest('exporter-b', fixtures['source-b-new.vcf'])
            snapshot = ledger.load_directory()
            contact = snapshot.contacts['urn:person:2']
            cell = next(attr.attr_id for attr in contact.attributes.values()
                        if attr.property == 'TEL' and 'cell' in attr.types)
            ledger.suppress('urn:person:2', cell, '展商要求停用备用号码')
            # 重导同来源旧卡：被屏蔽属性不能因旧卡复活，只会产生 blocked 决策。
            blocked_receipt = ledger.ingest('exporter-b', fixtures['source-b.vcf'])
            final = ledger.load_directory()
            blocked = [d.to_dict() for d in final.decisions if d.action == 'suppressed-blocked']
            stale = [d.to_dict() for d in final.decisions if d.action == 'stale-rev-ignored']
            withdrawn = [d.to_dict() for d in final.decisions if d.action in
                         ('withdrawn', 'source-claim-removed')]
            groups = [{
                'uid': uid,
                'members': [{'ref': member.ref, 'status': member.status}
                            for member in data.members.values()],
            } for uid, data in final.contacts.items() if data.kind == 'group']
            quarantines = ledger.list_quarantines()
            preserved_raw = ledger.quarantine_raw(quarantines[0]['id'])
        return dict(receipts=receipts, stale_receipt=stale_receipt,
                    blocked_receipt=blocked_receipt,
                    requests=len(server.requests),
                    contacts=final.active_contacts(),
                    groups=groups,
                    blocked_decisions=blocked,
                    stale_decisions=stale,
                    withdrawal_decisions=withdrawn,
                    quarantines=quarantines,
                    quarantine_preserved=preserved_raw == fixtures['unsupported-encoding.vcf'],
                    database=str(database))

def main(argv=None):
    parser = argparse.ArgumentParser(description='来源检查与原件留存')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'capture', 'normalize'):
        command = commands.add_parser(name)
        command.add_argument('path')
        if name == 'capture':
            command.add_argument('--database', required=True)
            command.add_argument('--source', required=True)
    ingest = commands.add_parser('ingest')
    ingest.add_argument('path')
    ingest.add_argument('--database', required=True)
    ingest.add_argument('--source', required=True)
    contacts = commands.add_parser('contacts')
    contacts.add_argument('--database', required=True)
    decisions = commands.add_parser('decisions')
    decisions.add_argument('--database', required=True)
    groups = commands.add_parser('groups')
    groups.add_argument('--database', required=True)
    qlist = commands.add_parser('quarantine-list')
    qlist.add_argument('--database', required=True)
    qraw = commands.add_parser('quarantine-raw')
    qraw.add_argument('--database', required=True)
    qraw.add_argument('quarantine_id', type=int)
    qraw.add_argument('output')
    suppress = commands.add_parser('suppress')
    suppress.add_argument('--database', required=True)
    suppress.add_argument('--uid', required=True)
    suppress.add_argument('--attr-id', required=True)
    suppress.add_argument('--reason', required=True)
    unsuppress = commands.add_parser('unsuppress')
    unsuppress.add_argument('--database', required=True)
    unsuppress.add_argument('--uid', required=True)
    unsuppress.add_argument('--attr-id', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--database', required=True)
    fixtures = commands.add_parser('fixtures')
    fixtures.add_argument('directory')
    commands.add_parser('demo')
    commands.add_parser('demo-merge')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            result = demo()
        elif args.command == 'demo-merge':
            result = demo_merge()
        elif args.command == 'fixtures':
            destination = Path(args.directory)
            destination.mkdir(parents=True, exist_ok=True)
            for name, raw in samples().items():
                path = destination/name
                if path.exists():
                    raise ValueError('不覆盖已有夹具文件：'+name)
                path.write_bytes(raw)
            result = dict(files=sorted(samples()))
        elif args.command == 'list':
            with Archive(args.database) as archive:
                result = archive.list()
        elif args.command == 'normalize':
            raw = read_input(args.path)
            parsed = parse_cards(raw)
            result = {'cards': [card_to_dict(card) for card in parsed.cards],
                      'quarantines': [
                          {'index': item.index, 'reason': item.reason, 'detail': item.detail,
                           'line_no': item.line_no, 'digest': item.digest}
                          for item in parsed.quarantines]}
        elif args.command == 'inspect':
            result = inspect_bytes(read_input(args.path))
        elif args.command == 'capture':
            raw = read_input(args.path)
            rows = inspect_bytes(raw)
            with Archive(args.database) as archive:
                result = dict(capture_id=archive.save(args.source, raw, rows), rows=len(rows))
        elif args.command == 'ingest':
            with Ledger(args.database) as ledger:
                result = ledger.ingest(args.source, read_input(args.path))
        elif args.command == 'contacts':
            with Ledger(args.database) as ledger:
                result = ledger.load_directory().active_contacts()
        elif args.command == 'decisions':
            with Ledger(args.database) as ledger:
                result = [item.to_dict() for item in ledger.load_directory().decisions]
        elif args.command == 'groups':
            with Ledger(args.database) as ledger:
                result = [{
                    'uid': uid, 'kind': contact.kind,
                    'members': [member.to_dict() for member in contact.members.values()],
                } for uid, contact in ledger.load_directory().contacts.items()
                    if contact.kind == 'group']
        elif args.command == 'quarantine-list':
            with Ledger(args.database) as ledger:
                result = ledger.list_quarantines()
        elif args.command == 'quarantine-raw':
            with Ledger(args.database) as ledger:
                raw = ledger.quarantine_raw(args.quarantine_id)
            Path(args.output).write_bytes(raw)
            result = dict(quarantine_id=args.quarantine_id, bytes=len(raw), output=args.output)
        elif args.command == 'suppress':
            with Ledger(args.database) as ledger:
                result = ledger.suppress(args.uid, args.attr_id, args.reason)
        elif args.command == 'unsuppress':
            with Ledger(args.database) as ledger:
                result = ledger.unsuppress(args.uid, args.attr_id)
        else:  # pragma: no cover - argparse 已限定命令集合
            raise ValueError('未知命令: ' + str(args.command))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__, reason=str(error)), ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
