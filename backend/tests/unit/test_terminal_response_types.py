"""Guard test: every response type the agent might emit must be registered
in ``_RESPONSE_TYPES`` so the WebSocket dispatcher can route it back to a
pending Future.

This test exists because we shipped a regression where ``grep_result``
(and several other Phase 1 *_result types) were missing from the set,
causing every ``remote_grep`` call to hang until the MCP layer's 60s
timeout. Detecting this kind of mismatch with a static check is much
better than discovering it via timeouts in production.
"""

from __future__ import annotations

from app.api.v1.endpoints.terminal import _RESPONSE_TYPES


# Source of truth: every response type emitted by the agent's handler
# implementations in agent/main.py. When you add a new handler, you MUST
# add the type here AND register it in `_RESPONSE_TYPES`.
EXPECTED_AGENT_RESPONSE_TYPES = frozenset({
    # exec / file IO
    "exec_result",
    "file_content",
    "write_result",
    "dir_listing",
    # stat
    "stat_result",
    # mutating file ops
    "mkdir_result",
    "delete_result",
    "move_result",
    "copy_result",
    # search
    "glob_result",
    "grep_result",
})


class TestResponseTypesRegistered:
    def test_every_expected_type_is_registered(self):
        missing = EXPECTED_AGENT_RESPONSE_TYPES - _RESPONSE_TYPES
        assert not missing, (
            f"_RESPONSE_TYPES is missing: {sorted(missing)}. "
            "If you added a new agent handler, register its response type "
            "in backend/app/api/v1/endpoints/terminal.py::_RESPONSE_TYPES."
        )

    def test_no_unknown_types_registered(self):
        """Catch typos that would silently never match anything."""
        unknown = _RESPONSE_TYPES - EXPECTED_AGENT_RESPONSE_TYPES
        assert not unknown, (
            f"_RESPONSE_TYPES has unknown types: {sorted(unknown)}. "
            "Either remove them or add them to EXPECTED_AGENT_RESPONSE_TYPES "
            "in this test."
        )

    def test_all_known_types_have_result_or_listing_suffix(self):
        """Sanity check: response types follow our naming convention."""
        for t in EXPECTED_AGENT_RESPONSE_TYPES:
            assert t.endswith("_result") or t in ("file_content", "dir_listing"), (
                f"unexpected naming for response type: {t!r}"
            )
