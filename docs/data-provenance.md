# Evaluation data provenance

InferenceOps uses Bitext's Customer Support LLM Chatbot Training Dataset for its first independent intent-classification benchmark.

- Source: <https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset>
- License: CDLA-Sharing-1.0
- Pinned revision: `430d1a89bd93bd1fa23c16f29dd53e73f0087443`
- Source file SHA-256: `6f81102b0100b97b8468eb04368033a23206bf1fde9d53500d5806ec1001a434`
- Source file size: 19,202,474 bytes
- Task: exact match on `category` and `intent`
- Validation manifest: 270 cases, 10 per intent
- Held-out test manifest: 540 cases, 20 per intent
- Review queue: 135 cases, 5 per intent

The complete dataset is downloaded into ignored `data/external/` and is not silently republished. Manifests are deterministic from seed 42. Reports must describe initial labels as **published dataset labels** until the review queue is completed. Model-disagreement cases must be reviewed before a final portfolio claim.

The dataset is hybrid synthetic and curated by computational linguists. It is independent of this project's original ticket templates, but it is not evidence of performance on a specific company's production traffic.
