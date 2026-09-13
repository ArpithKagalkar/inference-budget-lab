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
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .engine import Config, run_experiment, live_ready, provider_config, maximum_scheduled_cost, paid_provider_run
from inferenceops.audit import audit_traces
from inferenceops.connectors import GitHubConnector, GitHubError, LocalOutboxNotifier, SlackWebhookNotifier
from inferenceops.storage import Repository
from inferenceops.traces import generate_demo_traces, normalize_records
from inferenceops.workflows import WorkflowError, WorkflowService

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv('LAB_DATABASE', str(ROOT / 'data' / 'lab.sqlite3')))
LOCK = threading.Lock()
JOBS = {}

def ops_repository():
    return Repository(DB)

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

    def json_body(self, maximum=65536):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= maximum:
            raise ValueError(f'Request body must be 1–{maximum} bytes')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError('Request body must be a JSON object')
        return value

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.respond(200, {'status': 'ok', 'live_ready': live_ready(),
                                      'paid_provider': paid_provider_run() if live_ready() else False,
                                      'product': 'InferenceOps', 'version': '0.1.0'})
        if path == '/api/summary':
            return self.respond(200, ops_repository().summary())
        if path == '/api/trace-batches':
            return self.respond(200, ops_repository().list_batches())
        if path == '/api/opportunities':
            repository = ops_repository()
            batches = repository.list_batches()
            return self.respond(200, repository.opportunities(batches[0]['id']) if batches else [])
        if path.startswith('/api/opportunities/'):
            opportunity = ops_repository().opportunity(path.rsplit('/', 1)[1])
            return self.respond(200 if opportunity else 404, opportunity or {'error': 'Opportunity not found'})
        if path == '/api/workflows':
            return self.respond(200, ops_repository().workflows())
        if path.startswith('/api/workflows/'):
            workflow = ops_repository().workflow(path.rsplit('/', 1)[1])
            return self.respond(200 if workflow else 404, workflow or {'error': 'Workflow not found'})
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
        path = urlsplit(self.path).path
        try:
            payload = self.json_body(5_000_000 if path == '/api/trace-batches' else 65536)
            if path == '/api/runs':
                return self.start_run(payload)
            if path == '/api/trace-batches':
                return self.create_trace_batch(payload)
            if path == '/api/audits':
                return self.create_audit(payload)
            if path == '/api/provider-preflight':
                return self.provider_preflight(payload)
            parts = path.strip('/').split('/')
            if len(parts) == 4 and parts[:2] == ['api', 'opportunities'] and parts[3] == 'experiments':
                return self.start_opportunity_experiment(parts[2], payload)
            if len(parts) == 4 and parts[:2] == ['api', 'workflows']:
                return self.workflow_action(parts[2], parts[3], payload)
            return self.respond(404, {'error': 'Not found'})
        except (TypeError, ValueError, KeyError, WorkflowError, GitHubError, json.JSONDecodeError) as error:
            return self.respond(400, {'error': str(error)})

    def start_run(self, payload):
        try:
            cfg = Config.parse(payload)
        except (TypeError, ValueError) as error:
            return self.respond(400, {'error': str(error)})
        with LOCK:
            if any(job['status'] == 'running' for job in JOBS.values()):
                return self.respond(409, {'error': 'An experiment is already running'})
            run_id = uuid.uuid4().hex[:12]
            JOBS[run_id] = {'id': run_id, 'status': 'running', 'message': 'Preparing experiment'}
        threading.Thread(target=execute, args=(run_id,cfg), daemon=True).start()
        return self.respond(202, {'id': run_id})

    def create_trace_batch(self, payload):
        source = payload.get('source', 'json')
        if source == 'demo':
            raw = generate_demo_traces(payload.get('count', 900), payload.get('seed', 42))
            metadata = {'seed': payload.get('seed', 42), 'synthetic': True,
                        'description': 'Deterministic production-shaped demonstration traces'}
        elif source in ('json', 'langfuse-export'):
            raw = payload.get('records')
            metadata = payload.get('metadata') or {'synthetic': False}
        else:
            raise ValueError('source must be demo, json, or langfuse-export')
        records = normalize_records(raw)
        digest = hashlib.sha256(json.dumps([row.to_dict() for row in records], sort_keys=True).encode()).hexdigest()
        repository = ops_repository()
        batch_id = repository.add_trace_batch(source, records, digest, metadata)
        return self.respond(201, next(row for row in repository.list_batches() if row['id'] == batch_id))

    def create_audit(self, payload):
        repository = ops_repository()
        batch_id = payload.get('batch_id')
        if not batch_id:
            batches = repository.list_batches()
            if not batches:
                raise ValueError('Import a trace batch before running an audit')
            batch_id = batches[0]['id']
        traces = repository.traces(batch_id)
        if not traces:
            raise ValueError('Trace batch not found or empty')
        findings = audit_traces(traces)
        return self.respond(201, repository.replace_opportunities(batch_id, findings))

    def provider_preflight(self, payload):
        cfg_payload = {key: value for key, value in payload.items() if key in Config.__dataclass_fields__}
        cfg_payload['mode'] = 'simulation'
        cfg_payload['paid_confirmation'] = ''
        # Validate ordinary bounds without crossing the paid confirmation gate.
        cfg = Config.parse(cfg_payload)
        cfg.mode = 'live'
        if not live_ready():
            raise ValueError('Live providers are not configured')
        maximum = maximum_scheduled_cost(cfg)
        providers = {tier: {key: value for key, value in provider_config(tier).items() if key != 'key'}
                     for tier in ('economy', 'strong')}
        cap = payload.get('budget_cap', 5.0)
        if type(cap) not in (int, float) or not 0 < cap <= 100:
            raise ValueError('budget_cap must be between 0 and 100')
        return self.respond(200, {'call_count': 2 * cfg.validation_requests + 3 * cfg.requests,
                                  'maximum_cost': maximum, 'budget_cap': cap,
                                  'currency': cfg.budget_currency, 'within_cap': maximum <= cap,
                                  'required_confirmation': f'RUN {cap:.2f} {cfg.budget_currency}',
                                  'providers': providers})

    def start_opportunity_experiment(self, opportunity_id, payload):
        repository = ops_repository()
        opportunity = repository.opportunity(opportunity_id)
        if not opportunity:
            return self.respond(404, {'error': 'Opportunity not found'})
        if payload.get('mode', 'simulation') != 'simulation':
            raise ValueError('Opportunity workflows use simulation in V1; run live evidence through the Experiment Lab')
        service = WorkflowService(repository)
        workflow = service.create_for_opportunity(opportunity)
        service.begin_experiment(workflow['id'])
        try:
            cfg = Config.parse({key: value for key, value in payload.items() if key in Config.__dataclass_fields__})
            run_id = uuid.uuid4().hex[:12]
            result = run_experiment(cfg)
            save(run_id, result)
            workflow = service.finish_experiment(workflow['id'], run_id, result['accepted'])
        except Exception as error:
            service.fail_experiment(workflow['id'], type(error).__name__)
            raise
        return self.respond(201, workflow)

    def workflow_action(self, workflow_id, action, payload):
        repository = ops_repository()
        service = WorkflowService(repository)
        workflow = repository.workflow(workflow_id)
        if not workflow:
            return self.respond(404, {'error': 'Workflow not found'})
        opportunity = repository.opportunity(workflow['opportunity_id'])
        if action == 'pr-preview':
            if workflow['state'] == 'PR_PREVIEWED':
                return self.respond(200, workflow)
            if workflow['state'] != 'VERIFIED':
                raise WorkflowError('Only verified experiments can generate a PR preview')
            experiment = get_run(workflow['experiment_id'])
            preview = GitHubConnector().preview(workflow, opportunity, experiment)
            return self.respond(200, service.record_preview(workflow_id, preview))
        if action == 'create-pr':
            if workflow.get('pr', {}).get('verified'):
                return self.respond(200, workflow)
            if workflow['state'] != 'PR_PREVIEWED':
                raise WorkflowError('PR preview must be completed before PR creation')
            if payload.get('confirm') is not True:
                raise WorkflowError('Creating the external draft PR requires confirm=true')
            try:
                result = GitHubConnector().create_draft(workflow['pr_preview'])
                return self.respond(201, service.record_pr(workflow_id, result))
            except Exception as error:
                service.record_partial(workflow_id, 'pr_creation_failed', type(error).__name__)
                raise
        if action == 'notify':
            if workflow['state'] not in ('PR_CREATED', 'PARTIAL'):
                raise WorkflowError('A verified draft PR is required before notification')
            if payload.get('delivery') == 'slack':
                if payload.get('confirm_delivery') is not True:
                    raise WorkflowError('Real Slack delivery requires confirm_delivery=true')
                webhook = os.getenv('SLACK_WEBHOOK_URL', '')
                if not webhook:
                    raise WorkflowError('SLACK_WEBHOOK_URL is not configured')
                notifier = SlackWebhookNotifier(webhook)
            else:
                notifier = LocalOutboxNotifier()
            result = notifier.send(workflow, opportunity)
            return self.respond(201, service.record_notification(workflow_id, result))
        return self.respond(404, {'error': 'Unknown workflow action'})

def main():
    parser = argparse.ArgumentParser(description='Inference Budget Lab local dashboard')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    with connection():
        pass
    print(f'InferenceOps -> http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
