"""Widget forms for ``notebooks/local_sre_agent.ipynb``.

These widgets only handle UI plumbing — text inputs, buttons, status output.
All Coral CLI calls happen directly in notebook cells (``!coral source add …``)
so first-time readers can see the commands being run."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import os

import ipywidgets as widgets

from sre_agent.local.onboarding import update_env

_MAC_INSTALL = "brew install withcoral/tap/coral"
_LINUX_INSTALL = "curl -fsSL https://withcoral.com/install.sh | sh"

_INPUT_LAYOUT = widgets.Layout(width="540px")
_INPUT_STYLE = {"description_width": "140px"}


def _password(description: str, env_key: str, placeholder: str = "") -> widgets.Password:
    return widgets.Password(
        description=description,
        value=os.getenv(env_key, ""),
        placeholder=placeholder,
        layout=_INPUT_LAYOUT,
        style=_INPUT_STYLE,
    )


def _text(description: str, env_key: str, default: str = "", placeholder: str = "") -> widgets.Text:
    return widgets.Text(
        description=description,
        value=os.getenv(env_key, default),
        placeholder=placeholder,
        layout=_INPUT_LAYOUT,
        style=_INPUT_STYLE,
    )


def install_coral_form() -> widgets.Widget:
    """Two buttons — *Install on macOS* and *Install on Linux* — that run the
    documented install commands with live output streaming.

    A status banner above the buttons shows whether ``coral`` is already on
    ``PATH`` so readers who already have Coral installed know the buttons are
    optional."""
    status = widgets.HTML()
    mac_btn = widgets.Button(
        description="Install on macOS (brew)",
        button_style="primary",
        icon="apple",
        layout=widgets.Layout(width="260px"),
    )
    linux_btn = widgets.Button(
        description="Install on Linux (curl)",
        button_style="primary",
        icon="linux",
        layout=widgets.Layout(width="260px"),
    )
    output = widgets.Output()

    def refresh_status() -> None:
        path = shutil.which("coral")
        if path:
            version = subprocess.run(
                ["coral", "--version"], capture_output=True, text=True, check=False
            )
            v = (version.stdout or version.stderr).strip() or "coral"
            status.value = (
                f"<p style='color:#2a8a2a;margin:0 0 8px'>✓ Coral is installed: "
                f"<code>{v}</code> &nbsp;·&nbsp; <code>{path}</code></p>"
            )
        else:
            status.value = (
                "<p style='color:#c47700;margin:0 0 8px'>⚠ <code>coral</code> "
                "is not on your PATH. Click the install command for your OS, or "
                "run it in your terminal.</p>"
            )

    async def run(cmd: str) -> None:
        mac_btn.disabled = True
        linux_btn.disabled = True
        original_mac = mac_btn.description
        original_linux = linux_btn.description
        mac_btn.description = "Installing…"
        output.clear_output()
        with output:
            print(f"$ {cmd}\n", flush=True)
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    print(line.decode(errors="replace"), end="", flush=True)
                returncode = await proc.wait()
                print(f"\n[exit {returncode}]")
                if returncode != 0:
                    print(
                        "\nIf the install needed sudo or a TTY, run the command "
                        "in your terminal instead."
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"✗ {type(exc).__name__}: {exc}")
        mac_btn.description = original_mac
        linux_btn.description = original_linux
        mac_btn.disabled = False
        linux_btn.disabled = False
        refresh_status()

    mac_btn.on_click(lambda _b: asyncio.ensure_future(run(_MAC_INSTALL)))
    linux_btn.on_click(lambda _b: asyncio.ensure_future(run(_LINUX_INSTALL)))

    refresh_status()
    return widgets.VBox([status, widgets.HBox([mac_btn, linux_btn]), output])


def anthropic_key_form() -> widgets.Widget:
    """Password field + Save button for ``ANTHROPIC_API_KEY``."""
    key_input = _password("API Key:", "ANTHROPIC_API_KEY", "sk-ant-...")
    save_btn = widgets.Button(description="Save", button_style="primary", icon="check")
    output = widgets.Output()

    def on_save(_b: widgets.Button) -> None:
        output.clear_output()
        value = key_input.value.strip()
        with output:
            if not value:
                print("⚠ Paste your Anthropic API key first.")
                return
            update_env("ANTHROPIC_API_KEY", value)
            print("✓ Saved to .env")

    save_btn.on_click(on_save)
    return widgets.VBox([key_input, save_btn, output])


def credentials_form() -> widgets.Widget:
    """One consolidated form for all four provider credentials.

    Each provider is a small section. Leave a section blank to skip that source —
    the Save button only writes the values you actually entered. Saved values land
    in both ``.env`` (so kernel restarts don't lose them) and ``os.environ`` (so
    the next ``!coral source add`` cell inherits them)."""
    inputs: dict[str, widgets.Widget] = {
        "DD_API_KEY":   _password("API Key:", "DD_API_KEY"),
        "DD_APPLICATION_KEY":   _password("App Key:", "DD_APPLICATION_KEY"),
        "DD_SITE":      _text("Site (optional):", "DD_SITE", default="datadoghq.com"),
        "GITHUB_TOKEN": _password("Token:", "GITHUB_TOKEN", "ghp_… or github_pat_…"),
        "SENTRY_TOKEN": _password("Token:", "SENTRY_TOKEN"),
        "SENTRY_ORG":   _text("Org:", "SENTRY_ORG", placeholder="acme"),
        "SLACK_TOKEN":  _password("Bot Token:", "SLACK_TOKEN", "xoxb-…"),
    }

    sections: list[tuple[str, list[str]]] = [
        ("Datadog", ["DD_API_KEY", "DD_APPLICATION_KEY", "DD_SITE"]),
        ("GitHub",  ["GITHUB_TOKEN"]),
        ("Sentry",  ["SENTRY_TOKEN", "SENTRY_ORG"]),
        ("Slack",   ["SLACK_TOKEN"]),
    ]

    children: list[widgets.Widget] = []
    for provider, keys in sections:
        children.append(widgets.HTML(f"<h4 style='margin:8px 0 4px'>{provider}</h4>"))
        children.extend(inputs[k] for k in keys)

    save_btn = widgets.Button(description="Save credentials", button_style="primary", icon="check")
    output = widgets.Output()

    def on_save(_b: widgets.Button) -> None:
        output.clear_output()
        saved: list[str] = []
        with output:
            for env_key, widget in inputs.items():
                value = widget.value.strip()
                if value:
                    update_env(env_key, value)
                    saved.append(env_key)
            if saved:
                print(f"✓ Saved {len(saved)} value(s) to .env: {', '.join(saved)}")
            else:
                print("○ No values entered — nothing saved.")

    save_btn.on_click(on_save)
    return widgets.VBox([*children, save_btn, output])
