$ErrorActionPreference = 'Stop'

# Normalized $1 per occupied GPU-hour. Replace this with your cloud rental or
# attributed infrastructure rate before claiming an absolute currency saving.
$env:ECONOMY_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:ECONOMY_MODEL = 'qwen3:0.6b'
$env:ECONOMY_HOURLY_COST = '1.00'
$env:ECONOMY_REASONING_EFFORT = 'none'
$env:ECONOMY_INPUT_PER_MILLION = ''
$env:ECONOMY_OUTPUT_PER_MILLION = ''

$env:STRONG_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:STRONG_MODEL = 'qwen3:4b'
$env:STRONG_HOURLY_COST = '1.00'
$env:STRONG_REASONING_EFFORT = 'none'
$env:STRONG_INPUT_PER_MILLION = ''
$env:STRONG_OUTPUT_PER_MILLION = ''

python -m lab.server
