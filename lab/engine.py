import concurrent.futures
import heapq
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from .dataset import CATEGORIES, FIELDS, PRODUCTS, URGENCIES, fixtures, difficulty, fingerprint, score

PROMPT_VERSION = '2.0'
SYSTEM = ('Extract a support ticket as a JSON object with exactly product, category, urgency. '
          'Product must be Atlas, Beacon, or Canvas. Category definitions: billing means charges, '
          'fees, or invoices; access means sign-in or password-reset problems; performance means '
          'slow loading or slow search; bug means corrupted exports or a button that does nothing. '
          'Urgency must be low, normal, or high. Production blocked means high; no rush means low; '
          'otherwise use normal. If the ticket says an earlier issue is no longer the problem and '
          'introduces the current issue with “however”, classify only the current issue. Return only JSON.')
POLICIES = [('strong', 'Always strong'), ('economy', 'Always economy'), ('adaptive', 'Adaptive router')]

@dataclass
class Config:
    mode: str = 'simulation'
    requests: int = 160
    validation_requests: int = 120
    seed: int = 42
    rps: float = 4.0
    concurrency: int = 8
    quality_min: float = 0.9
    latency_slo_ms: float = 1800
    traffic: str = 'steady'

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown configuration fields')
        cfg = cls(**value)
        for key in ('requests', 'validation_requests', 'seed', 'concurrency'):
            if type(getattr(cfg, key)) is not int:
                raise ValueError(f'{key} must be an integer')
        for key in ('rps', 'quality_min', 'latency_slo_ms'):
            x = getattr(cfg, key)
            if type(x) not in (int, float) or not math.isfinite(x):
                raise ValueError(f'{key} must be a finite number')
        if cfg.mode not in ('simulation', 'live') or cfg.traffic not in ('steady', 'burst'):
            raise ValueError('Invalid mode or traffic shape')
        if not (20 <= cfg.requests <= 500 and 20 <= cfg.validation_requests <= 500
                and 1 <= cfg.concurrency <= 32 and 0.1 <= cfg.rps <= 50
                and 0.5 <= cfg.quality_min <= 1 and 100 <= cfg.latency_slo_ms <= 60000
                and 0 <= cfg.seed <= 999999):
            raise ValueError('Configuration is outside supported limits')
        if cfg.mode == 'live' and not live_ready():
            raise ValueError('Live endpoints and explicit token prices must be configured on the server')
        return cfg

def provider_config(tier):
    prefix = tier.upper()
    return {'base_url': os.getenv(f'{prefix}_BASE_URL', '').rstrip('/'),
            'model': os.getenv(f'{prefix}_MODEL', ''), 'key': os.getenv(f'{prefix}_API_KEY', ''),
            'input_price': os.getenv(f'{prefix}_INPUT_PER_MILLION'),
            'output_price': os.getenv(f'{prefix}_OUTPUT_PER_MILLION'),
            'hourly_cost': os.getenv(f'{prefix}_HOURLY_COST'),
            'reasoning_effort': os.getenv(f'{prefix}_REASONING_EFFORT', '')}

def cost_basis(provider):
    """Return token or compute billing when its required values are valid."""
    try:
        token_values = [float(provider[k]) for k in ('input_price', 'output_price')]
        if all(math.isfinite(value) and value >= 0 for value in token_values):
            return 'tokens'
    except (TypeError, ValueError):
        pass
    try:
        hourly = float(provider['hourly_cost'])
        if math.isfinite(hourly) and hourly > 0:
            return 'compute_time'
    except (TypeError, ValueError):
        pass
    return None

def live_ready():
    for tier in ('economy', 'strong'):
        p = provider_config(tier)
        if not p['base_url'].startswith(('http://', 'https://')) or not p['model']:
            return False
        if cost_basis(p) is None:
            return False
    return True

def model_for(policy, text, threshold):
    return ('economy' if difficulty(text) <= threshold else 'strong') if policy == 'adaptive' else policy

def simulated(row, tier, seed):
    rng = random.Random(fingerprint(f'{seed}:{row["text"]}:{tier}'))
    level = difficulty(row['text'])
    probability = 0.985 if tier == 'strong' else {0.15: 0.98, 0.55: 0.79, 0.9: 0.47}[level]
    output = dict(row['expected'])
    if rng.random() > probability:
        output['category'] = 'access' if output['category'] != 'access' else 'billing'
    input_tokens = math.ceil((len(SYSTEM) + len(row['text'])) / 4)
    output_tokens = 24
    prices = (0.15, 0.6) if tier == 'economy' else (2.5, 10)
    service_ms = (150 if tier == 'economy' else 650) + input_tokens * (0.45 if tier == 'economy' else 1.1)
    service_ms *= rng.lognormvariate(0, 0.20)
    return {'output': output, 'service_ms': service_ms, 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'cost': (input_tokens * prices[0] + output_tokens * prices[1]) / 1e6,
            'error': None, 'cost_known': True}

