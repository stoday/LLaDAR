import unittest

from replay import parse_stream


class ProtocolTests(unittest.TestCase):
    def test_completed_stream(self):
        self.assertEqual(parse_stream('{"type":"content"}\n{"type":"end"}\n')[-1]['type'], 'end')

    def test_interrupted_stream_is_not_complete(self):
        with self.assertRaisesRegex(RuntimeError, 'no final end'):
            parse_stream('{"type":"content"}\n')

    def test_observed_failure_is_not_complete_even_with_end(self):
        with self.assertRaisesRegex(RuntimeError, 'source_submission_unavailable'):
            parse_stream('{"type":"diagnostic","kind":"source_submission_unavailable"}\n{"type":"end"}\n')


if __name__ == '__main__':
    unittest.main()
