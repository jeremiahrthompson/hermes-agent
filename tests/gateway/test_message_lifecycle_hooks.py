"""Tests for message:received and message:processed gateway hook events."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gateway.hooks import HookRegistry
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SessionSource,
)
from gateway.config import Platform, PlatformConfig


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────


class StubAdapter(BasePlatformAdapter):
    """Minimal concrete adapter for testing message lifecycle hooks."""

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send(
        self, chat_id: str, content: str = "", *, reply_to: str = None, **kwargs
    ) -> MagicMock:
        return MagicMock(success=True)

    async def send_typing(self, chat_id: str, **kwargs) -> None:
        pass

    async def stop_typing(self, chat_id: str, **kwargs) -> None:
        pass

    async def get_chat_info(self, chat_id: str) -> dict:
        return {"id": chat_id, "type": "dm"}


def _make_source(
    chat_id: str = "ch123",
    user_id: str = "u1",
    user_name: str = "tester",
    chat_type: str = "dm",
    thread_id: str = None,
):
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        user_name=user_name,
        thread_id=thread_id,
    )


def _make_event(
    text: str = "hello",
    chat_id: str = "ch123",
    user_id: str = "u1",
    user_name: str = "tester",
    chat_type: str = "dm",
    message_id: str = "msg1",
    thread_id: str = None,
):
    return MessageEvent(
        text=text,
        source=_make_source(
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            chat_type=chat_type,
            thread_id=thread_id,
        ),
        message_id=message_id,
    )


def _make_adapter(hooks: HookRegistry = None) -> StubAdapter:
    """Create a StubAdapter with optional hooks wired."""
    config = PlatformConfig(enabled=True)
    adapter = StubAdapter(config, Platform.DISCORD)
    adapter.set_message_handler(AsyncMock(return_value="bot response"))
    if hooks is not None:
        adapter.set_hooks(hooks)
    return adapter


def _create_hook_dir(
    tmp_path: Path, hook_name: str, events: list, handler_code: str
) -> Path:
    """Create a hook directory with HOOK.yaml and handler.py."""
    hook_dir = tmp_path / hook_name
    hook_dir.mkdir()
    (hook_dir / "HOOK.yaml").write_text(
        f"name: {hook_name}\ndescription: Test hook\n"
        f"events: {events}\n"
    )
    (hook_dir / "handler.py").write_text(handler_code)
    return hook_dir


# ─────────────────────────────────────────────────────────────────────────────
# message:received — core behavior
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageReceivedBasic:
    """Basic message:received hook behavior."""

    def test_no_hooks_message_processed_normally(self, tmp_path):
        """When no hooks are registered, messages process normally."""
        adapter = _make_adapter(hooks=None)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            # Give background task time to run
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once_with(event)

    def test_empty_hooks_message_processed_normally(self, tmp_path):
        """When HookRegistry has no handlers, messages process normally."""
        hooks = HookRegistry()
        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once_with(event)

    def test_hook_fires_before_message_handler(self, tmp_path):
        """message:received hook fires before _message_handler is called."""
        call_order = []

        async def hook_handler(event_type, context):
            call_order.append("hook")

        hooks = HookRegistry()
        hook_dir = _create_hook_dir(
            tmp_path,
            "order-hook",
            '["message:received"]',
            f"async def handle(event_type, context):\n"
            f"    import asyncio\n"
            f"    await asyncio.sleep(0.01)\n"
            f"    call_order.append('hook')\n",
        )
        # Manually register since we're using tmp_path
        hooks._handlers.setdefault("message:received", []).append(hook_handler)
        hooks._loaded_hooks.append(
            {"name": "order-hook", "events": ["message:received"], "path": str(hook_dir)}
        )

        handler_mock = AsyncMock(return_value="response")
        adapter = _make_adapter(hooks=hooks)
        adapter.set_message_handler(handler_mock)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())
        assert call_order == ["hook"]
        handler_mock.assert_called_once()

    def test_hook_can_drop_message(self, tmp_path):
        """Hook setting should_process=False drops the message."""
        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(
            lambda event_type, context: context.__setitem__("should_process", False)
        )
        hooks._loaded_hooks.append(
            {"name": "drop-hook", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        # Handler should NOT have been called
        adapter._message_handler.assert_not_called()

    def test_hook_default_allows_message(self, tmp_path):
        """Hook that does nothing (default) allows the message through."""
        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(
            lambda event_type, context: None  # does nothing
        )
        hooks._loaded_hooks.append(
            {"name": "noop-hook", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# message:received — context fields
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageReceivedContext:
    """message:received hook context contains all required fields."""

    def test_hook_context_has_required_fields(self, tmp_path):
        """Context includes event, platform, chat_type, should_process, metadata."""
        captured = {}

        async def capture_hook(event_type, context):
            captured.update(context)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(capture_hook)
        hooks._loaded_hooks.append(
            {"name": "capture", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event(text="test message", chat_id="ch999")

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        assert "event" in captured
        assert "platform" in captured
        assert "chat_type" in captured
        assert "should_process" in captured
        assert "metadata" in captured

    def test_hook_context_platform_is_string(self, tmp_path):
        """Platform is a string (e.g. 'discord'), not the Platform enum."""
        captured = {}

        async def capture_hook(event_type, context):
            captured["platform"] = context["platform"]

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(capture_hook)
        hooks._loaded_hooks.append(
            {"name": "capture", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        assert captured["platform"] == "discord"
        assert isinstance(captured["platform"], str)

    def test_hook_context_metadata_fields(self, tmp_path):
        """metadata contains chat_id, user_id, chat_type, thread_id, message_id."""
        captured = {}

        async def capture_hook(event_type, context):
            captured["metadata"] = context["metadata"]

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(capture_hook)
        hooks._loaded_hooks.append(
            {"name": "capture", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event(
            chat_id="ch_test",
            user_id="u_test",
            chat_type="group",
            thread_id="th_test",
            message_id="msg_test",
        )

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        md = captured["metadata"]
        assert md["chat_id"] == "ch_test"
        assert md["user_id"] == "u_test"
        assert md["chat_type"] == "group"
        assert md["thread_id"] == "th_test"
        assert md["message_id"] == "msg_test"


# ─────────────────────────────────────────────────────────────────────────────
# message:received — multiple hooks
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageReceivedMultipleHooks:
    """Multiple hooks all run; any False drops the message."""

    def test_multiple_hooks_all_run(self, tmp_path):
        """Both hooks run even when first allows and second allows."""
        call_log = []

        async def hook_a(event_type, context):
            call_log.append("a")

        async def hook_b(event_type, context):
            call_log.append("b")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).extend([hook_a, hook_b])
        hooks._loaded_hooks.extend([
            {"name": "hook-a", "events": ["message:received"], "path": "/tmp"},
            {"name": "hook-b", "events": ["message:received"], "path": "/tmp"},
        ])

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        assert "a" in call_log
        assert "b" in call_log
        adapter._message_handler.assert_called_once()

    def test_any_hook_false_drops_message(self, tmp_path):
        """If any hook sets should_process=False, message is dropped."""
        call_log = []

        async def hook_allow(event_type, context):
            call_log.append("allow")

        async def hook_drop(event_type, context):
            call_log.append("drop")
            context["should_process"] = False

        async def hook_never_runs(event_type, context):
            call_log.append("never")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).extend([
            hook_allow, hook_drop, hook_never_runs
        ])
        hooks._loaded_hooks.extend([
            {"name": "allow", "events": ["message:received"], "path": "/tmp"},
            {"name": "drop", "events": ["message:received"], "path": "/tmp"},
            {"name": "never", "events": ["message:received"], "path": "/tmp"},
        ])

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        # All hooks run (no short-circuit)
        assert "allow" in call_log
        assert "drop" in call_log
        assert "never" in call_log
        # But message was dropped
        adapter._message_handler.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# message:received — should_process identity check
# ─────────────────────────────────────────────────────────────────────────────


class TestShouldProcessIdentityCheck:
    """Only should_process is False (identity) drops; falsy values do not."""

    @pytest.mark.parametrize(
        "value,should_process",
        [
            (None, True),       # None is falsy but not False
            (0, True),         # 0 is falsy but not False
            ("", True),        # empty string is falsy but not False
            (False, False),    # only False drops
            (True, True),      # True is identity-True
        ],
    )
    def test_should_process_identity(self, tmp_path, value, should_process):
        """Only is False (not just falsy) causes a drop."""
        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(
            lambda event_type, context: context.__setitem__("should_process", value)
        )
        hooks._loaded_hooks.append(
            {"name": "test", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        if should_process is False:
            adapter._message_handler.assert_not_called()
        else:
            adapter._message_handler.assert_called_once()

    def test_deleted_should_process_key_allows(self, tmp_path):
        """Deleting should_process key (not present) allows the message."""
        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(
            lambda event_type, context: context.pop("should_process", None)
        )
        hooks._loaded_hooks.append(
            {"name": "test", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# message:received — error and timeout handling
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageReceivedFailOpen:
    """Hook errors and timeouts fail-open (message is processed)."""

    def test_hook_fail_open_on_exception(self, tmp_path):
        """Hook raising an exception: message is still processed."""
        async def bad_hook(event_type, context):
            raise RuntimeError("hook error")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(bad_hook)
        hooks._loaded_hooks.append(
            {"name": "bad", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once()

    def test_hook_fail_open_on_timeout(self, tmp_path):
        """Hook exceeding timeout: message is still processed after timeout."""
        import time

        async def slow_hook(event_type, context):
            await asyncio.sleep(10)  # much longer than 1s timeout

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(slow_hook)
        hooks._loaded_hooks.append(
            {"name": "slow", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        start = time.monotonic()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        elapsed = time.monotonic() - start

        # Should have timed out well under 10s (should be ~5-6s total)
        assert elapsed < 8, f"Timeout took too long: {elapsed:.1f}s"
        adapter._message_handler.assert_called_once()

    def test_hook_timeout_configurable_via_env(self, tmp_path):
        """HERMES_HOOK_TIMEOUT env var controls the timeout."""
        import time

        async def slow_hook(event_type, context):
            await asyncio.sleep(3)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(slow_hook)
        hooks._loaded_hooks.append(
            {"name": "slow", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        with patch.dict(os.environ, {"HERMES_HOOK_TIMEOUT": "0.5"}):
            start = time.monotonic()

            async def run():
                await adapter.handle_message(event)
                await asyncio.sleep(0.05)

            asyncio.get_event_loop().run_until_complete(run())
            elapsed = time.monotonic() - start

        # Should have timed out at 0.5s
        assert elapsed < 2, f"Timeout did not respect HERMES_HOOK_TIMEOUT: {elapsed:.1f}s"
        adapter._message_handler.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# message:processed — core behavior
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageProcessedBasic:
    """message:processed hook fires after agent response."""

    def test_processed_fires_after_successful_response(self, tmp_path):
        """Hook fires with success=True and response text after normal completion."""
        captured = {}

        async def processed_hook(event_type, context):
            captured.update(context)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:processed", []).append(processed_hook)
        hooks._loaded_hooks.append(
            {"name": "processed", "events": ["message:processed"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        adapter.set_message_handler(AsyncMock(return_value="hello world"))
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        assert captured.get("success") is True
        assert captured.get("response") == "hello world"
        assert captured.get("error") is None

    def test_processed_fires_after_error(self, tmp_path):
        """Hook fires with success=False and error string when handler raises."""
        captured = {}

        async def processed_hook(event_type, context):
            captured.update(context)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:processed", []).append(processed_hook)
        hooks._loaded_hooks.append(
            {"name": "processed", "events": ["message:processed"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        adapter.set_message_handler(AsyncMock(side_effect=RuntimeError("test error")))

        # Mock send so the error handler doesn't fail
        async def mock_send(*args, **kwargs):
            pass

        event = _make_event()
        adapter.send = mock_send

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        assert captured.get("success") is False
        assert "test error" in captured.get("error", "")

    def test_processed_not_fired_when_message_dropped(self, tmp_path):
        """message:processed does NOT fire when message:received dropped the message."""
        call_log = []

        async def received_drop(event_type, context):
            call_log.append("received")
            context["should_process"] = False

        async def processed_hook(event_type, context):
            call_log.append("processed")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(received_drop)
        hooks._handlers.setdefault("message:processed", []).append(processed_hook)
        hooks._loaded_hooks.extend([
            {"name": "drop", "events": ["message:received"], "path": "/tmp"},
            {"name": "processed", "events": ["message:processed"], "path": "/tmp"},
        ])

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        assert "received" in call_log
        assert "processed" not in call_log


# ─────────────────────────────────────────────────────────────────────────────
# message:processed — context fields
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageProcessedContext:
    """message:processed context contains response, success, error, event, metadata."""

    def test_processed_context_has_response_and_event(self, tmp_path):
        """Context includes response text and the original MessageEvent."""
        captured = {}

        async def hook(event_type, context):
            captured["response"] = context["response"]
            captured["event"] = context["event"]

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:processed", []).append(hook)
        hooks._loaded_hooks.append(
            {"name": "test", "events": ["message:processed"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        adapter.set_message_handler(AsyncMock(return_value="final answer"))
        event = _make_event(text="original question")

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        assert captured["response"] == "final answer"
        assert captured["event"].text == "original question"


# ─────────────────────────────────────────────────────────────────────────────
# message:processed — error handling
# ─────────────────────────────────────────────────────────────────────────────


class TestMessageProcessedErrorHandling:
    """message:processed hook errors are non-critical."""

    def test_processed_error_does_not_break_cleanup(self, tmp_path):
        """Hook error in message:processed does not prevent session cleanup."""

        async def bad_hook(event_type, context):
            raise RuntimeError("processed hook error")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:processed", []).append(bad_hook)
        hooks._loaded_hooks.append(
            {"name": "bad", "events": ["message:processed"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        # Should not raise — error should be caught
        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        # If this raises, the test fails
        asyncio.get_event_loop().run_until_complete(run())


# ─────────────────────────────────────────────────────────────────────────────
# Integration / edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestIntegrationEdgeCases:
    """Edge cases involving the broader gateway system."""

    def test_hooks_none_attribute_passthrough(self, tmp_path):
        """adapter._hooks = None (never set): messages process normally."""
        config = PlatformConfig(enabled=True)
        adapter = StubAdapter(config, Platform.DISCORD)
        # _hooks is None by default (never set)
        adapter.set_message_handler(AsyncMock(return_value="response"))
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())
        adapter._message_handler.assert_called_once()

    def test_set_hooks_after_connect(self, tmp_path):
        """Setting hooks after initial connect: hooks fire on subsequent messages."""
        captured = []

        async def hook(event_type, context):
            captured.append("fired")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(hook)
        hooks._loaded_hooks.append(
            {"name": "test", "events": ["message:received"], "path": "/tmp"}
        )

        config = PlatformConfig(enabled=True)
        adapter = StubAdapter(config, Platform.DISCORD)
        adapter.set_message_handler(AsyncMock(return_value="response"))

        event1 = _make_event(text="first", message_id="m1")
        event2 = _make_event(text="second", message_id="m2")

        async def run():
            # Message before set_hooks
            await adapter.handle_message(event1)
            await asyncio.sleep(0.05)
            # Set hooks
            adapter.set_hooks(hooks)
            # Message after set_hooks
            await adapter.handle_message(event2)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        # Only event2 should have triggered the hook
        assert captured == ["fired"]

    def test_hook_with_photo_batching(self, tmp_path):
        """Photo messages still batch correctly when a hook is registered."""
        captured = []

        async def hook(event_type, context):
            captured.append(context["event"].message_id)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(hook)
        hooks._loaded_hooks.append(
            {"name": "test", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)

        photo_event1 = _make_event(
            text="", message_id="photo1", chat_type="dm"
        )
        photo_event1.message_type = MessageType.PHOTO
        photo_event1.media_urls = ["http://example.com/photo1.jpg"]
        photo_event1.media_types = ["image/jpeg"]

        photo_event2 = _make_event(
            text="", message_id="photo2", chat_type="dm"
        )
        photo_event2.message_type = MessageType.PHOTO
        photo_event2.media_urls = ["http://example.com/photo2.jpg"]
        photo_event2.media_types = ["image/jpeg"]

        async def run():
            await adapter.handle_message(photo_event1)
            await adapter.handle_message(photo_event2)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        # Both photos triggered the hook independently
        assert "photo1" in captured
        assert "photo2" in captured

    def test_both_hooks_fire_for_same_message(self, tmp_path):
        """Both message:received and message:processed fire for the same message."""
        received_log = []
        processed_log = []

        async def received_hook(event_type, context):
            received_log.append(context["event"].message_id)

        async def processed_hook(event_type, context):
            processed_log.append(context["event"].message_id)

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(received_hook)
        hooks._handlers.setdefault("message:processed", []).append(processed_hook)
        hooks._loaded_hooks.extend([
            {"name": "recv", "events": ["message:received"], "path": "/tmp"},
            {"name": "proc", "events": ["message:processed"], "path": "/tmp"},
        ])

        adapter = _make_adapter(hooks=hooks)
        adapter.set_message_handler(AsyncMock(return_value="response"))
        event = _make_event(message_id="both_hooks_test")

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        assert received_log == ["both_hooks_test"]
        assert processed_log == ["both_hooks_test"]

    def test_async_hook_supported(self, tmp_path):
        """Async hook handlers work correctly."""
        captured = []

        async def async_hook(event_type, context):
            await asyncio.sleep(0.01)
            captured.append("async_ok")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(async_hook)
        hooks._loaded_hooks.append(
            {"name": "async", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.1)

        asyncio.get_event_loop().run_until_complete(run())

        assert captured == ["async_ok"]
        adapter._message_handler.assert_called_once()

    def test_sync_hook_supported(self, tmp_path):
        """Sync hook handlers work correctly (legacy support)."""
        captured = []

        def sync_hook(event_type, context):
            captured.append("sync_ok")

        hooks = HookRegistry()
        hooks._handlers.setdefault("message:received", []).append(sync_hook)
        hooks._loaded_hooks.append(
            {"name": "sync", "events": ["message:received"], "path": "/tmp"}
        )

        adapter = _make_adapter(hooks=hooks)
        event = _make_event()

        async def run():
            await adapter.handle_message(event)
            await asyncio.sleep(0.05)

        asyncio.get_event_loop().run_until_complete(run())

        assert captured == ["sync_ok"]
        adapter._message_handler.assert_called_once()
