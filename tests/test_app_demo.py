import json
import unittest

import app
from sampledata import samples


class MergeDemoTests(unittest.TestCase):
    def test_demo_merge_exercises_real_loopback_and_full_pipeline(self):
        report = app.demo_merge()
        # 所有演练夹具都经回环 HTTP 实际取回。
        self.assertEqual(report['requests'], 6)
        self.assertTrue(all(receipt['cards'] >= 1 for receipt in report['receipts']))
        # 隔离件保留原始字节，不产出联系人姓名。
        self.assertTrue(report['quarantine_preserved'])
        self.assertEqual({item['reason'] for item in report['quarantines']},
                         {'unsupported-encoding', 'unsupported-charset'})
        # 前向引用全部补齐，组与人分清。
        self.assertTrue(report['groups'])
        self.assertTrue(all(member['status'] == 'resolved'
                            for group in report['groups']
                            for member in group['members']))
        # 陈旧 REV 与人工屏蔽两条保护路径都留下可检查决策。
        self.assertTrue(report['stale_decisions'])
        self.assertTrue(report['blocked_decisions'])
        self.assertTrue(any(item['action'] in ('withdrawn', 'source-claim-removed')
                            for item in report['withdrawal_decisions']))
        # 双语变体并列存在。
        person2 = next(contact for contact in report['contacts']
                       if contact['uid'] == 'urn:person:2')
        fn_languages = {attr['language'] for attr in person2['attributes']
                        if attr['property'] == 'FN'}
        self.assertEqual(fn_languages, {'zh', 'en'})
        # 报告必须是可 JSON 检查的。
        json.dumps(report, ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
