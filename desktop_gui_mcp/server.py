"""MCP server exposing desktop automation helpers."""

from __future__ import annotations

import base64
import os
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal, Optional, Sequence, TypedDict

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
    _default_image_quality = 10

mcp_server = FastMCP(SERVER_NAME)


class ToolResponse(TypedDict):
    status: str
    summary: str
    screenshot: Optional[str]
    screenshot_dimensions: Optional[tuple[int, int]]


def _encode_image_to_base64(
    image,
    quality: Optional[int] = None,
) -> str:
    """Encode a PIL image to a base64 string."""
    image_to_save = image
    save_kwargs = {}
    quality_to_use = quality if quality is not None else _default_image_quality
    # Encourage compact payloads by defaulting to modest quality levels (10-20 is usually plenty).
    save_kwargs["quality"] = max(10, min(95, quality_to_use))
    save_kwargs["optimize"] = True
    save_kwargs["progressive"] = True
    if image.mode not in {"RGB", "L"}:
        image_to_save = image.convert("RGB")

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

    pyautogui.write(text, interval=interval)
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

    if as_hotkey:
        pyautogui.hotkey(*keys, interval=interval)
        summary = f"Pressed hotkey combination: {' + '.join(keys)}."
    else:
        pyautogui.press(keys, interval=interval)
        summary = f"Pressed keys sequentially: {', '.join(keys)}."

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
        "JPEG quality (10-95); values around 20 generally provide clear results with small payloads.",
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

    width, height = screenshot_image.size
    quality = 20
    screenshot_b64 = _encode_image_to_base64(screenshot_image, quality=quality)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    if region is not None:
        left, top, width, height = normalized_region
        summary_detail = f" region=({left}, {top}, {width}, {height})"
    else:
        summary_detail = ""
    summary = f"Captured screenshot at {timestamp}{summary_detail}."
    return _build_response(
        summary,
        screenshot_b64=screenshot_b64,
        dimensions=(width, height),
    )


def run_server() -> None:
    """Start the MCP server loop."""

    mcp_server.run()
