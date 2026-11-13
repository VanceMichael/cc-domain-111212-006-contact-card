import unittest

from sampledata import samples
from vcard import (
    parse_cards, unfold, logical_lines, split_structured, card_to_dict,
)


class UnfoldTests(unittest.TestCase):
    def test_unfold_is_byte_rule_and_keeps_content_whitespace(self):
        raw = 'FN:深圳会展\r\n 中心\r\n\t联络处\r\n'.encode()
        # CRLF 与“首个”折行 WSP 一并删除；后续 WSP 才是内容。
        self.assertEqual(unfold(raw), 'FN:深圳会展中心联络处\r\n'.encode())
        # 两个 WSP 时只删折行用的第一个。
        self.assertEqual(unfold(b'FN:a\r\n  b'), b'FN:a b')
        # 孤立 LF 后的空白不是折行，必须保留为独立物理行边界。
        raw_lf = b'FN:a\nb\n c'
        self.assertEqual(unfold(raw_lf), raw_lf)

    def test_logical_lines_preserve_raw_spans(self):
        raw = 'BEGIN:VCARD\r\nFN:长\r\n 名\r\nEND:VCARD\r\n'.encode()
        lines = logical_lines(raw)
        values = [line for line, _, _ in lines]
        self.assertEqual(values, [b'BEGIN:VCARD', b'FN:\xe9\x95\xbf\xe5\x90\x8d', b'END:VCARD'])
        # 每个逻辑行都能映射回原件字节区间，展开结果与区间内原件一致。
        for line, start, end in lines:
            segment = raw[start:end].rstrip(b'\r\n')
            self.assertEqual(line, segment.replace(b'\r\n ', b'').replace(b'\r\n\t', b''))

    def test_folded_chinese_fixture_round_trips(self):
        parsed = parse_cards(samples()['bilingual.vcf'])
        card = parsed.cards[0]
        zh_fn = next(p for p in card.get('FN') if p.language == 'zh')
        self.assertEqual(zh_fn.text, '深圳会展中心王经理联络处')


class StructureTests(unittest.TestCase):
    def test_semicolon_inside_text_is_not_a_separator(self):
        components = split_structured(r';;Road\; Building;City;;;')
        flat = [item for component in components for item in component]
        self.assertIn(r'Road\; Building', flat)
        self.assertEqual(len(components), 7)
        parsed = parse_cards(samples()['sample.vcf'])
        person = parsed.cards[1]
        address = person.get('ADR')[0]
        self.assertEqual(address.components[2], ['Road; Building'])
        self.assertEqual(address.types, ['work'])

    def test_vcard3_escaped_address_and_text_tel(self):
        parsed = parse_cards(samples()['vcard3.vcf'])
        self.assertEqual(parsed.cards[0].version, '3.0')
        card = parsed.cards[0]
        address = card.get('ADR')[0]
        self.assertEqual(address.components[2], ['园区路 9 号; 二层'])
        self.assertEqual(address.types, ['work', 'postal'])
        tel = card.get('TEL')[0]
        self.assertIsNone(tel.uri)  # 3.0 TEL 是文本而非 URI
        self.assertEqual(tel.text, '+86-571-5555-6666')
        self.assertEqual(card.get('EMAIL')[0].types, ['internet', 'pref'])

    def test_altid_language_are_variants_not_overwrites(self):
        parsed = parse_cards(samples()['bilingual.vcf'])
        card = parsed.cards[0]
        fns = card.get('FN')
        self.assertEqual(len(fns), 2)
        self.assertEqual({p.language for p in fns}, {'zh', 'en'})
        self.assertTrue(all(p.altid == 'fn1' for p in fns))
        addresses = card.get('ADR')
        self.assertEqual(len(addresses), 2)
        self.assertEqual({p.components[3][0] for p in addresses}, {'深圳', 'Shenzhen'})

    def test_params_quoted_and_type_lists(self):
        raw = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
               b'TEL;TYPE="work,voice";PREF=2;X-Esc="a;b":tel:+1\r\nEND:VCARD\r\n')
        card = parse_cards(raw).cards[0]
        tel = card.get('TEL')[0]
        self.assertEqual(tel.types, ['work', 'voice'])
        self.assertEqual(tel.pref, 2)
        self.assertEqual(tel.params['x-esc'], ['a;b'])

    def test_clientpidmap_stays_on_card_context(self):
        card = parse_cards(samples()['sample.vcf']).cards[1]
        self.assertEqual(card.clientpidmaps, [('1', 'urn:source:a')])
        tel = card.get('TEL')[0]
        self.assertEqual(tel.pids, ['1.1'])


