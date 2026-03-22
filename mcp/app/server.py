from fastmcp import FastMCP

MOUNT_PREFIX = "/mcp"
MCP_PATH = "/"

mcp = FastMCP(
    name="ClaudeTodo",
    instructions=(
        "Claude Todo はタスク管理システムです。"
        "プロジェクトの作成・管理、タスクの登録・更新・削除・検索、"
        "コメントの追加などの機能を提供します。"
        "X-API-Key ヘッダーで認証してください。"
    ),
)


def register_tools() -> None:
    from .tools import projects, tasks  # noqa: F401
