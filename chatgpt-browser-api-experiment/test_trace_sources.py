import unittest

from trace_sources import scalars, safe_stack, static_flow_evidence


class SourceTraceTests(unittest.TestCase):
    def test_nested_serialized_json_retains_source_path(self):
        found = list(scalars({'state': '{"parent":"private-id"}'}))
        self.assertIn(('$.state<decoded-json>.parent', 'private-id'), found)

    def test_call_stack_does_not_emit_url_query_secrets(self):
        stack = 'at f (https://chatgpt.com/assets/app.js:1:20)\nat g (https://chatgpt.com/a.js?token=private-value:1:30)'
        result = safe_stack(stack)
        self.assertEqual(result, [('/assets/app.js', '1', '20')])
        self.assertNotIn('private-value', str(result))

    def test_code_evidence_is_labeled_with_asset_and_offset(self):
        evidence = static_flow_evidence({'/assets/sentinel-x.js':'prefix chatRequirementsToken:c.token suffix'})
        self.assertEqual(evidence[0]['field'], 'chatRequirementsToken')
        self.assertEqual(evidence[0]['offset'], 7)


if __name__=='__main__':
    unittest.main()
