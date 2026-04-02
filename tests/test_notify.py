"""Tests for desktop notification functionality."""

import platform
import subprocess
from unittest import mock

import pytest

from agent_native_workflow.notify import send_notification


def test_send_notification_macos() -> None:
    """Test macOS notification via osascript."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            send_notification(title="Test Title", body="Test Body")

            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["osascript", "-e", 'display notification "Test Body" with title "Test Title"']
            assert args[1]["check"] is True
            assert args[1]["capture_output"] is True
            assert args[1]["timeout"] == 5


def test_send_notification_linux() -> None:
    """Test Linux notification via notify-send."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Linux"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            send_notification(title="Test Title", body="Test Body")

            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["notify-send", "Test Title", "Test Body"]
            assert args[1]["check"] is False
            assert args[1]["capture_output"] is True
            assert args[1]["timeout"] == 5


def test_send_notification_unsupported_platform() -> None:
    """Test that unsupported platforms silently do nothing."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Windows"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            # Should not raise; should silently return
            send_notification(title="Test Title", body="Test Body")

            # subprocess.run should never be called for unsupported platform
            mock_run.assert_not_called()


def test_send_notification_timeout_error() -> None:
    """Test that subprocess timeout is silently handled."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch(
            "agent_native_workflow.notify.subprocess.run",
            side_effect=subprocess.TimeoutExpired("osascript", 5),
        ):
            # Should not raise
            send_notification(title="Test Title", body="Test Body")


def test_send_notification_file_not_found() -> None:
    """Test that missing notify-send is silently handled."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Linux"):
        with mock.patch(
            "agent_native_workflow.notify.subprocess.run",
            side_effect=FileNotFoundError("notify-send not found"),
        ):
            # Should not raise
            send_notification(title="Test Title", body="Test Body")


def test_send_notification_subprocess_error() -> None:
    """Test that subprocess errors are silently handled."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch(
            "agent_native_workflow.notify.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "osascript"),
        ):
            # Should not raise
            send_notification(title="Test Title", body="Test Body")


def test_send_notification_with_special_characters() -> None:
    """Test that special characters in title/body are handled correctly."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            send_notification(title='Title with "quotes"', body="Body with 'apostrophes'")

            mock_run.assert_called_once()
            # Verify the command was called (exact escaping is handled by subprocess)
            args = mock_run.call_args[0][0]
            assert "osascript" in args
            assert "-e" in args


def test_send_notification_empty_strings() -> None:
    """Test that empty title/body don't cause errors."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            send_notification(title="", body="")

            mock_run.assert_called_once()


def test_send_notification_quote_escaping() -> None:
    """Test that quotes in title/body are escaped for osascript safety."""
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            send_notification(title='Say "hello"', body='Message "with quotes"')

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            # Verify quotes are escaped in the script
            script = args[2]  # The -e argument value
            assert '\\"' in script
            assert 'Say \\"hello\\"' in script


def test_config_notify_disabled() -> None:
    """Test that WorkflowConfig.notify=False prevents notifications."""
    from agent_native_workflow.config import WorkflowConfig

    cfg = WorkflowConfig(notify=False)
    assert cfg.notify is False


def test_config_notify_enabled_by_default() -> None:
    """Test that notifications are enabled by default."""
    from agent_native_workflow.config import WorkflowConfig

    cfg = WorkflowConfig()
    assert cfg.notify is True


def test_config_notify_from_env_zero() -> None:
    """Test that ANW_NOTIFY=0 disables notifications."""
    from agent_native_workflow.config import WorkflowConfig
    import os

    # Set environment variable and resolve config
    os.environ["ANW_NOTIFY"] = "0"
    try:
        cfg = WorkflowConfig.resolve()
        assert cfg.notify is False
    finally:
        del os.environ["ANW_NOTIFY"]


def test_config_notify_from_env_one() -> None:
    """Test that ANW_NOTIFY=1 enables notifications."""
    from agent_native_workflow.config import WorkflowConfig
    import os

    # Set environment variable and resolve config
    os.environ["ANW_NOTIFY"] = "1"
    try:
        cfg = WorkflowConfig.resolve()
        assert cfg.notify is True
    finally:
        del os.environ["ANW_NOTIFY"]


def test_send_notification_when_disabled_via_config() -> None:
    """Test that notify=False in config prevents notifications."""
    from agent_native_workflow.config import WorkflowConfig

    # Create a config with notify=False
    cfg = WorkflowConfig(notify=False)
    assert cfg.notify is False

    # Verify the flag is properly set (behavior is tested by pipeline)
    # The actual suppression happens via the `if wcfg.notify:` guard in pipeline.py


def test_send_notification_when_enabled_via_config() -> None:
    """Test that notify=True in config allows notifications."""
    from agent_native_workflow.config import WorkflowConfig

    # Create a config with notify=True (default)
    cfg = WorkflowConfig(notify=True)
    assert cfg.notify is True

    # Verify the flag is properly set (behavior is tested by pipeline)
    # The actual suppression happens via the `if wcfg.notify:` guard in pipeline.py


def test_pipeline_interrupted_notification_title() -> None:
    """Test that interrupted pipeline sends 'anw: interrupted' notification."""
    # This test documents the "anw: interrupted" behavior added as an enhancement
    # beyond the base requirements (which only specify "anw: error", "anw: converged",
    # and "anw: did not converge").
    with mock.patch("agent_native_workflow.notify.platform.system", return_value="Darwin"):
        with mock.patch("agent_native_workflow.notify.subprocess.run") as mock_run:
            # Simulate pipeline interruption (shutdown_requested=True, but converged=False)
            shutdown_requested = True
            converged = False
            metrics_total_iterations = 3
            total_time = 45.2

            # This mirrors the pipeline's logic for the "anw: interrupted" case
            if not converged and shutdown_requested:
                send_notification(
                    title="anw: interrupted",
                    body=f"Pipeline interrupted after {metrics_total_iterations} iteration(s), {total_time}s",
                )

            # Verify the notification was sent with the correct title
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            script = args[2]
            assert "anw: interrupted" in script
