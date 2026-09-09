"""Local-only HTTP application, persistent experiments, and background job runner."""
import argparse
from contextlib import contextmanager
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .engine import Config, run_experiment, live_ready

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv('LAB_DATABASE', str(ROOT / 'data' / 'lab.sqlite3')))
LOCK = threading.Lock()
JOBS = {}

@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, created REAL, payload TEXT)')
            yield conn
    finally:
        conn.close()

def save(run_id, payload):
    payload.update(id=run_id, created=time.time())
    with connection() as conn:
        conn.execute('INSERT INTO experiments VALUES (?, ?, ?)', (run_id, payload['created'], json.dumps(payload)))

def get_run(run_id):
    with connection() as conn:
        row = conn.execute('SELECT payload FROM experiments WHERE id=?', (run_id,)).fetchone()
    return json.loads(row[0]) if row else None

def history():
    with connection() as conn:
        rows = conn.execute('SELECT payload FROM experiments ORDER BY created DESC LIMIT 30').fetchall()
    return [{k:v for k,v in json.loads(row[0]).items() if k in ('id','created','config','accepted','threshold')}
            for row in rows]

def execute(run_id, cfg):
    def update(message):
        with LOCK:
            JOBS[run_id].update(message=message)
    try:
        result = run_experiment(cfg, update)
        save(run_id, result)
        with LOCK:
            JOBS[run_id].update(status='complete', message='Experiment complete')
    except Exception as error:
        with LOCK:
            JOBS[run_id].update(status='failed', message=f'Experiment failed ({type(error).__name__})')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, data, content_type='application/json', filename=None):
        body = json.dumps(data, allow_nan=False).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.respond(200, {'status': 'ok', 'live_ready': live_ready()})
        if path == '/api/runs':
            return self.respond(200, history())
        if path.startswith('/api/jobs/'):
            with LOCK:
                job = dict(JOBS.get(path.rsplit('/',1)[1], {}))
            return self.respond(200 if job else 404, job or {'error': 'Job not found'})
        if path.startswith('/api/runs/'):
            parts = path.strip('/').split('/')
            run = get_run(parts[2])
            if run is None:
                return self.respond(404, {'error': 'Experiment not found'})
            if len(parts) == 4 and parts[3] == 'csv':
                buffer = io.StringIO(newline='')
                columns = ['policy','id','difficulty','model','correct','valid','latency_ms','queue_ms',
                           'service_ms','cost','cost_known','input_tokens','output_tokens','error']
                writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for result in run['results']:
                    for row in result['records']:
                        writer.writerow({'policy': result['id'], **row})
                return self.respond(200, buffer.getvalue().encode(), 'text/csv', f'{run["id"]}.csv')
            return self.respond(200, run)
        assets = {'/': ('index.html','text/html; charset=utf-8'),
                  '/app.js': ('app.js','text/javascript; charset=utf-8'),
                  '/style.css': ('style.css','text/css; charset=utf-8')}
        if path in assets:
            filename, mime = assets[path]
            return self.respond(200, (ROOT/'web'/filename).read_bytes(), mime)
        return self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        # This app can spend money in live mode. Reject browser cross-origin requests.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.respond(403, {'error': 'Cross-origin requests are disabled'})
        if urlsplit(self.path).path != '/api/runs':
            return self.respond(404, {'error': 'Not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                raise ValueError('Request body must be 1–8192 bytes')
            cfg = Config.parse(json.loads(self.rfile.read(size)))
        except (TypeError, ValueError) as error:
            return self.respond(400, {'error': str(error)})
        with LOCK:
            if any(job['status'] == 'running' for job in JOBS.values()):
                return self.respond(409, {'error': 'An experiment is already running'})
            run_id = uuid.uuid4().hex[:12]
            JOBS[run_id] = {'id': run_id, 'status': 'running', 'message': 'Preparing experiment'}
        threading.Thread(target=execute, args=(run_id,cfg), daemon=True).start()
        return self.respond(202, {'id': run_id})

def main():
    parser = argparse.ArgumentParser(description='Inference Budget Lab local dashboard')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    with connection():
        pass
    print(f'Inference Budget Lab -> http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
