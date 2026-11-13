import tempfile
import unittest
from pathlib import Path

from sampledata import samples
from vcard import parse_cards
from directory import Directory, value_key, parse_rev, normalize_member_ref
from storage import Ledger


def cards_of(name):
    return parse_cards(samples()[name]).cards


def attr_ids(contact, property_name):
    return {attr.attr_id for attr in contact.attributes.values()
            if attr.property == property_name}


class MergeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = Directory()
        self.bilingual = cards_of('bilingual.vcf')[0]
        self.group = cards_of('forward-group.vcf')[0]
        self.person3 = cards_of('forward-person.vcf')[0]

    def ingest(self, card, source):
        self.directory.ingest_card(card, source=source, capture_id=1)

    def test_forward_member_reference_resolves_later(self):
        self.ingest(self.group, 'service-desk')
        group = self.directory.contacts['urn:group:forward']
        self.assertEqual(group.kind, 'group')
        self.assertTrue(all(member.status == 'pending'
                            for member in group.members.values()))
        self.ingest(self.bilingual, 'service-desk')
        self.assertEqual(group.members['urn:person:2'].status, 'resolved')
        self.assertEqual(group.members['urn:person:3'].status, 'pending')
        self.ingest(self.person3, 'service-desk')
        self.assertEqual(group.members['urn:person:3'].status, 'resolved')
        actions = [d.action for d in self.directory.decisions]
        self.assertIn('member-resolved', actions)
        # 人和组始终是不同 KIND 的联系人。
        self.assertEqual(self.directory.contacts['urn:person:2'].kind, 'individual')

    def test_language_variants_remain_distinct_attributes(self):
        self.ingest(self.bilingual, 'service-desk')
        contact = self.directory.contacts['urn:person:2']
        fn_attributes = [attr for attr in contact.attributes.values()
                         if attr.property == 'FN']
        self.assertEqual(len(fn_attributes), 2)
        self.assertEqual({attr.language for attr in fn_attributes}, {'zh', 'en'})
        self.assertEqual({attr.display for attr in fn_attributes},
                         {'深圳会展中心王经理联络处', 'Manager Wang'})
        families = {attr.family for attr in fn_attributes}
        self.assertEqual(len(families), 1)  # 同一 ALTID 家族

    def test_duplicate_tel_merges_types_pref_and_keeps_sources(self):
        self.ingest(self.bilingual, 'service-desk')
        source_b = cards_of('source-b.vcf')[0]
        self.ingest(source_b, 'exporter-b')
        contact = self.directory.contacts['urn:person:2']
        work_tels = [attr for attr in contact.attributes.values()
                     if attr.property == 'TEL' and value_key(
                         next(p for p in self.bilingual.get('TEL') if 'work' in p.types))
                     == attr.value_key]
        self.assertEqual(len(work_tels), 1)
        tel = work_tels[0]
        self.assertEqual(set(tel.types), {'work', 'voice', 'fax'})
        self.assertEqual(tel.pref, 1)
        self.assertEqual(set(tel.sources), {'service-desk', 'exporter-b'})
        # 来源级声明各自保留行号与原始值。
        self.assertTrue(all(claim.line_no for claim in tel.sources.values()))

    def test_stale_rev_cannot_overwrite_existing_values(self):
        self.ingest(self.bilingual, 'service-desk')
        source_b = cards_of('source-b.vcf')[0]
        self.ingest(source_b, 'exporter-b')
        contact = self.directory.contacts['urn:person:2']
        before = {attr.attr_id: attr.display for attr in contact.attributes.values()}
        # 再次重导同一张更旧的卡。
        self.ingest(source_b, 'exporter-b')
        after = {attr.attr_id: attr.display for attr in contact.attributes.values()}
        self.assertEqual(before, after)
        self.assertTrue(any(d.action == 'stale-rev-ignored'
                            for d in self.directory.decisions))

    def test_manual_suppression_survives_reimport(self):
        self.ingest(self.bilingual, 'service-desk')
        newer = cards_of('source-b-new.vcf')[0]
        self.ingest(newer, 'exporter-b')
        contact = self.directory.contacts['urn:person:2']
        cell = next(attr.attr_id for attr in contact.attributes.values()
                    if attr.property == 'TEL' and 'cell' in attr.types)
        self.directory.suppress('urn:person:2', cell, '停用备用号码')
        # 旧卡重导不能复活；新卡重导同样被属性级屏蔽拦截。
        self.ingest(cards_of('source-b.vcf')[0], 'exporter-b')
        self.ingest(newer, 'exporter-b')
        contact = self.directory.contacts['urn:person:2']
        self.assertEqual(contact.attributes[cell].status, 'suppressed')
        self.assertEqual(contact.attributes[cell].suppress_reason, '停用备用号码')
        blocked = [d for d in self.directory.decisions if d.action == 'suppressed-blocked']
        self.assertTrue(blocked)
        # 人工解除后才能恢复。
        self.directory.unsuppress('urn:person:2', cell)
        self.assertEqual(contact.attributes[cell].status, 'active')

    def test_new_rev_withdraws_only_source_owned_attributes(self):
        self.ingest(self.bilingual, 'service-desk')
        self.ingest(cards_of('source-b.vcf')[0], 'exporter-b')
        contact = self.directory.contacts['urn:person:2']
        note = next(iter(attr_ids(contact, 'NOTE')))
        shared_fn = next(attr for attr in contact.attributes.values()
                         if attr.property == 'FN' and attr.language == 'zh')
        self.ingest(cards_of('source-b-new.vcf')[0], 'exporter-b')
        self.assertEqual(contact.attributes[note].status, 'withdrawn')
        # FN 是 service-desk 仍持有的属性，属性保留；且 exporter-b 的 FN 与现值冲突，
        # 其来源声明作为冲突审计证据保留，显示值仍以 service-desk 为准。
        self.assertEqual(shared_fn.status, 'active')
        self.assertEqual(shared_fn.display, '深圳会展中心王经理联络处')
        self.assertIn('service-desk', shared_fn.sources)
        self.assertIn('exporter-b', shared_fn.sources)
        self.assertTrue(shared_fn.conflicts)
        self.assertTrue(any(d.action == 'withdrawn' for d in self.directory.decisions))
        self.assertTrue(any(d.action == 'source-claim-removed'
                            for d in self.directory.decisions))

    def test_cross_source_variant_conflict_keeps_existing_value(self):
        first = ('BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
                 'FN;ALTID=1;LANGUAGE=zh:甲\r\nEND:VCARD\r\n').encode()
        second = ('BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
                  'FN;ALTID=1;LANGUAGE=zh:乙\r\nEND:VCARD\r\n').encode()
        directory = Directory()
        directory.ingest_card(parse_cards(first).cards[0], 'a', 1)
        directory.ingest_card(parse_cards(second).cards[0], 'b', 2)
        attr = next(attr for attr in directory.contacts['u'].attributes.values()
                    if attr.property == 'FN')
        self.assertEqual(attr.display, '甲')  # 后到来源不覆盖现值
        self.assertEqual(len(attr.conflicts), 1)
        self.assertEqual(attr.conflicts[0]['source'], 'b')
        self.assertEqual(attr.conflicts[0]['raw_value'], '乙')
        self.assertEqual(set(attr.sources), {'a', 'b'})  # 双方 provenance 都在
        self.assertTrue(any(d.action == 'variant-conflict'
                            for d in directory.decisions))
        # 同来源后来的新 REV 卡缺失该属性，冲突证据仍保留。
        newer = b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nREV:20260202T000000Z\r\nEND:VCARD\r\n'
        directory.ingest_card(parse_cards(newer).cards[0], 'b', 3)
        self.assertIn('b', attr.sources)
        self.assertEqual(attr.display, '甲')

    def test_pid_does_not_define_cross_source_identity(self):
        first = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
                 b'TEL;PID=1.1:tel:+1\r\nEND:VCARD\r\n')
        second = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
                  b'TEL;PID=1.1:tel:+2\r\nEND:VCARD\r\n')
        directory = Directory()
        directory.ingest_card(parse_cards(first).cards[0], 'a', 1)
        directory.ingest_card(parse_cards(second).cards[0], 'b', 2)
        contact = directory.contacts['u']
        tels = [attr for attr in contact.attributes.values() if attr.property == 'TEL']
        self.assertEqual(len(tels), 2)  # 同 PID 不同值不会互相覆盖
        self.assertEqual(tels[0].pids_by_source, {'a': ['1.1']})
        self.assertEqual(tels[1].pids_by_source, {'b': ['1.1']})

    def test_member_on_person_is_rejected_and_kinds_never_mix(self):
        person = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nFN:x\r\n'
                  b'MEMBER:urn:person:9\r\nEND:VCARD\r\n')
        directory = Directory()
        directory.ingest_card(parse_cards(person).cards[0], 's', 1)
        self.assertEqual(directory.contacts['u'].kind, 'individual')
        self.assertEqual(directory.contacts['u'].members, {})
        self.assertTrue(any(d.action == 'member-on-non-group'
                            for d in directory.decisions))
        group = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nKIND:group\r\n'
                 b'FN:g\r\nEND:VCARD\r\n')
        directory.ingest_card(parse_cards(group).cards[0], 's', 2)
        self.assertTrue(any(d.action == 'kind-conflict'
                            for d in directory.decisions))
        self.assertEqual(directory.contacts['u'].kind, 'individual')

    def test_rev_parsing_and_member_normalization(self):
        self.assertEqual(parse_rev('20260101T000000Z').year, 2026)
        self.assertEqual(parse_rev('2026-03-03T08:30:00Z').month, 3)
        self.assertIsNone(parse_rev('not-a-date'))
        self.assertEqual(normalize_member_ref('URN:UUID:ABC'), 'urn:uuid:abc')

    def test_same_rev_reimport_is_stale_and_without_rev_never_withdraws(self):
        first = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nNOTE:keep\r\n'
                 b'REV:20260101T000000Z\r\nEND:VCARD\r\n')
        directory = Directory()
        directory.ingest_card(parse_cards(first).cards[0], 's', 1)
        directory.ingest_card(parse_cards(first).cards[0], 's', 1)
        self.assertTrue(any(d.action == 'stale-rev-ignored'
                            for d in directory.decisions))
        no_rev = b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nEND:VCARD\r\n'
        directory.ingest_card(parse_cards(no_rev).cards[0], 's', 2)
        contact = directory.contacts['u']
        self.assertTrue(any(attr.property == 'NOTE' for attr in contact.attributes.values()))
        self.assertFalse(any(d.action == 'withdrawn' for d in directory.decisions))

    def test_withdrawn_attribute_restores_when_source_redeclares(self):
        first = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nNOTE:x\r\n'
                 b'REV:20260101T000000Z\r\nEND:VCARD\r\n')
        second = b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nREV:20260202T000000Z\r\nEND:VCARD\r\n'
        third = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nNOTE:x\r\n'
                 b'REV:20260303T000000Z\r\nEND:VCARD\r\n')
        directory = Directory()
        for raw in (first, second, third):
            directory.ingest_card(parse_cards(raw).cards[0], 's', 1)
        note = next(attr for attr in directory.contacts['u'].attributes.values()
                    if attr.property == 'NOTE')
        self.assertEqual(note.status, 'active')
        actions = [d.action for d in directory.decisions]
        self.assertIn('withdrawn', actions)
        self.assertIn('restored', actions)


