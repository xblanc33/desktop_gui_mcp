"""MCP server exposing desktop automation helpers."""

from __future__ import annotations

import base64
import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal, Optional, Sequence, TypedDict

from PIL import Image

if sys.platform == "darwin":
    try:
        import Quartz  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        Quartz = None  # type: ignore[assignment]
else:  # pragma: no cover - platform specific
    Quartz = None  # type: ignore[assignment]

import pyautogui
from mcp.server.fastmcp import FastMCP

SERVER_NAME = "desktop-gui-mcp"
ENV_PREFIX = "DESKTOP_GUI_MCP"
SCREENSHOT_FORMAT = "JPEG"


def _get_env_var(name: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch an environment variable using the Desktop GUI MCP prefix."""

    return os.getenv(f"{ENV_PREFIX}_{name}", default)


_pause_raw = _get_env_var("PAUSE")
if _pause_raw is not None:
    try:
        pyautogui.PAUSE = max(0.0, float(_pause_raw))
    except ValueError as exc:
        raise ValueError("Environment variable DESKTOP_GUI_MCP_PAUSE must be numeric.") from exc

if _get_env_var("FAILSAFE", "1").lower() in {"0", "false", "no"}:
    pyautogui.FAILSAFE = False

_default_image_quality_raw = _get_env_var("IMAGE_QUALITY")
if _default_image_quality_raw is not None:
    try:
        _default_image_quality = int(_default_image_quality_raw)
    except ValueError as exc:
        raise ValueError(
            "Environment variable DESKTOP_GUI_MCP_IMAGE_QUALITY must be an integer."
        ) from exc
else:
    _default_image_quality = 5

_ALLOWED_COLOR_MODES = {"color", "gray", "palette"}
_default_color_mode_raw = _get_env_var("SCREENSHOT_COLOR_MODE", "palette").lower()
if _default_color_mode_raw not in _ALLOWED_COLOR_MODES:
    raise ValueError(
        "Environment variable DESKTOP_GUI_MCP_SCREENSHOT_COLOR_MODE must be one of "
        f"{sorted(_ALLOWED_COLOR_MODES)}."
    )
_default_color_mode = _default_color_mode_raw

_default_palette_size_raw = _get_env_var("SCREENSHOT_PALETTE_SIZE")
if _default_palette_size_raw is not None:
    try:
        _default_palette_size = max(2, min(256, int(_default_palette_size_raw)))
    except ValueError as exc:
        raise ValueError(
            "Environment variable DESKTOP_GUI_MCP_SCREENSHOT_PALETTE_SIZE must be an integer."
        ) from exc
else:
    _default_palette_size = 32

mcp_server = FastMCP(SERVER_NAME)


class ToolResponse(TypedDict):
    status: str
    summary: str
    screenshot: Optional[str]
    screenshot_dimensions: Optional[tuple[int, int]]


def _apply_color_mode(
    image,
    color_mode: str,
    palette_size: Optional[int],
):
    """Adjust image colors to improve compression while retaining dimensions."""

    if color_mode == "gray":
        return image.convert("L")

    if color_mode == "palette":
        palette = palette_size if palette_size is not None else _default_palette_size
        palette = max(2, min(256, int(palette)))
        return image.convert("RGB").quantize(colors=palette, method=Image.MEDIANCUT).convert("RGB")

    return image


def _encode_image_to_base64(
    image,
    quality: Optional[int] = None,
    *,
    color_mode: Optional[str] = None,
    palette_size: Optional[int] = None,
) -> str:
    """Encode a PIL image to a base64 string."""

    mode_to_use = (color_mode or _default_color_mode).lower()
    if mode_to_use not in _ALLOWED_COLOR_MODES:
        raise ValueError(f"Unsupported color mode: {mode_to_use}")

    image_to_save = _apply_color_mode(image, mode_to_use, palette_size)

    save_kwargs = {}
    quality_to_use = quality if quality is not None else _default_image_quality
    save_kwargs["quality"] = max(5, min(95, int(quality_to_use)))
    save_kwargs["optimize"] = True
    save_kwargs["progressive"] = True

    if image_to_save.mode not in {"RGB", "L"}:
        image_to_save = image_to_save.convert("RGB")

    buffer = BytesIO()
    image_to_save.save(buffer, format=SCREENSHOT_FORMAT, **save_kwargs)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _build_response(
    summary: str,
    *,
    status: str = "success",
    screenshot_b64: Optional[str] = None,
    dimensions: Optional[tuple[int, int]] = None,
) -> ToolResponse:
    return ToolResponse(
        status=status,
        summary=summary,
        screenshot=screenshot_b64,
        screenshot_dimensions=dimensions,
    )


def _normalize_region(
    region: Optional[Sequence[int]],
) -> Optional[tuple[int, int, int, int]]:
    if region is None:
        return None
    if len(region) != 4:
        raise ValueError("Region must contain exactly four integers: left, top, width, height.")
    left, top, width, height = (int(value) for value in region)
    return left, top, width, height


_COMMAND_OR_CONTROL_KEY = "command" if sys.platform == "darwin" else "ctrl"

# Map common user-facing key names to the canonical values expected by pyautogui.
_KEY_ALIASES = {
    "cmd": "command",
    "commandorcontrol": _COMMAND_OR_CONTROL_KEY,
    "control": "ctrl",
    "spacebar": "space",
    "return": "enter",
    "escape": "esc",
    "windows": "win",
    "meta": "command" if sys.platform == "darwin" else "win",
}

# pyautogui exposes the available key names for the current platform; use them to validate input.
_VALID_KEYS = set(getattr(pyautogui, "KEYBOARD_KEYS", []))


def _normalize_key_name(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("Key names must be strings.")

    if key == " ":
        normalized_key = "space"
    else:
        stripped = key.strip()
        if not stripped:
            raise ValueError("Key names must not be empty.")
        normalized_key = stripped.lower()
        normalized_key = _KEY_ALIASES.get(normalized_key, normalized_key)

    if _VALID_KEYS and normalized_key not in _VALID_KEYS:
        raise ValueError(f"Unsupported key for this platform: {normalized_key}")

    return normalized_key


def _normalize_keys(keys: Sequence[str]) -> list[str]:
    return [_normalize_key_name(key) for key in keys]


def _press_hotkey(keys: Sequence[str], interval: float) -> None:
    if len(keys) == 1:
        pyautogui.press(keys[0])
        return

    sleep_interval = interval if interval > 0 else max(0.02, pyautogui.PAUSE)
    pressed_keys: list[str] = []
    try:
        for key in keys:
            pyautogui.keyDown(key)
            pressed_keys.append(key)
            time.sleep(sleep_interval)
    finally:
        for key in reversed(pressed_keys):
            pyautogui.keyUp(key)
            time.sleep(sleep_interval)


def _keys_to_text(keys: Sequence[str]) -> Optional[str]:
    text_chars: list[str] = []
    for key in keys:
        if len(key) == 1:
            text_chars.append(key)
        elif key == "space":
            text_chars.append(" ")
        else:
            return None
    return "".join(text_chars)


def _type_text_macos_layout_aware(text: str, interval: float) -> bool:
    if Quartz is None:
        return _type_text_macos_via_osascript(text, interval)

    try:
        for index, char in enumerate(text):
            event_down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(event_down, len(char), char)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)

            event_up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(event_up, len(char), char)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)

            if index < len(text) - 1:
                sleep_interval = interval if interval > 0 else pyautogui.PAUSE
                if sleep_interval > 0:
                    time.sleep(sleep_interval)
    except Exception:  # noqa: BLE001
        return _type_text_macos_via_osascript(text, interval)

    return True


def _type_text_macos_via_osascript(text: str, interval: float) -> bool:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    sleep_interval = interval if interval > 0 else pyautogui.PAUSE
    if sleep_interval > 0:
        time.sleep(sleep_interval)

    return True


def _type_text_windows_layout_aware(text: str, interval: float) -> bool:
    if not sys.platform.startswith(("win", "cygwin")):
        return False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    if not user32:
        return False

    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.ULONG_PTR),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("ki", KEYBDINPUT),
        ]

    sleep_interval = interval if interval > 0 else pyautogui.PAUSE

    for index, char in enumerate(text):
        code = ord(char)
        down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0))
        up = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0),
        )
        if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT)) == 0:
            return False
        if user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 0:
            return False

        if index < len(text) - 1 and sleep_interval > 0:
            time.sleep(sleep_interval)

    return True


def _type_text_linux_layout_aware(text: str, interval: float) -> bool:
    if not sys.platform.startswith("linux"):
        return False

    xdotool_path = shutil.which("xdotool")
    if not xdotool_path:
        return False

    delay_ms = max(0, int((interval if interval > 0 else 0) * 1000))
    command = [xdotool_path, "type", "--clearmodifiers"]
    if delay_ms > 0:
        command.extend(["--delay", str(delay_ms)])
    command.append(text)
    try:
        subprocess.run(command, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    sleep_interval = interval if interval > 0 else pyautogui.PAUSE
    if sleep_interval > 0:
        time.sleep(sleep_interval)
    return True


def _type_text_clipboard_fallback(text: str, interval: float) -> bool:
    try:
        import pyperclip  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False

    previous_contents: Optional[str]
    try:
        previous_contents = pyperclip.paste()
    except Exception:  # noqa: BLE001
        previous_contents = None

    try:
        pyperclip.copy(text)
    except Exception:  # noqa: BLE001
        return False

    try:
        if sys.platform == "darwin":
            _press_hotkey(["command", "v"], interval if interval > 0 else 0.02)
        else:
            _press_hotkey(["ctrl", "v"], interval if interval > 0 else 0.02)
    finally:
        if previous_contents is not None:
            try:
                pyperclip.copy(previous_contents)
            except Exception:  # noqa: BLE001
                pass

    sleep_interval = interval if interval > 0 else pyautogui.PAUSE
    if sleep_interval > 0:
        time.sleep(sleep_interval)
    return True


def _type_text_with_layout_awareness(text: str, interval: float) -> None:
    platform = sys.platform
    typed = False
    if platform == "darwin":
        typed = _type_text_macos_layout_aware(text, interval)
    elif platform.startswith(("win", "cygwin")):
        typed = _type_text_windows_layout_aware(text, interval)
    elif platform.startswith("linux"):
        typed = _type_text_linux_layout_aware(text, interval)

    if not typed:
        typed = _type_text_clipboard_fallback(text, interval)

    if not typed:
        pyautogui.write(text, interval=interval)


def _detect_keyboard_layout_macos() -> Optional[dict[str, str]]:
    plist_path = os.path.expanduser("~/Library/Preferences/com.apple.HIToolbox.plist")
    if not os.path.exists(plist_path):
        return None
    try:
        with open(plist_path, "rb") as plist_file:
            plist_data = plistlib.load(plist_file)
    except Exception:  # noqa: BLE001
        return None

    sources = plist_data.get("AppleSelectedInputSources") or []
    if not sources:
        return None

    current: Optional[dict[str, object]] = None
    for entry in reversed(sources):
        if entry.get("InputSourceKind") == "Keyboard Layout":
            current = entry
            break
    if current is None:
        current = sources[-1]

    layout = current.get("KeyboardLayout Name") or current.get("Localized Name")
    layout_id = current.get("KeyboardLayout ID")
    input_source_id = current.get("InputSourceID") or plist_data.get(
        "AppleCurrentKeyboardLayoutInputSourceID"
    )
    script = current.get("KeyboardLayout Script")
    languages = current.get("Languages") or current.get("Language")
    if isinstance(languages, (list, tuple)):
        languages_str = ",".join(str(language) for language in languages)
    elif isinstance(languages, str):
        languages_str = languages
    else:
        languages_str = None

    result: dict[str, str] = {}
    if layout:
        result["layout"] = layout
    if layout_id is not None:
        result["layout_id"] = str(layout_id)
    if script:
        result["script"] = script
    if languages_str:
        result["languages"] = languages_str
    if input_source_id:
        result["input_source_id"] = input_source_id
    return result or None


def _detect_keyboard_layout_windows() -> Optional[dict[str, str]]:
    try:
        import ctypes  # noqa: PLC0415
        import locale  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None

    try:
        layout_handle = ctypes.windll.user32.GetKeyboardLayout(0)
    except Exception:  # noqa: BLE001
        return None

    if not layout_handle:
        return None

    language_id = layout_handle & 0xFFFF
    locale_name = locale.windows_locale.get(language_id)
    language_code = locale_name.split("_", 1)[0] if locale_name else None

    result: dict[str, str] = {
        "layout": language_code.upper() if language_code else f"0x{language_id:04X}",
        "hkl": f"0x{layout_handle & 0xFFFFFFFF:08X}",
    }
    if locale_name:
        result["locale"] = locale_name
    return result


def _detect_keyboard_layout_linux() -> Optional[dict[str, str]]:
    try:
        completed = subprocess.run(
            ["setxkbmap", "-query"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        locale_env = os.environ.get("LANG")
        if not locale_env:
            return None
        locale_code = locale_env.split(".", 1)[0]
        return {"layout": locale_code}

    layout_info: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        layout_info[key.strip().lower()] = value.strip()

    layout = layout_info.get("layout")
    if not layout:
        return None

    result: dict[str, str] = {"layout": layout}
    for optional_key in ("variant", "options"):
        optional_value = layout_info.get(optional_key)
        if optional_value:
            result[optional_key] = optional_value
    return result


def _detect_keyboard_layout() -> Optional[dict[str, str]]:
    platform = sys.platform
    if platform == "darwin":
        return _detect_keyboard_layout_macos()
    if platform.startswith(("win", "cygwin")):
        return _detect_keyboard_layout_windows()
    return _detect_keyboard_layout_linux()


@mcp_server.tool(name="desktop_move_mouse")
def move_mouse(
    x: Annotated[float, "X screen coordinate in pixels"],
    y: Annotated[float, "Y screen coordinate in pixels"],
    duration: Annotated[float, "Seconds to complete movement"] = 0.0,
) -> ToolResponse:
    """Move the mouse cursor to an absolute position."""

    pyautogui.moveTo(x, y, duration=duration)
    summary = f"Moved cursor to ({x:.1f}, {y:.1f}) over {duration:.2f}s."
    return _build_response(summary)


@mcp_server.tool(name="desktop_mouse_click")
def click(
    x: Annotated[Optional[float], "X coordinate; uses current cursor position if omitted"] = None,
    y: Annotated[Optional[float], "Y coordinate; uses current cursor position if omitted"] = None,
    button: Annotated[Literal["left", "right", "middle"], "Mouse button to use"] = "left",
    clicks: Annotated[int, "Number of clicks"] = 1,
    interval: Annotated[float, "Delay between clicks in seconds"] = 0.0,
) -> ToolResponse:
    """Perform a mouse click (or multiple clicks)."""

    pyautogui.click(x=x, y=y, clicks=clicks, interval=interval, button=button)
    if x is not None and y is not None:
        position_str = f" at ({x:.1f}, {y:.1f})"
    else:
        position_str = ""
    plural = "s" if clicks != 1 else ""
    summary = f"Clicked {button} button{position_str} {clicks} time{plural}."
    return _build_response(summary)


@mcp_server.tool(name="desktop_mouse_drag")
def drag(
    start_x: Annotated[Optional[float], "Start X coordinate; default is current pointer"] = None,
    start_y: Annotated[Optional[float], "Start Y coordinate; default is current pointer"] = None,
    end_x: Annotated[float, "Destination X coordinate"] = 0.0,
    end_y: Annotated[float, "Destination Y coordinate"] = 0.0,
    duration: Annotated[float, "Seconds to complete the drag"] = 0.5,
    button: Annotated[Literal["left", "right", "middle"], "Mouse button to hold"] = "left",
) -> ToolResponse:
    """Drag the mouse from the start coordinates to the end coordinates."""

    if start_x is not None and start_y is not None:
        pyautogui.moveTo(start_x, start_y)

    pyautogui.dragTo(end_x, end_y, duration=duration, button=button)
    summary = f"Dragged {button} button to ({end_x:.1f}, {end_y:.1f}) over {duration:.2f}s."
    return _build_response(summary)


@mcp_server.tool(name="desktop_type_text")
def type_text(
    text: Annotated[str, "Text to type"],
    interval: Annotated[float, "Delay between keystrokes in seconds"] = 0.0,
    press_enter: Annotated[bool, "Append an Enter keypress after typing"] = False,
) -> ToolResponse:
    """Type the given text using the keyboard."""

    _type_text_with_layout_awareness(text, interval)
    if press_enter:
        pyautogui.press("enter")
    summary = "Typed text{}.".format(" and pressed enter" if press_enter else "")
    return _build_response(summary)


@mcp_server.tool(name="desktop_press_keys")
def press_keys(
    keys: Annotated[list[str], "Sequence of keys to press"],
    as_hotkey: Annotated[
        bool, "If true, presses keys together (hotkey) instead of sequentially"
    ] = False,
    interval: Annotated[float, "Delay between key presses in seconds"] = 0.0,
) -> ToolResponse:
    """Press a set of keys sequentially or as a hotkey combination."""

    if not keys:
        raise ValueError("The keys list must not be empty.")

    normalized_keys = _normalize_keys(keys)

    if as_hotkey:
        _press_hotkey(normalized_keys, interval)
        summary = f"Pressed hotkey combination: {' + '.join(normalized_keys)}."
    else:
        text_value = _keys_to_text(normalized_keys)
        if text_value is not None:
            _type_text_with_layout_awareness(text_value, interval)
            summary = f"Typed text: {text_value!r}."
        else:
            pyautogui.press(normalized_keys, interval=interval)
            summary = f"Pressed keys sequentially: {', '.join(normalized_keys)}."

    return _build_response(summary)


@mcp_server.tool(name="desktop_get_keyboard_layout")
def get_keyboard_layout() -> ToolResponse:
    """Attempt to identify the active keyboard layout."""

    layout_info = _detect_keyboard_layout()
    if layout_info is None:
        return _build_response(
            "Unable to determine the active keyboard layout on this platform.",
            status="error",
        )

    detail_parts = [f"{key}={value}" for key, value in layout_info.items() if value]
    summary = "Keyboard layout detected: {}".format("; ".join(detail_parts))
    return _build_response(summary)


@mcp_server.tool(name="desktop_get_screen_size")
def get_screen_size() -> ToolResponse:
    """Return the width and height of the primary screen."""

    width, height = pyautogui.size()
    summary = f"Screen size: {width}x{height}"
    return _build_response(summary)


@mcp_server.tool(name="desktop_capture_screenshot")
def screenshot(
    region: Annotated[
        Optional[Sequence[int]],
        "Optional region as [left, top, width, height]. Default captures full screen.",
    ] = None,
    quality: Annotated[
        Optional[int],
        "JPEG quality (5-95); lower values drastically reduce payload size.",
    ] = None,
    color_mode: Annotated[
        Optional[Literal["color", "gray", "palette"]],
        "Color treatment before encoding; palette mode reduces colors to shrink payload size.",
    ] = None,
    palette_size: Annotated[
        Optional[int],
        "Number of colors when using palette mode (2-256). Defaults to environment setting or 32.",
    ] = None,
) -> ToolResponse:
    """Capture a screenshot and return its base64 representation along with the image dimensions."""

    normalized_region = _normalize_region(region)
    try:
        screenshot_image = pyautogui.screenshot(region=normalized_region)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Unable to capture a screenshot. Ensure Pillow and PyScreeze are installed "
            "(try `pip install pillow pyscreeze`)."
        ) from exc

    screenshot_width, screenshot_height = screenshot_image.size
    mode_to_use = (color_mode or _default_color_mode).lower()
    quality_to_use = quality if quality is not None else _default_image_quality
    palette_to_use = palette_size

    screenshot_b64 = _encode_image_to_base64(
        screenshot_image,
        quality=quality_to_use,
        color_mode=mode_to_use,
        palette_size=palette_to_use,
    )
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    summary_parts: list[str] = []
    if region is not None:
        left, top, width, height = normalized_region
        summary_parts.append(f"region=({left}, {top}, {width}, {height})")

    summary_parts.append(f"mode={mode_to_use}")
    summary_parts.append(f"quality={max(5, min(95, int(quality_to_use)))}")
    if mode_to_use == "palette":
        palette_detail = palette_size if palette_size is not None else _default_palette_size
        summary_parts.append(f"palette={palette_detail}")

    details_str = "; ".join(summary_parts)
    summary = f"Captured screenshot at {timestamp}"
    if details_str:
        summary += f"; {details_str}."
    else:
        summary += "."

    return _build_response(
        summary,
        screenshot_b64=screenshot_b64,
        dimensions=(screenshot_width, screenshot_height),
    )


def run_server() -> None:
    """Start the MCP server loop."""

    mcp_server.run()
