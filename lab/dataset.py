"""Synthetic labeled fixture corpus. No model-produced or production data."""
import hashlib
import random

FIELDS = ('product', 'category', 'urgency')
CATEGORIES = ('billing', 'access', 'performance', 'bug')
PRODUCTS = ('Atlas', 'Beacon', 'Canvas')
URGENCIES = ('low', 'normal', 'high')
PHRASES = {
    'billing': ('I was charged twice', 'My invoice has an unexpected fee'),
    'access': ('I cannot sign in', 'My password reset link has expired'),
    'performance': ('The dashboard takes a minute to load', 'Every search is very slow'),
    'bug': ('Export produces a corrupted file', 'The save button does nothing'),
}

def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()

def difficulty(text):
    """A deliberately interpretable, label-free request feature."""
    if 'not the issue' in text.lower() or 'however' in text.lower():
        return 0.9
    if 'previously' in text.lower() or len(text) > 200:
        return 0.55
    return 0.15

def fixtures(split, count, seed=42):
    rng = random.Random(f'{seed}:{split}')
    rows = []
    for i in range(count):
        product = rng.choice(PRODUCTS)
        category = rng.choice(CATEGORIES)
        urgency = rng.choice(URGENCIES)
        level = rng.choices(['easy', 'medium', 'hard'], [0.5, 0.25, 0.25])[0]
        phrase = rng.choice(PHRASES[category])
        urgency_text = {'low': 'No rush; this is a minor inconvenience.',
                        'normal': 'Please investigate during normal support hours.',
                        'high': 'Urgent: our production team is blocked.'}[urgency]
        text = f'For {product}: {phrase}. {urgency_text}'
        if level == 'medium':
            text += ' Previously I contacted support about this; the issue still persists after restarting.'
        if level == 'hard':
            other = rng.choice([c for c in CATEGORIES if c != category])
            text = f'For {product}: {rng.choice(PHRASES[other])}, but that is not the issue anymore. However, {phrase.lower()}. {urgency_text}'
        text += f' Reference {split}-{seed}-{i:04d}.'
        rows.append({'id': f'{split}-{i:04d}', 'text': text, 'difficulty': level,
                     'expected': {'product': product, 'category': category, 'urgency': urgency}})
    return rows

def score(output, expected):
    valid = isinstance(output, dict) and set(output) == set(FIELDS)
    if valid:
        valid = (output['product'] in PRODUCTS and output['category'] in CATEGORIES
                 and output['urgency'] in URGENCIES)
    matches = {field: bool(valid and output.get(field) == expected[field]) for field in FIELDS}
    return {'correct': all(matches.values()), 'valid': bool(valid), 'fields': matches}
