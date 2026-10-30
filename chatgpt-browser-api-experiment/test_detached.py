import unittest

from detached import reconstruct_answer


class DetachedProtocolTests(unittest.TestCase):
    def test_complete_empty_delta_preserves_full_answer(self):
        text, complete = reconstruct_answer([
            {'type': 'content', 'messageId': 'a', 'mode': 'replace', 'revision': 1,
             'status': 'streaming', 'markdown': '嗨'},
            {'type': 'content', 'messageId': 'a', 'mode': 'append', 'revision': 2,
             'baseRevision': 1, 'status': 'streaming', 'markdown': '！'},
            {'type': 'content', 'messageId': 'a', 'mode': 'append', 'revision': 3,
             'baseRevision': 2, 'status': 'complete', 'markdown': ''},
        ])
        self.assertEqual(text, '嗨！')
        self.assertTrue(complete)

    def test_replace_discards_old_text(self):
        text, complete = reconstruct_answer([
            {'type': 'content', 'messageId': 'a', 'mode': 'replace', 'revision': 1,
             'status': 'streaming', 'markdown': '舊文字'},
            {'type': 'content', 'messageId': 'a', 'mode': 'replace', 'revision': 2,
             'status': 'complete', 'markdown': '新文字'},
        ])
        self.assertEqual(text, '新文字')
        self.assertTrue(complete)

    def test_missing_delta_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'reordered'):
            reconstruct_answer([
                {'type': 'content', 'messageId': 'a', 'mode': 'replace', 'revision': 1,
                 'status': 'streaming', 'markdown': '嗨'},
                {'type': 'content', 'messageId': 'a', 'mode': 'append', 'revision': 3,
                 'baseRevision': 2, 'status': 'complete', 'markdown': '！'},
            ])

    def test_end_without_complete_is_not_an_answer_completion(self):
        text, complete = reconstruct_answer([
            {'type': 'content', 'messageId': 'a', 'mode': 'replace', 'revision': 1,
             'status': 'streaming', 'markdown': '尚未完成'},
            {'type': 'end'},
        ])
        self.assertEqual(text, '尚未完成')
        self.assertFalse(complete)


if __name__ == '__main__':
    unittest.main()
