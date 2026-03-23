from fastmcp import FastMCP

MOUNT_PREFIX = "/mcp"
MCP_PATH = "/"

mcp = FastMCP(
    name="McpTodo",
    instructions=(
        "MCP Todo is a task management system. "
        "It provides tools for project management, task CRUD, search, "
        "batch operations, and comment management. "
        "Authenticate with the X-API-Key header. "
        "Rate limits: MCP endpoint is rate-limited to 120 requests/minute per IP via nginx. "
        "Field limits: title max 255 chars, description max 10000 chars, comment max 10000 chars.\n\n"
        "## Task lifecycle\n"
        "Tasks follow this workflow:\n"
        "1. Created (status=todo) → may have needs_detail=true if the task needs investigation\n"
        "2. Investigation complete → user decides: approved=true (proceed) or cancel/archive (skip)\n"
        "3. approved=true → ready for implementation (status=in_progress → done)\n\n"
        "## needs_detail flag workflow\n"
        "needs_detail=true means the user cannot yet decide whether or how to address the task. "
        "When asked to handle needs_detail tasks:\n"
        "1. Investigate WHY the task was created (background, context, root cause)\n"
        "2. Present multiple options/approaches if applicable\n"
        "3. Describe trade-offs for each option (cost, risk, complexity, impact)\n"
        "4. Record findings as a comment (via add_comment) to preserve the original description\n"
        "5. Do NOT start implementation — wait for the user's decision\n"
        "After the user reviews, they will either:\n"
        "- Approve: set approved=true (automatically clears needs_detail)\n"
        "- Reject: cancel or archive the task\n\n"
        "## Knowledge base\n"
        "Cross-project knowledge entries for reusable technical know-how. "
        "Categories: recipe, reference, tip, troubleshooting, architecture. "
        "Use search_knowledge for full-text search (Japanese supported via Lindera)."
    ),
)


def register_tools() -> None:
    from .tools import knowledge, projects, tasks  # noqa: F401
