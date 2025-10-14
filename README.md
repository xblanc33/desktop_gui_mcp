# Desktop GUI MCP

This project implements a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes a curated subset of [PyAutoGUI](https://pyautogui.readthedocs.io/en/latest/) automation utilities. It allows an MCP-compatible client (for example, OpenAI's desktop app) to request mouse and keyboard actions, capture screenshots, and query screen metadata through well-defined tools.

## Features

- Mouse control: move the pointer, click, drag, scroll.
- Keyboard input: type text and press key combinations.
- Keyboard layout detection: report the currently active input source (EN, FR, etc.).
- Display helpers: retrieve screen size, take screenshots (returned inline as base64).
- Safe execution: configurable delays and optional failsafe disabling.
- Uniform JSON responses that include operation status, a short summary, and (when using the screenshot tool) a base64-encoded capture.

## Available tools

- `desktop_move_mouse`: Move the desktop pointer to specific coordinates.
- `desktop_mouse_click`: Click mouse buttons at the current or specified position.
- `desktop_mouse_drag`: Drag the mouse between coordinates.
- `desktop_type_text`: Type strings and optionally press Enter, using layout-aware Unicode injection across platforms.
- `desktop_press_keys`: Press keys sequentially or as a hotkey combination (key names are case-insensitive; e.g. `Command`, `cmd`, and `command` map to the same key). Sequential character typing honours the active keyboard layout across platforms (macOS uses Unicode events with an AppleScript fallback, Windows uses `SendInput` with Unicode scans, Linux prefers `xdotool` when available).
- `desktop_get_screen_size`: Report the primary display resolution.
- `desktop_capture_screenshot`: Capture desktop screenshots (JPEG encoded in base64), optionally for a region, defaulting to aggressive palette-based compression (quality `5`, 32-colour quantization) with tunable parameters when you need more fidelity.
- `desktop_get_keyboard_layout`: Return metadata about the active keyboard layout/input source.

## Requirements

- Python 3.10 or newer.
- PyAutoGUI's system-level dependencies (Pillow, PyScreeze, etc.), which pip installs automatically on supported platforms.
- An MCP client capable of loading a local server.
- On macOS, grant Accessibility permission to the Python executable (or the terminal app launching the server) so synthetic mouse and keyboard events are delivered system-wide.
- For Linux, installing `xdotool` enables layout-aware text typing; otherwise the server falls back to clipboard-based injection.
- Optional: install `pyperclip` to enable clipboard-based text entry when native Unicode typing is unavailable.
- Optional: install `jpegtran`/`mozjpeg` if you plan to post-process screenshots further; the server already supports aggressive in-process JPEG compression.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

Update

```bash
pip install -e .
python -m compileall desktop_gui_mcp/server.py
```

## Running the server

When the package is installed, the console script `desktop-gui-mcp` becomes available. You can also invoke the module directly:

```bash
python -m desktop_gui_mcp
```

The executable listens to stdin/stdout as required by the MCP Server specification, so it is typically launched by an MCP client rather than manually in a terminal.

## Using with clients

### Gemini CLI

1. Make sure the package (and its dependencies Pillow and PyScreeze) is installed in the Python environment the CLI uses (`pip install desktop-gui-mcp` or `pip install -e .` from this repo).
2. Edit your Gemini CLI configuration (commonly `~/.config/gemini-cli/config.json`) and add a new entry under `mcpServers`:

   ```json
   {
     "mcpServers": {
       "desktop-gui": {
         "command": "python",
         "args": ["-m", "desktop_gui_mcp"],
         "cwd": "/Users/your-user/devs/desktop_gui_mcp"
       }
     }
   }
   ```

3. Restart the CLI; the server appears as `desktop-gui` in the available MCP tools list.

### Codex CLI

1. Install the project into the environment backing Codex CLI.
2. If you use a virtual environment, point Codex at the interpreter inside it (replace `/Users/your-user/devs/desktop_gui_mcp/.venv/bin/python` with your path). Append a server definition to the CLI configuration file (typically `~/.config/codex/config.toml`):

   ```toml
   [mcpServers.desktop-gui]
   command = "/Users/your-user/devs/desktop_gui_mcp/.venv/bin/python"
   args = ["-m", "desktop_gui_mcp"]
   cwd = "/Users/your-user/devs/desktop_gui_mcp"
   ```

   Merge this snippet with your existing configuration if other servers are already defined.
3. Restart Codex CLI to pick up the new server. If you have multiple virtual environments, make sure to update the `command` path whenever you switch.
4. In Codex workflows, **never satisfy desktop/navigation requests by running shell commands** such as `open`, `start`, or `xdg-open`. Instead, call the Desktop GUI MCP tools (e.g., Spotlight via `desktop_press_keys(["command", "space"])`, then `desktop_type_text("PowerPoint", press_enter=True)`).
5. If a workflow template or policy would normally attempt a shell command, override it explicitly by stating that the Desktop GUI MCP will be used and then invoking the appropriate tools.

### Claude (desktop)

1. Install `desktop-gui-mcp` (ensuring Pillow and PyScreeze are available) into the Python environment on the machine running Claude Desktop.
2. Create a file such as `~/Library/Application Support/Claude/mcp/servers/desktop-gui.json` (macOS path; adjust for other OSes) with the contents:

   ```json
   {
     "name": "desktop-gui",
     "command": "python",
     "args": ["-m", "desktop_gui_mcp"],
     "cwd": "/Users/your-user/devs/desktop_gui_mcp"
   }
   ```

3. Relaunch Claude Desktop; the new MCP server is listed in the integrations panel.

## Response format

Every tool exposed by the server returns a JSON object with three fields:

```json
{
  "status": "success",
  "summary": "Human readable description of the action",
  "screenshot": "<base64-encoded image (when requested) or null>",
  "screenshot_dimensions": [1512, 982]
}
```

- `status` is `success` for normal completions (errors raise MCP tool failures).
- `summary` gives a concise description of what the tool did.
- `screenshot` holds a base64 string when generated by the `desktop_capture_screenshot` tool; other tools return `null`.
- `screenshot_dimensions` reports the `[width, height]` of the captured image (or `null` when no screenshot is attached).

## Keyboard layout detection

Use `desktop_get_keyboard_layout` when you need to confirm which keyboard layout the OS currently exposes (for example, to decide whether to send `azerty`-specific shortcuts). Typical responses look like:

```json
{
  "status": "success",
  "summary": "Keyboard layout detected: layout=French; input_source_id=com.apple.keylayout.French",
  "screenshot": null,
  "screenshot_dimensions": null
}
```

The exact keys returned in the summary differ by platform: macOS reports the input source identifier, Windows surfaces the HKL/locale code, and Linux reads `setxkbmap` output (falling back to the `LANG` environment variable if needed).

## Screenshot compression

Screenshots default to the most aggressive compression settings (palette mode with 32 colours and JPEG quality 5). If you need richer visuals:

- Switch to `color_mode="color"` for full RGB output, or `gray` for luminance-only.
- Increase `quality` (up to 95) or supply a larger `palette_size` when using palette mode.

These controls can be mixed—for example, `color_mode="color", quality=20`—to balance clarity and token footprint while preserving the original width and height.

## Debug mode

Set `DESKTOP_GUI_MCP_DEBUG=1` to capture a persistent trace of every tool call. When enabled, the server writes a timestamped log (and, for `desktop_capture_screenshot`, the JPEG and corresponding base64 payload) to `DESKTOP_GUI_MCP_DEBUG_DIR` (default `./desktop_gui_mcp_debug`). This is invaluable when auditing agent behaviour or replaying UI flows; remember to disable it once you finish debugging to avoid collecting unnecessary artifacts.

## Configuration

Runtime settings can be supplied via environment variables:

- `DESKTOP_GUI_MCP_PAUSE`: default pause between PyAutoGUI actions (seconds, float).
- `DESKTOP_GUI_MCP_FAILSAFE`: set to `0` to disable PyAutoGUI's corner failsafe.
- `DESKTOP_GUI_MCP_IMAGE_QUALITY`: JPEG quality (1-95, defaults to 5) for screenshot compression; raise it only when you need extra detail.
- `DESKTOP_GUI_MCP_SCREENSHOT_COLOR_MODE`: `color`, `gray`, or `palette` (default `palette`). Use `color` for full-fidelity captures or `gray` for luminance-only output.
- `DESKTOP_GUI_MCP_SCREENSHOT_PALETTE_SIZE`: Palette size (2-256, default 32) used when the color mode is `palette`.
- `DESKTOP_GUI_MCP_DEBUG`: Set to `1`/`true` to enable request tracing and artifact capture.
- `DESKTOP_GUI_MCP_DEBUG_DIR`: Directory for debug traces and persisted screenshots (defaults to `./desktop_gui_mcp_debug`).

Environment overrides can also be placed in a project-local `.env` file, which the server loads automatically on startup.

The server still honors the previous `PY_AUTO_GUI_MCP_*` names for backward compatibility, but these will be removed in a future release.

Use the `desktop_capture_screenshot` tool whenever you need visual confirmation after an action.
The `region` parameter for the screenshot tool accepts a list `[left, top, width, height]` describing the capture area.
Responses include both the base64 data and its associated dimensions.

Lower `DESKTOP_GUI_MCP_IMAGE_QUALITY` if the base64 data still exceeds client limits, or raise it slightly when you need more detail. Palette mode with a small `palette_size` is the default and offers significant savings; switch to `color` only when necessary. When colour information is not critical, `gray` provides another option. Screenshots are always encoded as JPEG, so alpha channels are discarded.

## Development

Install development dependencies:

```bash
pip install -e .[dev]
```

Useful commands:

- `ruff check desktop_gui_mcp`: static linting.
- `mypy desktop_gui_mcp`: type checking.

See `AGENT.md` for operational guidance targeted at agents orchestrating GUI automation with this server.

## Disclaimer

Mouse and keyboard automation can interfere with normal computer usage. Use this server with caution, preferably on a dedicated testing machine or within a virtual environment where unintended actions are harmless.
