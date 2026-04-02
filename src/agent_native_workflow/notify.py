"""Desktop notifications for pipeline completion."""

import platform
import subprocess


def send_notification(title: str, body: str) -> None:
    """Send a desktop notification (macOS/Linux, silently no-op elsewhere)."""
    system = platform.system()
    try:
        if system == "Darwin":
            # macOS: use osascript; escape quotes to prevent injection
            safe_title = title.replace('"', '\\"')
            safe_body = body.replace('"', '\\"')
            script = f'display notification "{safe_body}" with title "{safe_title}"'
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                timeout=5,
            )
        elif system == "Linux":
            # Linux: notify-send (best-effort; args are safe from injection)
            subprocess.run(
                ["notify-send", title, body],
                check=False,
                capture_output=True,
                timeout=5,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        pass  # Notification failed; silently ignore to never block the pipeline
