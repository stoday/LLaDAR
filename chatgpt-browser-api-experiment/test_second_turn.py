import json
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit

from second_turn import assemble_browser_second, followup_request, updated_state


class SecondTurnTests(unittest.TestCase):
    def test_browser_packet_is_reencoded_without_changing_fresh_credentials(self):
        fields={'conversationState':json.dumps({'backendConversationId':'conversation','parentMessageId':'parent'}),
            'prompt':'秋天天氣真好','userMessageId':'user','assistantMessageId':'pending-assistant',
            'chatRequirementsToken':'private-requirements','proofToken':'private-proof',
            'turnstileToken':'private-turnstile','oai-session-id':'session','futureField':'preserve-me'}
        packet=dict(url='https://chatgpt.com/unauth-mweb/conversation/updates',
            body=urlencode(fields).encode(),headers={':authority':'chatgpt.com','cookie':'private-cookie',
                'content-length':'10','content-type':'application/x-www-form-urlencoded'})
        result=assemble_browser_second(packet,'秋天天氣真好')
        self.assertEqual(dict(parse_qsl(result['body'].decode())),fields)
        self.assertNotIn(':authority',result['headers'])
        self.assertNotIn('cookie',result['headers'])
        self.assertEqual(packet['headers']['cookie'],'private-cookie')

    def test_incomplete_browser_packet_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'missing required'):
            assemble_browser_second(dict(body=b'prompt=test',headers={},url='https://chatgpt.com'),'test')

    def test_returned_safety_is_merged_with_latest_state(self):
        state=updated_state([
            {'type':'state','conversationState':'{"parentMessageId":"old"}','safety':{}},
            {'type':'state','conversationState':'{"parentMessageId":"new"}',
             'safety':{'dismissedInterventionIds':[]}},
        ])
        self.assertEqual(state['parentMessageId'],'new')
        self.assertEqual(state['safety'],{'dismissedInterventionIds':[]})

    def test_followup_uses_pending_id_and_cookie_jar(self):
        first=dict(method='POST',url='https://chatgpt.com/unauth-mweb/conversation/updates?operationId=old',
            headers={'cookie':'private-cookie','content-length':'10','x-oai-turn-trace-id':'old'},
            body=urlencode({'conversationState':'{}','prompt':'嗨','userMessageId':'old',
                            'assistantMessageId':'old','chatRequirementsToken':'private-token'}).encode())
        second=followup_request(first,{'parentMessageId':'returned-id'},'秋天天氣真好')
        fields=dict(parse_qsl(second['body'].decode()))
        self.assertTrue(fields['assistantMessageId'].startswith('pending-'))
        self.assertEqual(fields['prompt'],'秋天天氣真好')
        self.assertEqual(json.loads(fields['conversationState'])['parentMessageId'],'returned-id')
        self.assertNotIn('cookie',second['headers'])
        self.assertNotIn('content-length',second['headers'])
        self.assertNotEqual(dict(parse_qsl(urlsplit(second['url']).query))['operationId'],'old')
        self.assertEqual(first['headers']['cookie'],'private-cookie')


if __name__=='__main__':
    unittest.main()
