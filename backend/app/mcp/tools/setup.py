"""MCP tool that returns a CLAUDE.md snippet for integrating mcp-todo into a project."""

from ..server import mcp

_CLAUDE_MD_SNIPPET = """\
## Task Management (required)
- **Always use the mcp-todo MCP server for task management** (use MCP tools, NOT TodoWrite)
- See MCP server instructions for tool usage details (task lifecycle, knowledge base, documents, etc.)

### Session start workflow (recommended)
When no specific instructions are given at session start, call `get_work_context` to check current status:
- **approved**: Approved tasks ready for implementation
- **in_progress**: Tasks currently in progress
- **overdue**: Overdue tasks
- **needs_detail**: Tasks requiring investigation

Use `get_task_context` when you need detailed context for a task \
(combines get_task + get_subtasks + get_task_activity into a single call).

### When MCP connection is unavailable (required)
If mcp-todo MCP server tools are not available at session start:
1. Check `.mcp.json` configuration (verify URL and API key)
2. Check server status (`curl -s {server_url}/health`)
3. If unresolved, suggest the user restart the session
4. **Never fall back to TodoWrite or other alternatives — fix the connection**

## Development Workflow
Before modifying code or configuration files:
1. **Task first** — Ensure a task exists via `create_task` (exception: trivial typo/formatting fixes)
2. **Docs first** — Search project documents (`search_documents`) and update relevant specs BEFORE implementation
3. **Implement** — Follow the updated specs; record significant decisions as task comments
4. **Test** — Run the test suite and verify all tests pass
5. **Spec review** — Compare the diff against project documents; fix discrepancies before completing
6. **Complete** — Mark the task done via `complete_task` with a completion report

## Git
- Include the task ID in commit messages for traceability (e.g., `feat: add versioning [task:69c22641]`)
"""


@mcp.tool()
async def get_setup_guide(
    server_url: str = "https://todo.vtech-studios.com",
) -> dict:
    """Get the CLAUDE.md snippet for integrating mcp-todo into a project.

    Prerequisites: .mcp.json must already be configured with the server URL and API key.
    This tool is called AFTER MCP connection is established.

    Returns the recommended CLAUDE.md section to add to the project.
    Write the snippet to the project's CLAUDE.md (create if it doesn't exist).
    Then verify connection by calling `list_projects`.

    Args:
        server_url: The mcp-todo server URL (default: https://todo.vtech-studios.com)
    """
    snippet = _CLAUDE_MD_SNIPPET.format(server_url=server_url)

    return {
        "claude_md_snippet": snippet.strip(),
    }
