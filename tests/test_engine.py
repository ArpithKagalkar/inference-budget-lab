import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from lab.dataset import fixtures, score, difficulty
from lab.engine import Config, evaluate, live_call, live_ready, run_experiment

class EngineTests(unittest.TestCase):
    def test_reproducible_and_no_identical_prompts_across_splits(self):
        first = run_experiment(Config(requests=80))
        self.assertEqual(first, run_experiment(Config(requests=80)))
        validation = {r['text'] for r in fixtures('validation', 120)}
        self.assertTrue(validation.isdisjoint(r['text'] for r in fixtures('test',80)))

    def test_validation_selection_does_not_depend_on_test_size(self):
        a = run_experiment(Config(requests=20, validation_requests=30))
        b = run_experiment(Config(requests=200, validation_requests=30))
        self.assertEqual(a['calibration'], b['calibration'])
        self.assertEqual(a['threshold'], b['threshold'])

    def test_strict_schema_and_exact_match(self):
        expected = {'product':'Atlas','category':'billing','urgency':'high'}
        self.assertTrue(score(expected, expected)['correct'])
        for output in (None, [], 'billing', {**expected, 'extra':True}, {**expected,'category':'other'}):
            self.assertFalse(score(output, expected)['valid'])
            self.assertFalse(score(output, expected)['correct'])
        result = score({**expected,'urgency':'low'},expected)
        self.assertTrue(result['valid'])
        self.assertFalse(result['correct'])
        self.assertEqual(sum(result['fields'].values()),2)

    def test_queue_growth_and_cost_invariance(self):
        rows = fixtures('test',80)
        quiet, _ = evaluate(rows,'strong',0,Config(rps=1,concurrency=1))
        loaded, records = evaluate(rows,'strong',0,Config(rps=20,concurrency=1))
        self.assertGreater(loaded['p95_ms'],quiet['p95_ms']*5)
        self.assertEqual(quiet['total_cost'],loaded['total_cost'])
        self.assertTrue(all(r['latency_ms'] >= r['service_ms'] for r in records))

    def test_impossible_latency_is_not_accepted(self):
        result = run_experiment(Config(latency_slo_ms=100))
        self.assertFalse(result['validation_feasible'])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['threshold'],0)

    def test_invalid_configuration(self):
        for value in ({'requests':0},{'rps':float('nan')},{'seed':True},{'unknown':1},[],{'concurrency':33}):
            with self.assertRaises(ValueError):
                Config.parse(value)

    def test_router_uses_text_without_labels(self):
        self.assertEqual(difficulty('Atlas is broken.'),.15)
        self.assertEqual(difficulty('Previously reported this.'),.55)
        self.assertEqual(difficulty('However, billing is still broken.'),.9)

class MockProvider(BaseHTTPRequestHandler):
    include_usage = True
    response_content = '{"product":"Atlas","category":"billing","urgency":"high"}'
    def log_message(self,*args):
        pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['model']=='mock-model'
        assert self.path=='/v1/chat/completions'
        payload={'choices':[{'message':{'content':self.response_content}}]}
        if self.include_usage:
            payload['usage']={'prompt_tokens':100,'completion_tokens':20}
        data=json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)

class LiveAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),MockProvider)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockProvider.include_usage=True
        MockProvider.response_content='{"product":"Atlas","category":"billing","urgency":"high"}'
        env={}
        for tier in ('ECONOMY','STRONG'):
            env.update({f'{tier}_BASE_URL':f'http://127.0.0.1:{self.server.server_port}/v1',
                        f'{tier}_MODEL':'mock-model',f'{tier}_INPUT_PER_MILLION':'1',
                        f'{tier}_OUTPUT_PER_MILLION':'2'})
        self.env=patch.dict(os.environ,env)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_usage_drives_cost(self):
        self.assertTrue(live_ready())
        result=live_call({'text':'test'},'economy')
        self.assertTrue(result['cost_known'])
        self.assertAlmostEqual(result['cost'],.00014)
        self.assertIsNone(result['error'])

    def test_missing_usage_is_not_free(self):
        MockProvider.include_usage=False
        result=live_call({'text':'test'},'economy')
        self.assertFalse(result['cost_known'])

    def test_compute_time_cost_does_not_require_usage(self):
        MockProvider.include_usage=False
        with patch.dict(os.environ, {'ECONOMY_INPUT_PER_MILLION':'',
                                     'ECONOMY_OUTPUT_PER_MILLION':'',
                                     'ECONOMY_HOURLY_COST':'1.5'}):
            result=live_call({'text':'test'},'economy')
        self.assertTrue(result['cost_known'])
        self.assertEqual(result['cost_basis'],'compute_time')
        self.assertAlmostEqual(result['cost'],result['service_ms']/3_600_000*1.5)

    def test_invalid_json_keeps_billed_tokens(self):
        MockProvider.response_content='not json'
        result=live_call({'text':'test'},'economy')
        self.assertEqual(result['error'],'Invalid model JSON response')
        self.assertAlmostEqual(result['cost'],.00014)

if __name__=='__main__':
    unittest.main()