def live_call(row, tier):
    p = provider_config(tier)
    basis = cost_basis(p)
    payload = {'model': p['model'], 'messages': [{'role': 'system', 'content': SYSTEM},
               {'role': 'user', 'content': row['text']}], 'temperature': 0,
               'max_tokens': 150, 'response_format': {'type': 'json_schema', 'json_schema': {
                   'name': 'support_ticket', 'strict': True, 'schema': {'type': 'object',
                   'properties': {'product': {'type': 'string', 'enum': list(PRODUCTS)},
                                  'category': {'type': 'string', 'enum': list(CATEGORIES)},
                                  'urgency': {'type': 'string', 'enum': list(URGENCIES)}},
                   'required': list(FIELDS), 'additionalProperties': False}}}}
    if p['reasoning_effort']:
        payload['reasoning_effort'] = p['reasoning_effort']
    headers = {'Content-Type': 'application/json'}
    if p['key']:
        headers['Authorization'] = f'Bearer {p["key"]}'
    request = urllib.request.Request(p['base_url'] + '/chat/completions',
                                     data=json.dumps(payload).encode(), headers=headers)
    start = time.perf_counter()
    result = {'output': {}, 'cost': 0, 'input_tokens': 0, 'output_tokens': 0,
              'error': None, 'cost_known': False, 'cost_basis': basis}
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
        usage = data.get('usage', {})
        if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens')):
            result.update(input_tokens=usage['prompt_tokens'], output_tokens=usage['completion_tokens'])
            if basis == 'tokens':
                result['cost_known'] = True
                result['cost'] = (usage['prompt_tokens'] * float(p['input_price'])
                                  + usage['completion_tokens'] * float(p['output_price'])) / 1e6
        try:
            result['output'] = json.loads(data['choices'][0]['message']['content'])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            result['error'] = 'Invalid model JSON response'
    except urllib.error.HTTPError as error:
        result['error'] = f'Provider HTTP {error.code}'
    except Exception as error:
        result['error'] = f'Provider request failed ({type(error).__name__})'
    result['service_ms'] = (time.perf_counter() - start) * 1000
    if basis == 'compute_time':
        result['cost_known'] = True
        result['cost'] = result['service_ms'] / 3_600_000 * float(p['hourly_cost'])
    return result

def percentile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    lo = int(position)
    return values[lo] + (values[min(lo + 1, len(values)-1)] - values[lo]) * (position-lo)

