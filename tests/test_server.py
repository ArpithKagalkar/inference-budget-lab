import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from lab import server

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch=Path(__file__).resolve().parents[1]/'work'
        scratch.mkdir(exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=scratch)
        cls.db_patch=patch.object(server,'DB',Path(cls.temp.name)/'test.sqlite3')
        cls.db_patch.start()
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.db_patch.stop()
        cls.temp.cleanup()

    def request(self,path,payload=None,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:
            headers['Origin']=origin
        req=urllib.request.Request(self.url+path,headers=headers,
             data=json.dumps(payload).encode() if payload is not None else None)
        return urllib.request.urlopen(req,timeout=5)

    def test_full_job_persistence_and_csv_export(self):
        with self.request('/api/runs',{'requests':20}) as response:
            self.assertEqual(response.status,202)
            run_id=json.load(response)['id']
        for _ in range(100):
            with self.request(f'/api/jobs/{run_id}') as response:
                job=json.load(response)
            if job['status']!='running':
                break
            time.sleep(.02)
        self.assertEqual(job['status'],'complete')
        with self.request(f'/api/runs/{run_id}') as response:
            run=json.load(response)
        self.assertEqual(len(run['results']),3)
        with self.request(f'/api/runs/{run_id}/csv') as response:
            records=list(csv.DictReader(io.StringIO(response.read().decode())))
        self.assertEqual(len(records),60)
        self.assertEqual({r['policy'] for r in records},{'strong','economy','adaptive'})
        with self.request('/api/runs') as response:
            self.assertIn(run_id,[r['id'] for r in json.load(response)])

    def test_rejects_invalid_request(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/runs',{'requests':-1})
        self.assertEqual(caught.exception.code,400)
        caught.exception.close()

    def test_rejects_cross_origin_post(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/runs',{'requests':20},'https://unrelated.example')
        self.assertEqual(caught.exception.code,403)
        caught.exception.close()

    def test_static_allowlist_and_health(self):
        with self.request('/api/health') as response:
            self.assertEqual(json.load(response)['status'],'ok')
        with self.request('/') as response:
            self.assertIn(b'Inference Budget Lab',response.read())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/.env')
        self.assertEqual(caught.exception.code,404)
        caught.exception.close()
