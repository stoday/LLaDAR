import json
import unittest

from compare_turns import compare_fields, cookies


class CredentialComparisonTests(unittest.TestCase):
    def test_values_do_not_appear_in_saved_comparison(self):
        comparison = compare_fields({'token': 'secret-first-value'}, {'token': 'secret-second-value'})
        encoded = json.dumps(comparison)
        self.assertNotIn('secret-first-value', encoded)
        self.assertNotIn('secret-second-value', encoded)
        self.assertFalse(comparison['token']['same'])

    def test_absent_header_is_distinct_from_unchanged_empty_header(self):
        comparison = compare_fields({'conduit': 'token', 'empty': ''}, {'empty': ''})
        self.assertFalse(comparison['conduit']['second_present'])
        self.assertFalse(comparison['conduit']['same'])
        self.assertTrue(comparison['empty']['same'])
        self.assertTrue(comparison['empty']['first_empty'])

    def test_cookie_comparison_is_independent_of_header_order(self):
        comparison = compare_fields(cookies('a=first; b=second'), cookies('b=second; a=first'))
        self.assertTrue(all(v['same'] for v in comparison.values()))


if __name__ == '__main__':
    unittest.main()
