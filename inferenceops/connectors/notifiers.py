"""Staged outbox and optional Slack webhook notifications."""
import json
import urllib.request


def optimization_message(workflow, opportunity):
    preview = workflow.get("pr_preview") or {}
    pr = workflow.get("pr") or {}
    evidence = preview.get("evidence", {})
    return (f"InferenceOps optimization {workflow['id']}\n"
            f"Endpoint: {opportunity['endpoint']}\n"
            f"Finding: {opportunity['title']}\n"
            f"Experiment: {workflow.get('experiment_id') or 'not linked'}\n"
            f"Quality: {evidence.get('quality', 'n/a')}\n"
            f"p95 latency: {evidence.get('p95_ms', 'n/a')} ms\n"
            f"Cost reduction: {evidence.get('savings_percent', 'n/a')}%\n"
            f"Draft PR: {pr.get('url', preview.get('proposed_url', 'preview only'))}")


class LocalOutboxNotifier:
    def send(self, workflow, opportunity):
        return {"delivery": "staged", "channel": "local-outbox",
                "message": optimization_message(workflow, opportunity)}


class SlackWebhookNotifier:
    def __init__(self, webhook_url):
        if not webhook_url.startswith("https://"):
            raise ValueError("Slack webhook must use HTTPS")
        self.webhook_url = webhook_url

    def send(self, workflow, opportunity):
        message = optimization_message(workflow, opportunity)
        request = urllib.request.Request(self.webhook_url, data=json.dumps({"text": message}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status // 100 != 2:
                raise RuntimeError(f"Slack webhook returned HTTP {response.status}")
        return {"delivery": "delivered", "channel": "slack-webhook", "message": message}
