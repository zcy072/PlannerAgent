"""Choose a graph retry path from a deterministic evaluator report."""


def evaluator_next_step(report: dict, retry_count: int, retry_limit: int) -> str:
    """Retry the Planner when any issue it can fix remains; warnings stay for the rest."""
    if report.get("passed") or retry_count >= retry_limit:
        return "finalize"
    non_retryable = set(report.get("non_retryable_issues") or [])
    day_issues = [
        issue
        for items in (report.get("day_issues") or {}).values()
        for issue in items
    ]
    retryable = [
        issue
        for issue in [*(report.get("global_issues") or []), *day_issues]
        if issue not in non_retryable
    ]
    return "planner_agent" if retryable else "finalize"