class LedgerPersistenceTests(unittest.TestCase):
    def test_ingest_quarantine_suppression_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'ledger.sqlite3'
            with Ledger(path) as ledger:
                ledger.ingest('service-desk', samples()['forward-group.vcf'])
                ledger.ingest('service-desk', samples()['bilingual.vcf'])
                ledger.ingest('exporter-b', samples()['source-b.vcf'])
                receipt = ledger.ingest('exporter-b', samples()['unsupported-encoding.vcf'])
                self.assertEqual(len(receipt['quarantines']), 2)
                contact = ledger.load_directory().contacts['urn:person:2']
                cell = next(attr.attr_id for attr in contact.attributes.values()
                            if attr.property == 'TEL' and 'cell' in attr.types)
                ledger.suppress('urn:person:2', cell, '停用')
                quarantined = ledger.list_quarantines()
                self.assertEqual({item['reason'] for item in quarantined},
                                 {'unsupported-encoding', 'unsupported-charset'})
                # 原件字节完整可导出，且与输入一致。
                self.assertEqual(ledger.quarantine_raw(quarantined[0]['id']),
                                 samples()['unsupported-encoding.vcf'])
            # 重新打开数据库：屏蔽状态、决策与成员解析全部持久。
            with Ledger(path) as ledger:
                reloaded = ledger.load_directory()
                contact = reloaded.contacts['urn:person:2']
                self.assertTrue(any(attr.status == 'suppressed'
                                    for attr in contact.attributes.values()))
                group = reloaded.contacts['urn:group:forward']
                self.assertEqual(group.members['urn:person:2'].status, 'resolved')
                self.assertTrue(reloaded.decisions)
                self.assertTrue(reloaded.quarantines)

    def test_duplicate_raw_keeps_one_capture_but_decisions_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'store.sqlite3'
            with Ledger(path) as ledger:
                first = ledger.ingest('s', samples()['forward-person.vcf'])
                second = ledger.ingest('s', samples()['forward-person.vcf'])
                self.assertEqual(first['capture_id'], second['capture_id'])
                self.assertGreaterEqual(
                    len(ledger.load_directory().decisions), 2)


if __name__ == '__main__':
    unittest.main()
