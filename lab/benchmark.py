import argparse
import json
from pathlib import Path
from .engine import Config, run_experiment

def main():
    parser = argparse.ArgumentParser(description='Run a reproducible LLM cost/quality/latency comparison')
    parser.add_argument('--requests', type=int, default=160)
    parser.add_argument('--validation-requests', type=int, default=120)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--rps', type=float, default=4)
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--quality-min', type=float, default=.9)
    parser.add_argument('--latency-slo-ms', type=float, default=1800)
    parser.add_argument('--traffic', choices=['steady','burst'], default='steady')
    parser.add_argument('--mode', choices=['simulation','live'], default='simulation')
    parser.add_argument('--output', default='outputs/benchmark.json')
    args = vars(parser.parse_args())
    output = Path(args.pop('output'))
    result = run_experiment(Config.parse(args), print)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'\nMode: {result["config"]["mode"]} | Selected threshold: {result["threshold"]}')
    for row in result['results']:
        m = row['metrics']
        print(f'{row["name"]:18} quality={m["quality"]:.1%} p95={m["p95_ms"]:.0f}ms '
              f'cost/1k=${m["cost_per_1k"]:.4f} feasible={m["feasible"]}')
    print(f'Saved {output.resolve()}')

if __name__ == '__main__':
    main()