def wilson(successes, n):
    z = 1.96
    p = successes / n
    center = (p + z*z / (2*n)) / (1 + z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return [max(0, center-half), min(1, center+half)]

def arrival_times(count, cfg):
    rng = random.Random(cfg.seed)
    cursor = 0
    times = []
    for i in range(count):
        multiplier = 4 if cfg.traffic == 'burst' and 0.35 <= i/count <= 0.65 else 1
        cursor += rng.expovariate(cfg.rps * multiplier) * 1000
        times.append(cursor)
    first = times[0]
    return [t-first for t in times]

def evaluate(rows, policy, threshold, cfg, progress=None):
    arrivals = arrival_times(len(rows), cfg)
    records = []
    def record(row, tier, result, arrival, queue_ms):
        grading = score(result['output'], row['expected'])
        return {**row, **result, **grading, 'model': tier, 'arrival_ms': arrival,
                'queue_ms': queue_ms, 'latency_ms': queue_ms + result['service_ms']}
    if cfg.mode == 'simulation':
        workers = [0.] * cfg.concurrency
        for row, arrival in zip(rows, arrivals):
            tier = model_for(policy, row['text'], threshold)
            result = simulated(row, tier, cfg.seed)
            start = max(arrival, heapq.heappop(workers))
            heapq.heappush(workers, start + result['service_ms'])
            records.append(record(row, tier, result, arrival, start-arrival))
    else:
        start = time.perf_counter()
        def execute(row, arrival):
            queue_ms = max(0, (time.perf_counter()-start)*1000-arrival)
            tier = model_for(policy, row['text'], threshold)
            return record(row, tier, live_call(row, tier), arrival, queue_ms)
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            pending = []
            for row, arrival in zip(rows, arrivals):
                time.sleep(max(0, start + arrival/1000-time.perf_counter()))
                pending.append(pool.submit(execute, row, arrival))
            for future in pending:
                records.append(future.result())
                if progress:
                    progress()
    return summarize(records, cfg), records

def summarize(records, cfg):
    n = len(records)
    correct = sum(r['correct'] for r in records)
    latency = [r['latency_ms'] for r in records]
    quality = correct/n
    p95 = percentile(latency, .95)
    known = all(r['cost_known'] for r in records)
    total = sum(r['cost'] for r in records)
    elapsed = max(r['arrival_ms'] + r['latency_ms'] for r in records) / 1000
    return {'requests': n, 'quality': quality, 'quality_ci': wilson(correct, n),
            'field_accuracy': sum(sum(r['fields'].values()) for r in records)/(n*3),
            'schema_validity': sum(r['valid'] for r in records)/n,
            'p50_ms': percentile(latency,.5), 'p95_ms': p95, 'p99_ms': percentile(latency,.99),
            'total_cost': total, 'cost_per_1k': total/n*1000, 'cost_known': known,
            'cost_per_correct': total/correct if correct else None,
            'slo_violations': sum(x > cfg.latency_slo_ms for x in latency)/n,
            'error_rate': sum(bool(r['error']) for r in records)/n,
            'economy_share': sum(r['model'] == 'economy' for r in records)/n,
            'throughput': n/elapsed, 'duration_s': elapsed,
            'feasible': quality >= cfg.quality_min and p95 <= cfg.latency_slo_ms,
            'quality_by_difficulty': {level: {'n': len(group), 'quality': sum(r['correct'] for r in group)/len(group)}
                for level in ('easy','medium','hard') if (group := [r for r in records if r['difficulty'] == level])}}

def run_experiment(cfg, update=lambda message: None):
    validation = fixtures('validation', cfg.validation_requests, cfg.seed)
    test = fixtures('test', cfg.requests, cfg.seed)
    update(f'Calibrating routing on {cfg.validation_requests} validation fixtures')
    candidates = []
    calibration_cost = 0
    calibration_cost_known = True
    # Live calibration calls each model once per fixture. Candidate quality/cost use these
    # responses; latency is a replay estimate, not a second measured live benchmark.
    cached = {}
    if cfg.mode == 'live':
        for tier in ('economy', 'strong'):
            _, records = evaluate(validation, tier, 0, cfg)
            cached[tier] = records
            calibration_cost += sum(r['cost'] for r in records)
            calibration_cost_known &= all(r['cost_known'] for r in records)
    for threshold in (0, .2, .6, 1):
        if cfg.mode == 'simulation':
            metrics, _ = evaluate(validation, 'adaptive', threshold, cfg)
        else:
            workers = [0.] * cfg.concurrency
            records = []
            for i, arrival in enumerate(arrival_times(len(validation), cfg)):
                tier = model_for('adaptive', validation[i]['text'], threshold)
                row = dict(cached[tier][i])
                start = max(arrival, heapq.heappop(workers))
                heapq.heappush(workers, start + row['service_ms'])
                row.update(arrival_ms=arrival, queue_ms=start-arrival, latency_ms=start-arrival+row['service_ms'])
                records.append(row)
            metrics = summarize(records, cfg)
        candidates.append({'threshold': threshold, **metrics})
    eligible = [x for x in candidates if x['feasible'] and x['cost_known']]
    selected = min(eligible, key=lambda x: x['cost_per_1k']) if eligible else candidates[0]
    results = []
    for policy, name in POLICIES:
        update(f'Benchmarking {name} on {cfg.requests} held-out fixtures')
        metrics, records = evaluate(test, policy, selected['threshold'], cfg)
        results.append({'id': policy, 'name': name, 'metrics': metrics, 'records': records})
    baseline = results[0]['metrics']['cost_per_1k']
    baseline_known = results[0]['metrics']['cost_known']
    for result in results:
        result['metrics']['savings'] = (1-result['metrics']['cost_per_1k']/baseline
                                        if baseline and baseline_known and result['metrics']['cost_known'] else None)
    adaptive = results[2]['metrics']
    return {'config': asdict(cfg), 'results': results, 'calibration': candidates,
            'threshold': selected['threshold'], 'validation_feasible': bool(eligible),
            'accepted': bool(eligible) and adaptive['feasible'] and adaptive['cost_known'],
            'calibration_cost': calibration_cost, 'calibration_cost_known': calibration_cost_known,
            'benchmark_cost': sum(r['metrics']['total_cost'] for r in results),
            'dataset': {'name': 'Support tickets / synthetic fixtures v1', 'validation_count': len(validation),
                        'test_count': len(test), 'test_sha256': fingerprint(json.dumps(test, sort_keys=True))},
            'providers': {tier: {**{k:v for k,v in provider_config(tier).items() if k != 'key'},
                                 'cost_basis': cost_basis(provider_config(tier))}
                          for tier in ('economy','strong')} if cfg.mode == 'live' else {},
            'methodology_version': '1.1', 'prompt_version': PROMPT_VERSION}
