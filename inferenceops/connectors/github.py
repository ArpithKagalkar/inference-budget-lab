"""Restricted GitHub draft-PR connector backed by the authenticated gh CLI."""
import base64
import json
import os
import subprocess


class GitHubError(RuntimeError):
    pass


class GitHubConnector:
    DEFAULT_REPOSITORY = "ArpithKagalkar/inferenceops-demo-support-service"
    POLICY_PATH = "config/inference-policy.json"

    def __init__(self, repository=None, runner=None):
        self.repository = repository or os.getenv("INFERENCEOPS_GITHUB_REPOSITORY", self.DEFAULT_REPOSITORY)
        if self.repository != self.DEFAULT_REPOSITORY:
            raise GitHubError("GitHub writes are restricted to the configured demonstration repository")
        self.runner = runner or self._run

    @staticmethod
    def _run(arguments):
        completed = subprocess.run(["gh", *arguments], text=True, capture_output=True, timeout=45)
        if completed.returncode:
            raise GitHubError(completed.stderr.strip() or "GitHub CLI request failed")
        return completed.stdout.strip()

    def _json(self, arguments):
        output = self.runner(arguments)
        return json.loads(output) if output else {}

    def preview(self, workflow, opportunity, experiment):
        adaptive = next(row for row in experiment["results"] if row["id"] == "adaptive")["metrics"]
        policy = {
            "strategy": "economy_confidence_fallback", "endpoint": opportunity["endpoint"],
            "economy_model": "gpt-5.6-luna", "strong_model": "gpt-5.6-terra",
            "confidence_threshold": experiment["threshold"], "schema_fallback": True,
            "source_experiment": workflow["experiment_id"],
        }
        evidence = {
            "quality": round(adaptive["quality"], 6), "p95_ms": round(adaptive["p95_ms"], 2),
            "cost_per_1k": round(adaptive["cost_per_1k"], 6),
            "savings_percent": round((adaptive.get("savings") or 0) * 100, 2),
            "accepted": experiment["accepted"], "simulation": experiment["config"]["mode"] == "simulation",
        }
        branch = f"inferenceops/optimization-{workflow['id']}"
        return {
            "repository": self.repository, "base": "main", "branch": branch,
            "files": [self.POLICY_PATH, f"evidence/{workflow['id']}.json"],
            "policy": policy, "evidence": evidence,
            "title": f"Optimize {opportunity['endpoint']} with verified fallback routing",
            "body": ("## InferenceOps evidence\n\n"
                     f"Workflow: `{workflow['id']}`\n\n"
                     f"Quality: **{evidence['quality']:.1%}**  \n"
                     f"p95 latency: **{evidence['p95_ms']:.0f} ms**  \n"
                     f"Modeled cost reduction: **{evidence['savings_percent']:.1f}%**\n\n"
                     "This is a draft demonstration change. It does not deploy or merge automatically."),
        }

    def _put_file(self, path, content, branch, message):
        allowed = {self.POLICY_PATH, path if path.startswith("evidence/") and path.endswith(".json") else None}
        if path not in allowed:
            raise GitHubError("Attempted GitHub write outside the allowlist")
        encoded = base64.b64encode(content.encode()).decode()
        arguments = ["api", "--method", "PUT", f"repos/{self.repository}/contents/{path}",
                     "-f", f"message={message}", "-f", f"content={encoded}", "-f", f"branch={branch}"]
        try:
            existing = self._json(["api", f"repos/{self.repository}/contents/{path}?ref={branch}"])
            arguments.extend(["-f", f"sha={existing['sha']}"])
        except (GitHubError, KeyError, json.JSONDecodeError):
            pass
        self.runner(arguments)

    def create_draft(self, preview):
        branch = preview["branch"]
        existing = self._json(["pr", "list", "--repo", self.repository, "--head", branch,
                               "--state", "all", "--json", "url,isDraft,headRefName"])
        if existing:
            return {"url": existing[0]["url"], "draft": existing[0]["isDraft"],
                    "branch": existing[0]["headRefName"], "verified": True, "idempotent": True}
        repo = self._json(["api", f"repos/{self.repository}"])
        base = repo["default_branch"]
        ref = self._json(["api", f"repos/{self.repository}/git/ref/heads/{base}"])
        self.runner(["api", "--method", "POST", f"repos/{self.repository}/git/refs",
                     "-f", f"ref=refs/heads/{branch}", "-f", f"sha={ref['object']['sha']}"])
        self._put_file(self.POLICY_PATH, json.dumps(preview["policy"], indent=2) + "\n", branch,
                       "Apply verified InferenceOps routing policy")
        evidence_path = next(path for path in preview["files"] if path.startswith("evidence/"))
        self._put_file(evidence_path, json.dumps(preview["evidence"], indent=2) + "\n", branch,
                       "Record InferenceOps evaluation evidence")
        url = self.runner(["pr", "create", "--repo", self.repository, "--base", base, "--head", branch,
                           "--draft", "--title", preview["title"], "--body", preview["body"]]).splitlines()[-1]
        verified = self._json(["pr", "view", url, "--repo", self.repository,
                               "--json", "url,isDraft,headRefName,files"])
        files = {item["path"] for item in verified.get("files", [])}
        if not verified.get("isDraft") or verified.get("headRefName") != branch or files != set(preview["files"]):
            raise GitHubError("Draft PR verification did not match the allowlisted preview")
        return {"url": verified["url"], "draft": True, "branch": branch,
                "files": sorted(files), "verified": True, "idempotent": False}
