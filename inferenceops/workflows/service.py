"""Deterministic workflow transitions for verified optimization work."""


TRANSITIONS = {
    "DISCOVERED": {"EXPERIMENT_PENDING"},
    "EXPERIMENT_PENDING": {"EXPERIMENT_RUNNING", "EXPERIMENT_FAILED"},
    "EXPERIMENT_RUNNING": {"VERIFIED", "REJECTED", "EXPERIMENT_FAILED"},
    "VERIFIED": {"PR_PREVIEWED"},
    "PR_PREVIEWED": {"PR_CREATED", "PARTIAL"},
    "PR_CREATED": {"NOTIFICATION_STAGED", "COMPLETE", "PARTIAL"},
    "NOTIFICATION_STAGED": {"COMPLETE", "PARTIAL"},
    "PARTIAL": {"PR_CREATED", "NOTIFICATION_STAGED", "COMPLETE"},
    "REJECTED": set(), "EXPERIMENT_FAILED": set(), "COMPLETE": set(),
}


class WorkflowError(ValueError):
    pass


class WorkflowService:
    def __init__(self, repository):
        self.repository = repository

    def create_for_opportunity(self, opportunity):
        if not opportunity.get("eligible_for_experiment") or opportunity.get("type") != "oversized_model":
            raise WorkflowError("Only oversized-model opportunities can start experiments in V1")
        workflow = self.repository.create_workflow(opportunity["id"])
        return self.transition(workflow["id"], "EXPERIMENT_PENDING", "experiment_requested")

    def transition(self, workflow_id, state, event_type, status="success", **changes):
        current = self.repository.workflow(workflow_id)
        if not current:
            raise WorkflowError("Workflow not found")
        if state not in TRANSITIONS.get(current["state"], set()):
            raise WorkflowError(f"Invalid workflow transition: {current['state']} -> {state}")
        updated = self.repository.update_workflow(workflow_id, state=state, **changes)
        self.repository.add_event(workflow_id, event_type, status, {"from": current["state"], "to": state, **changes})
        return self.repository.workflow(workflow_id)

    def begin_experiment(self, workflow_id):
        return self.transition(workflow_id, "EXPERIMENT_RUNNING", "experiment_started")

    def finish_experiment(self, workflow_id, experiment_id, accepted):
        return self.transition(workflow_id, "VERIFIED" if accepted else "REJECTED", "experiment_completed",
                               experiment_id=experiment_id, accepted=bool(accepted))

    def fail_experiment(self, workflow_id, error_type):
        return self.transition(workflow_id, "EXPERIMENT_FAILED", "experiment_failed", status="failed",
                               error_type=error_type)

    def record_preview(self, workflow_id, preview):
        return self.transition(workflow_id, "PR_PREVIEWED", "pr_previewed", pr_preview=preview)

    def record_pr(self, workflow_id, result):
        return self.transition(workflow_id, "PR_CREATED", "pr_created", pr=result)

    def record_partial(self, workflow_id, event_type, error_type):
        return self.transition(workflow_id, "PARTIAL", event_type, status="failed", error_type=error_type)

    def record_notification(self, workflow_id, result):
        if result.get("delivery") == "staged":
            self.transition(workflow_id, "NOTIFICATION_STAGED", "notification_recorded", notification=result)
            return self.transition(workflow_id, "COMPLETE", "workflow_completed")
        return self.transition(workflow_id, "COMPLETE", "notification_recorded", notification=result)
