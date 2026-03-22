from fastmcp import FastMCP

MOUNT_PREFIX = "/mcp"
MCP_PATH = "/"

mcp = FastMCP(
    name="ClaudeTodo",
    instructions=(
        "Claude Todo is a task management system. "
        "It provides tools for project management, task CRUD, search, "
        "batch operations, and comment management. "
        "Authenticate with the X-API-Key header."
    ),
)


def register_tools() -> None:
    from .tools import projects, tasks  # noqa: F401
