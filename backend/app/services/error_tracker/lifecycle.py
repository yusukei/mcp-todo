"""Sync error-issue status when linked tasks change lifecycle state.

Called from both REST API (lifecycle.py) and MCP tools (tasks.py)
to keep error issues in sync with their linked tasks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


async def resolve_linked_issues(task_id: str) -> int:
    """Set linked error issues to ``resolved`` when a task is completed.

    Only affects issues whose current status is ``unresolved``.
    Returns the number of issues updated.
    """
    from ...models.error_tracker import ErrorIssue, IssueStatus

    result = await ErrorIssue.get_motor_collection().update_many(
        {"linked_task_ids": task_id, "status": IssueStatus.unresolved.value},
        {"$set": {"status": IssueStatus.resolved.value, "updated_at": datetime.now(UTC)}},
    )
    if result.modified_count:
        logger.info(
            "error-tracker lifecycle: resolved %d issue(s) linked to task %s",
            result.modified_count,
            task_id,
        )
    return result.modified_count


async def ignore_linked_issues(task_id: str) -> int:
    """Set linked error issues to ``ignored`` when a task is archived.

    Only affects issues whose current status is ``unresolved``.
    Returns the number of issues updated.
    """
    from ...models.error_tracker import ErrorIssue, IssueStatus

    result = await ErrorIssue.get_motor_collection().update_many(
        {"linked_task_ids": task_id, "status": IssueStatus.unresolved.value},
        {"$set": {"status": IssueStatus.ignored.value, "updated_at": datetime.now(UTC)}},
    )
    if result.modified_count:
        logger.info(
            "error-tracker lifecycle: ignored %d issue(s) linked to task %s",
            result.modified_count,
            task_id,
        )
    return result.modified_count
