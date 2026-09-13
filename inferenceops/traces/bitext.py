"""Pinned Bitext dataset downloader and deterministic evaluation manifests."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import urllib.request


REVISION = "430d1a89bd93bd1fa23c16f29dd53e73f0087443"
FILENAME = "Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
URL = ("https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset/"
       f"resolve/{REVISION}/{FILENAME}")
LICENSE = "CDLA-Sharing-1.0"


def download(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        urllib.request.urlretrieve(URL, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"url": URL, "revision": REVISION, "filename": FILENAME, "license": LICENSE,
            "sha256": digest, "size_bytes": destination.stat().st_size}


def load_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"instruction", "category", "intent"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Bitext CSV does not contain instruction, category and intent")
    return [{"id": f"bitext-{index:05d}", "text": row["instruction"].strip(),
             "expected": {"category": row["category"].strip(), "intent": row["intent"].strip()},
             "flags": row.get("flags", "").strip()} for index, row in enumerate(rows)]


def stratified_manifests(rows, seed=42, validation_per_intent=10, test_per_intent=20):
    by_intent = {}
    for row in rows:
        by_intent.setdefault(row["expected"]["intent"], []).append(row)
    validation, test = [], []
    needed = validation_per_intent + test_per_intent
    for intent, group in sorted(by_intent.items()):
        if len(group) < needed:
            raise ValueError(f"Intent {intent} has fewer than {needed} examples")
        shuffled = list(group)
        random.Random(f"{seed}:{intent}").shuffle(shuffled)
        validation.extend(shuffled[:validation_per_intent])
        test.extend(shuffled[validation_per_intent:needed])
    random.Random(f"{seed}:validation").shuffle(validation)
    random.Random(f"{seed}:test").shuffle(test)
    return validation, test


def prepare(root):
    root = Path(root)
    external = root / "data" / "external"
    source = external / FILENAME
    provenance = download(source)
    validation, test = stratified_manifests(load_rows(source))
    (external / "bitext-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (external / "bitext-validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    (external / "bitext-test.json").write_text(json.dumps(test, indent=2), encoding="utf-8")
    review = []
    seen = {}
    for row in validation:
        intent = row["expected"]["intent"]
        if seen.get(intent, 0) < 5:
            review.append({"id": row["id"], "instruction": row["text"], **row["expected"],
                           "review_status": "pending", "review_notes": ""})
            seen[intent] = seen.get(intent, 0) + 1
    with (external / "bitext-review-queue.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review[0].keys())
        writer.writeheader(); writer.writerows(review)
    return {**provenance, "validation_count": len(validation), "test_count": len(test),
            "review_count": len(review)}


def main():
    parser = argparse.ArgumentParser(description="Download and prepare the pinned Bitext evaluation dataset")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()
    print(json.dumps(prepare(args.root), indent=2))


if __name__ == "__main__":
    main()
