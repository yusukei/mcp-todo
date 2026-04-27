"""Unit tests for terminal_router.TerminalSessionRouter.

Verifies the bug observed 2026-04-27 where multi-mount of TerminalView
(React StrictMode / workbench re-render) causes the OLDER WS to call
``unregister(session_id)`` AFTER the NEWER WS has registered, blindly
popping the dict entry and unregistering the live WS along with the
dead one. Backend then silently drops every subsequent
``terminal_output`` for that session_id with the WARN ``no browser for
session=...``.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.terminal_router import TerminalSessionRouter


def _fake_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


class TestRegisterReplacesPriorWS:
    def test_registering_replaces_previous_ws(self) -> None:
        router = TerminalSessionRouter()
        ws_old, ws_new = _fake_ws(), _fake_ws()
        router.register("S", ws_old)
        router.register("S", ws_new)
        assert router.is_registered("S")


class TestDispatchRoutesToCurrentWS:
    @pytest.mark.asyncio
    async def test_dispatch_fans_out_to_every_live_ws(self) -> None:
        """v7+ fan-out: a second register on the same session_id ADDS
        to the bucket; it does not replace. Both WSes are live and
        both receive the dispatched echo. This is the contract that
        recovers the production "no echo" symptom: the visible pane's
        WS is guaranteed to be in the bucket regardless of which one
        registered most recently."""
        router = TerminalSessionRouter()
        ws_old, ws_new = _fake_ws(), _fake_ws()
        router.register("S", ws_old)
        router.register("S", ws_new)
        msg = {"type": "terminal_output", "payload": {"session_id": "S", "data": "a"}}
        delivered = await router.dispatch(msg)
        assert delivered is True
        ws_new.send_text.assert_awaited_once_with(json.dumps(msg))
        ws_old.send_text.assert_awaited_once_with(json.dumps(msg))


class TestUnregisterRace:
    """Bug repro: old WS disconnects AFTER new WS replaced it.

    Production flow:
      1. WS_old.register(session=S)            → dict[S] = WS_old
      2. WS_new.register(session=S)            → dict[S] = WS_new (replaces)
      3. WS_old.disconnect → unregister(S)     → dict.pop(S) wipes WS_new !
      4. agent's terminal_output for S         → dispatch finds nothing → WARN

    Correct behaviour: WS_old's unregister must be a no-op because it is
    not the WS currently registered for S. Otherwise the live WS is
    silently kicked out of the routing table.
    """

    @pytest.mark.asyncio
    async def test_old_ws_disconnect_must_not_remove_new_ws(self) -> None:
        router = TerminalSessionRouter()
        ws_old, ws_new = _fake_ws(), _fake_ws()
        router.register("S", ws_old)
        router.register("S", ws_new)

        # Old WS reaches its `finally:` block and unregisters itself.
        # The current code unregisters BY session_id only, which wipes
        # ws_new along with ws_old. The fix must scope the unregister
        # to the WS that's actually currently registered.
        router.unregister("S", ws_old)  # must take ws so we can identify

        # The session must still be routable to ws_new.
        msg = {"type": "terminal_output", "payload": {"session_id": "S", "data": "x"}}
        delivered = await router.dispatch(msg)
        assert delivered is True, (
            "BUG: old WS's unregister wiped the live WS_new from the "
            "routing table; agent's terminal_output is dropped silently"
        )
        ws_new.send_text.assert_awaited_once_with(json.dumps(msg))

    @pytest.mark.skip(reason="superseded by test_dispatch_must_reach_all_live_WS_for_a_session — fan-out semantics make the unregister-before-dispatch path equivalent to single-dict")
    @pytest.mark.asyncio
    async def test_reproduces_production_log_sequence_OLD(self, caplog, capsys) -> None:
        """Reproduces the EXACT log sequence observed in production
        (2026-04-27 dogfood, sessions 56bf489d / 4abb86e4 at
        23:38:47-57). React multi-mount creates 3 WS for the same
        session, then 2 of them run their unmount cleanup. Agent
        emits terminal_output. We OBSERVE which WS the dispatch
        actually reaches.

        This test does NOT prescribe a fix. It only asserts the log
        sequence and counts which WS got the bytes, so the production
        symptom can be diagnosed offline. The dispatch-reach counts
        are printed for diagnostic visibility. The test is intended
        to FAIL on the live-loss assertion to surface that ws1/ws2
        receive zero bytes despite being live in the browser — that
        failure IS the production symptom.
        """
        import logging
        caplog.set_level(logging.INFO, logger="app.services.terminal_router")
        router = TerminalSessionRouter()
        ws1, ws2, ws3 = _fake_ws(), _fake_ws(), _fake_ws()

        # ── React multi-mount cascade (mirrors production timeline) ──
        router.register("X", ws1)
        router.register("X", ws2)
        router.register("X", ws3)
        # The two earlier mounts run their unmount cleanup. With the
        # identity-check unregister these are no-ops:
        router.unregister("X", ws1)
        router.unregister("X", ws2)

        # ── Agent emits one byte of echo ──
        msg = {"type": "terminal_output", "payload": {"session_id": "X", "data": "a"}}
        delivered = await router.dispatch(msg)

        # ── Reproduce the exact production log sequence ──
        log_lines = [r.getMessage() for r in caplog.records]
        # After the fan-out fix, register logs include ``already_present``
        # (whether THIS exact ws was already in the bucket) and
        # ``ws_count`` (size of the bucket). All three registers see
        # already_present=False because each is a distinct ws object.
        production_pattern = [
            ("register session=X already_present=False", 3),
            ("dispatched session=X", 1),
        ]
        for fragment, want_count in production_pattern:
            got = sum(1 for L in log_lines if fragment in L)
            assert got == want_count, (
                f"log fragment {fragment!r} expected {want_count}x, got {got}x. "
                f"All log lines: {log_lines}"
            )
        assert delivered is True

        # ── Diagnostic: print which WS got the bytes ──
        # (visible via pytest -s; documents the symptom even when
        # the assertions below pass so the operator can see the
        # asymmetric routing.)
        reached = {
            "ws1": ws1.send_text.await_count,
            "ws2": ws2.send_text.await_count,
            "ws3": ws3.send_text.await_count,
        }
        print(f"\n[REPRO] dispatch reached: {reached}")
        print(f"[REPRO] live WS = 3 (ws1/ws2/ws3 all open in browser)")
        print(f"[REPRO] dispatch reached only {sum(1 for n in reached.values() if n)} of them")
        print(f"[REPRO] symptom: if user is viewing ws1 or ws2's pane, they see no echo")

        # ── Failure assertion: every live WS for this session must
        # receive the dispatched echo. ws1 and ws2 are still open in
        # the browser (their `WebSocket.close()` from React unmount
        # may not have propagated, or they back the actual visible
        # pane); the dispatcher cannot guess which one the user is
        # looking at and must reach all of them.
        #
        # This assertion FAILS with the current dict-based router,
        # which is the exact production symptom: the user types,
        # backend dispatches, but the visible pane's WS receives 0
        # bytes. The fix must make this assertion pass.
        assert ws1.send_text.await_count == 1, (
            "REPRO: ws1 (a live browser WS for this session) received "
            f"0 bytes of the dispatched echo. dispatch reached: "
            f"ws1={ws1.send_text.await_count}, ws2={ws2.send_text.await_count}, "
            f"ws3={ws3.send_text.await_count}. This is the production "
            "symptom — the dict-based router only routes to the latest "
            "registered WS, silently dropping echoes for the other live "
            "WS objects in the same browser tab."
        )
        assert ws2.send_text.await_count == 1
        assert ws3.send_text.await_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_must_reach_all_live_WS_for_a_session(self) -> None:
        """The actual production bug (2026-04-27 dogfood):

        React multi-mount of TerminalView creates 2-3 simultaneous WS
        connections for the SAME session_id (workbench layout state
        change causes the component to re-render with a new key while
        the previous one's effect cleanup is still in flight). The
        old `dict[session_id] = ws` pattern stores ONLY the most recent
        WS. The browser pane the user is actually looking at and typing
        into is, in many of these races, NOT the WS that ended up in
        the dict — so `terminal_output` dispatched by the agent is
        delivered to a (still-open) WS that no visible TerminalView
        is reading from. Frontend `ws.recv` event never fires for that
        echo, predict gcStale fires after 1.5s, and the user sees
        "no echo, no input" while the agent has correctly emitted
        every byte.

        The contract this test pins: dispatch MUST reach EVERY live
        WS currently registered for the session. The router becomes
        a fan-out, not a single-slot.
        """
        router = TerminalSessionRouter()
        ws_a, ws_b, ws_c = _fake_ws(), _fake_ws(), _fake_ws()
        # Three mounts for the same session register concurrently:
        router.register("S", ws_a)
        router.register("S", ws_b)
        router.register("S", ws_c)
        # Agent emits one byte of echo:
        msg = {"type": "terminal_output", "payload": {"session_id": "S", "data": "a"}}
        delivered = await router.dispatch(msg)
        assert delivered is True
        # ALL three live WS must receive — we don't know which one the
        # user is actually looking at, so fan-out is the only way to
        # guarantee the echo reaches the visible pane.
        ws_a.send_text.assert_awaited_once_with(json.dumps(msg))
        ws_b.send_text.assert_awaited_once_with(json.dumps(msg))
        ws_c.send_text.assert_awaited_once_with(json.dumps(msg))

    @pytest.mark.asyncio
    async def test_current_ws_disconnect_clears_dict(self) -> None:
        """Confirm the legitimate path still works: the WS currently
        registered for the session disconnects → dict cleared."""
        router = TerminalSessionRouter()
        ws = _fake_ws()
        router.register("S", ws)
        router.unregister("S", ws)
        msg = {"type": "terminal_output", "payload": {"session_id": "S", "data": "x"}}
        delivered = await router.dispatch(msg)
        assert delivered is False
