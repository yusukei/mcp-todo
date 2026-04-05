"""MCP Todo Server 定義

FastMCP サーバインスタンスを作成し、各ツールモジュールを登録する。
OAuth 2.1 (TodoOAuthProvider) による認証をサポートし、
MCP Todo ユーザーと OAuth トークンを紐付ける。
"""

import logging

from fastmcp import FastMCP
from mcp.server.auth.settings import ClientRegistrationOptions

from ..core.config import settings
from .oauth_provider import TodoOAuthProvider

logger = logging.getLogger(__name__)

MOUNT_PREFIX = "/mcp"
MCP_PATH = "/"

# OAuth プロバイダの構築
# base_url にマウントプレフィックスを含める（FastMCP の規約）
_base = settings.BASE_URL.rstrip("/") if settings.BASE_URL else "http://localhost:8000"
_base_url = f"{_base}{MOUNT_PREFIX}"

if not settings.BASE_URL:
    logger.warning(
        "BASE_URL is not set — OAuth URLs will use %s. "
        "Set BASE_URL to the public HTTPS URL for production.",
        _base_url,
    )
else:
    logger.info("MCP OAuth base_url: %s", _base_url)

_oauth_provider = TodoOAuthProvider(
    base_url=_base_url,
    client_registration_options=ClientRegistrationOptions(
        enabled=True,
    ),
)

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
        "Use search_knowledge for full-text search (Japanese supported via Lindera).\n\n"
        "When encountering technical challenges (build errors, integration issues, architecture decisions), "
        "search the knowledge base first with search_knowledge before researching from scratch. "
        "When you discover a non-obvious solution or pattern worth reusing across projects, "
        "suggest saving it as a knowledge entry via create_knowledge.\n\n"
        "## Project documents\n"
        "Project-scoped documents (specs, designs, API docs, guides, notes). "
        "Categories: spec, design, api, guide, notes. "
        "Use search_documents for full-text search (Japanese supported via Lindera). "
        "Use list_documents to browse a project's documents. "
        "Documents are the authoritative source for project specifications — "
        "read relevant documents before starting implementation work on a project. "
        "Documents are versioned: each update_document call creates a version snapshot automatically. "
        "Pass task_id when updating documents to link changes to the task that triggered them. "
        "Use get_document_history to view past versions and get_document_version to retrieve a specific version.\n"
        "Document content supports Markdown with Mermaid diagrams — "
        "use ```mermaid code blocks for sequence diagrams, flowcharts, ER diagrams, etc. "
        "The frontend renders them automatically.\n\n"
        "## Documentation sites\n"
        "Imported external documentation sites (e.g. SDK docs, API references). "
        "Use list_docsites to see available sites, get_docsite to browse the navigation tree, "
        "get_docpage to read a specific page, and search_docpages for full-text search "
        "(Japanese supported via Lindera).\n\n"
        "## Bookmarks\n"
        "Project-scoped bookmarks with web clipping. "
        "Use create_bookmark to save a URL — clipping starts automatically in the background "
        "(Playwright captures the page, extracts article content as Markdown, downloads images, "
        "and takes a thumbnail screenshot). "
        "Use list_bookmarks/search_bookmarks to find bookmarks, get_bookmark to read clipped content, "
        "and clip_bookmark to re-trigger clipping. "
        "Bookmarks can be organized into collections (create_bookmark_collection) and tagged. "
        "Full-text search covers title, description, tags, URL, and clipped content "
        "(Japanese supported via Lindera).\n\n"
        "## Remote execution\n"
        "Execute commands and access files on remote machines via connected agents. "
        "Each project can be linked to one remote workspace (agent + directory). "
        "Use list_remote_agents to see available agents, then configure workspaces "
        "via the web UI or REST API.\n"
        "- remote_exec: Run shell commands (git, docker, npm, etc.) in the project's remote directory\n"
        "- remote_read_file: Read files (relative to remote directory or absolute path)\n"
        "- remote_write_file: Write files (parent dirs created automatically)\n"
        "- remote_list_dir: List directory contents\n"
        "All operations require an online agent and a configured workspace.\n\n"
        "## Development workflow\n"
        "IMPORTANT: Follow this workflow whenever you are about to modify code or configuration files.\n\n"
        "### 1. Task registration\n"
        "Before making any changes to code or configuration files, "
        "ensure a task exists for the work. If none exists, create one via create_task. "
        "This applies to:\n"
        "- Direct user requests for implementation or bug fixes\n"
        "- Bugs or issues discovered during conversation or review\n"
        "- Refactoring, dependency updates, configuration changes\n"
        "- Spec or instruction updates that involve code changes\n"
        "The only exception is trivial fixes (typos, formatting) that do not affect behavior.\n\n"
        "### 2. Start work (set status to in_progress)\n"
        "Use search_documents to find project documents affected by the planned changes. "
        "If relevant documents exist, update them via update_document with the task_id "
        "and change_summary BEFORE beginning implementation. "
        "This ensures specs are updated as a guide for the work, not as an afterthought.\n\n"
        "### 3. Implementation\n"
        "Implement the changes according to the updated specs. "
        "When making significant technical decisions (choosing between approaches, "
        "trade-offs, rejecting alternatives), record the reasoning as a comment "
        "on the task via add_comment. This preserves context for future sessions.\n\n"
        "### 4. Test\n"
        "Run the project's test suite and verify all tests pass.\n\n"
        "### 5. Spec conformance review\n"
        "After tests pass, review the changes against project documents. "
        "This is the most critical step — specs are the source of truth.\n"
        "- Retrieve the diff of changed files\n"
        "- Use search_documents to fetch all specs related to the changes\n"
        "- Verify: does the implementation match what the specs describe?\n"
        "- Verify: are there new behaviors not covered by any spec?\n"
        "- If discrepancies are found, fix the implementation or update the spec, then re-test\n"
        "- Use a sub-agent for this review when available, to get an independent perspective\n\n"
        "### 6. Complete (complete_task)\n"
        "Mark the task as done with a completion_report summarizing what was implemented.\n\n"
        "## Commit convention\n"
        "When committing code changes, include the task ID in the commit message "
        "for traceability (e.g., 'feat: add versioning to documents [task:69c22641]'). "
        "This links git history to the task management system.\n\n"
        "## Onboarding\n"
        "When a user asks to set up mcp-todo for their project, "
        "call get_setup_guide to get the recommended CLAUDE.md snippet, "
        "then write it to the project's CLAUDE.md (create if needed). "
        "Prerequisite: .mcp.json is already configured manually by the user."
    ),
    auth=_oauth_provider,
)


def register_tools() -> None:
    from .tools import bookmarks, documents, docsites, knowledge, projects, remote, setup, tasks  # noqa: F401