class QuarantineTests(unittest.TestCase):
    def test_unsupported_encoding_quarantines_and_keeps_valid_card(self):
        parsed = parse_cards(samples()['unsupported-encoding.vcf'])
        self.assertEqual(len(parsed.quarantines), 2)
        reasons = {item.reason for item in parsed.quarantines}
        self.assertEqual(reasons, {'unsupported-encoding', 'unsupported-charset'})
        self.assertEqual(len(parsed.cards), 1)
        self.assertEqual(parsed.cards[0].uid, 'urn:person:5')
        # 乱码绝不进入任何属性文本。
        self.assertFalse(any('展馆'.encode('gbk') in prop.value_bytes
                             for card in parsed.cards for prop in card.properties))

    def test_invalid_utf8_is_quarantined_not_decoded_into_garbage(self):
        raw = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\nFN:\xff\xfe\r\nEND:VCARD\r\n')
        parsed = parse_cards(raw)
        self.assertEqual(parsed.quarantines[0].reason, 'invalid-utf8')
        self.assertEqual(parsed.cards, [])

    def test_malformed_and_unfinished_cards_raise(self):
        with self.assertRaises(ValueError):
            parse_cards(samples()['invalid.vcf'])
        with self.assertRaises(ValueError):
            parse_cards(b'END:VCARD\r\n')
        nested = b'BEGIN:VCARD\r\nBEGIN:VCARD\r\nEND:VCARD\r\n'
        with self.assertRaises(ValueError):
            parse_cards(nested)

    def test_unsupported_version_is_quarantined(self):
        raw = b'BEGIN:VCARD\r\nVERSION:2.1\r\nFN:old\r\nEND:VCARD\r\n'
        parsed = parse_cards(raw)
        self.assertEqual(parsed.quarantines[0].reason, 'unsupported-version')
        self.assertEqual(parsed.cards, [])

    def test_card_dict_is_serializable(self):
        parsed = parse_cards(samples()['bilingual.vcf'])
        view = card_to_dict(parsed.cards[0])
        self.assertEqual(view['uid'], 'urn:person:2')
        self.assertEqual(view['kind'], 'individual')
        self.assertIn('properties', view)

    def test_unknown_escape_is_literal_with_warning(self):
        raw = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
               b'NOTE:a\\qb\r\nN:;Jo\\,e;;;\r\nEND:VCARD\r\n')
        card = parse_cards(raw).cards[0]
        note = card.get('NOTE')[0]
        self.assertEqual(note.text, r'a\qb')
        self.assertTrue(note.warnings)
        self.assertEqual(card.get('N')[0].components[1], ['Jo,e'])

    def test_multiple_type_params_merge_and_geo_follows_version(self):
        raw = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:u\r\n'
               b'TEL;TYPE=work;TYPE=voice;TYPE=cell:tel:+1\r\n'
               b'EMAIL;TYPE="work,home":a@b.c\r\nEND:VCARD\r\n')
        card = parse_cards(raw).cards[0]
        self.assertEqual(card.get('TEL')[0].types, ['work', 'voice', 'cell'])
        self.assertEqual(card.get('EMAIL')[0].types, ['work', 'home'])
        geo4 = parse_cards(
            b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:g\r\nGEO:geo:30,120\r\nEND:VCARD\r\n'
        ).cards[0]
        self.assertEqual(geo4.get('GEO')[0].uri, 'geo:30,120')
        geo3 = parse_cards(
            b'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:g\r\nGEO:30.5;120.2\r\nEND:VCARD\r\n'
        ).cards[0]
        self.assertEqual(geo3.get('GEO')[0].components, [['30.5'], ['120.2']])

    def test_malformed_line_quarantines_single_card_only(self):
        stream = (b'BEGIN:VCARD\r\nVERSION:4.0\r\nUID:ok\r\nFN:good\r\nEND:VCARD\r\n'
                  b'BEGIN:VCARD\r\nVERSION:4.0\r\nFNnoColon\r\nEND:VCARD\r\n')
        parsed = parse_cards(stream)
        self.assertEqual([card.uid for card in parsed.cards], ['ok'])
        self.assertEqual(parsed.quarantines[0].reason, 'malformed-line')
        self.assertTrue(parsed.quarantines[0].raw.startswith(b'BEGIN:VCARD'))


if __name__ == '__main__':
    unittest.main()
