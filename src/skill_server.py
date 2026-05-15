#!/usr/bin/env python3
"""
Gemini Web MCP Server - Skill Optimized Edition (v3.0)
Optimized for low token consumption and ease of AI use.
"""

import logging
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Optional, Literal
import os
import json
import shutil

from .client_wrapper import (
    get_gemini_client, initialize_client, store_session, 
    get_session, remove_session, list_sessions, load_images,
    reset_client, get_cookie_status, get_cookie_from_browser
)
from .constants import MODEL_CONFIG

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ============================================
# Preset configurations for quick access
# ============================================
PRESETS = {
    "code": {"model": "thinking", "temperature": 0.2},
    "creative": {"model": "pro", "temperature": 0.8},
    "fast": {"model": "fast", "temperature": 0.5},
    "image": {"model": "pro", "media": "image"},
    "research": {"model": "pro", "research": True}
}

# ============================================
# Server initialization with minimal instructions
# ============================================
mcp = FastMCP(
    "Gemini Skill",
    instructions="""
# Gemini Skill (v3.0)
Gemini Web MCP Server - Optimized for AI use.

## MODELS
- fast: quick responses
- thinking: reasoning chain
- pro: best quality

## MAIN TOOLS
- ask: chat with Gemini
- media: generate images/videos/music
- edit: edit images
- session: manage multi-turn conversations
- prompts: manage prompt library
- cookie: check/refresh cookies

Use ask for most tasks.
"""
)

# ============================================
# Session management
# ============================================
_sessions = {}

# ============================================
# Tool implementations (minimal parameters)
# ============================================

@mcp.tool()
async def ask(
    message: str,
    model: Literal["fast", "thinking", "pro"] = "fast",
    image_path: Optional[str] = None,
    session_id: Optional[str] = None
) -> list[TextContent]:
    """
    Chat with Gemini.
    Args:
        message: what you want to say
        model: fast|thinking|pro (default: fast)
        image_path: optional path to image
        session_id: optional, for conversation
    """
    client = get_gemini_client()
    await initialize_client()
    config = MODEL_CONFIG[model]
    
    contents = [message]
    if image_path:
        images = load_images([image_path])
        contents.extend(images)
    
    if session_id and session_id in _sessions:
        response = await _sessions[session_id]["session"].send_message(contents)
    else:
        response = await client.generate_content(contents, model=config["name"])
    
    result = [response.text] if response.text else []
    
    if hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            if hasattr(img, "url") and img.url:
                result.append(f"\n[Image {i}]: {img.url}")
    
    if hasattr(response, "videos") and response.videos:
        for i, vid in enumerate(response.videos, 1):
            if hasattr(vid, "url") and vid.url:
                result.append(f"\n[Video {i}]: {vid.url}")
    
    if hasattr(response, "audio_url") and response.audio_url:
        result.append(f"\n[Audio]: {response.audio_url}")
    
    return [TextContent(type="text", text="".join(result))]


@mcp.tool()
async def media(
    prompt: str,
    type: Literal["image", "video", "music"] = "image",
    model: Literal["fast", "thinking", "pro"] = "fast",
    image_path: Optional[str] = None
) -> list[TextContent]:
    """
    Generate media (image/video/music).
    Args:
        prompt: what to generate
        type: image|video|music
        model: fast|thinking|pro
        image_path: optional reference image
    """
    client = get_gemini_client()
    await initialize_client()
    config = MODEL_CONFIG[model]
    
    prefix = {
        "image": "Generate image: ",
        "video": "Generate video: ",
        "music": "Create music: "
    }[type]
    
    contents = [prefix + prompt]
    if image_path:
        images = load_images([image_path])
        contents.extend(images)
    
    response = await client.generate_content(contents, model=config["name"])
    
    result = [response.text] if response.text else []
    
    if type == "image" and hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            if hasattr(img, "url"):
                result.append(f"\n[Image {i}]: {img.url}")
    
    if type == "video" and hasattr(response, "videos") and response.videos:
        for i, vid in enumerate(response.videos, 1):
            if hasattr(vid, "url"):
                result.append(f"\n[Video {i}]: {vid.url}")
    
    if type == "music" and hasattr(response, "audio_url") and response.audio_url:
        result.append(f"\n[Audio]: {response.audio_url}")
    
    return [TextContent(type="text", text="".join(result))]


@mcp.tool()
async def edit(
    image_path: str,
    prompt: str,
    model: Literal["fast", "thinking", "pro"] = "fast"
) -> list[TextContent]:
    """
    Edit an existing image.
    Args:
        image_path: path to image file
        prompt: what changes to make
        model: fast|thinking|pro
    """
    client = get_gemini_client()
    await initialize_client()
    config = MODEL_CONFIG[model]
    
    contents = [f"Edit this image: {prompt}"]
    images = load_images([image_path])
    contents.extend(images)
    
    response = await client.generate_content(contents, model=config["name"])
    
    result = [response.text] if response.text else []
    
    if hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            if hasattr(img, "url"):
                result.append(f"\n[Edited Image {i}]: {img.url}")
    
    return [TextContent(type="text", text="".join(result))]


@mcp.tool()
async def session(
    action: Literal["create", "send", "list", "reset"],
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    model: Literal["fast", "thinking", "pro"] = "fast",
    image_path: Optional[str] = None
) -> list[TextContent]:
    """
    Manage multi-turn conversation sessions.
    Args:
        action: create|send|list|reset
        session_id: required for send/reset
        message: message to send (send action only)
        model: fast|thinking|pro (create only)
        image_path: optional image
    """
    client = get_gemini_client()
    await initialize_client()
    
    if action == "create":
        config = MODEL_CONFIG[model]
        sess = client.start_chat(model=config["name"])
        sid = f"sess_{len(_sessions) + 1}"
        _sessions[sid] = {"session": sess, "model": model}
        return [TextContent(type="text", text=f"Created session: {sid}")]
    
    elif action == "send":
        if not session_id or session_id not in _sessions:
            return [TextContent(type="text", text=f"Invalid session: {session_id}")]
        
        contents = [message] if message else []
        if image_path:
            images = load_images([image_path])
            contents.extend(images)
        
        response = await _sessions[session_id]["session"].send_message(contents)
        
        result = [response.text] if response.text else []
        
        if hasattr(response, "images") and response.images:
            for i, img in enumerate(response.images, 1):
                if hasattr(img, "url"):
                    result.append(f"\n[Image {i}]: {img.url}")
        
        return [TextContent(type="text", text="".join(result))]
    
    elif action == "list":
        if not _sessions:
            return [TextContent(type="text", text="No active sessions")]
        
        items = []
        for i, (sid, data) in enumerate(_sessions.items(), 1):
            items.append(f"{i}. {sid} ({data['model']})")
        
        return [TextContent(type="text", text="\n".join(items))]
    
    elif action == "reset":
        if session_id:
            if session_id in _sessions:
                del _sessions[session_id]
                return [TextContent(type="text", text=f"Reset session: {session_id}")]
        else:
            _sessions.clear()
            reset_client()
            return [TextContent(type="text", text="Reset all sessions")]
    
    return [TextContent(type="text", text="Invalid action")]


@mcp.tool()
async def prompts(
    action: Literal["list", "get", "create", "delete"],
    name: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None
) -> list[TextContent]:
    """
    Manage prompt library.
    Args:
        action: list|get|create|delete
        name: prompt name (required for create/delete)
        content: prompt text (create only)
        category: optional category
    """
    from .tools.prompts import get_prompt_manager
    mgr = get_prompt_manager()
    
    if action == "list":
        items = mgr.list_prompts(category=category)
        if not items:
            return [TextContent(type="text", text="No prompts found")]
        
        result = []
        for i, p in enumerate(items, 1):
            result.append(f"{i}. {p['name']} ({p['category']})")
        
        return [TextContent(type="text", text="\n".join(result))]
    
    elif action == "get":
        if not name:
            return [TextContent(type="text", text="Name required")]
        
        for p in mgr.list_prompts():
            if p["name"] == name:
                return [TextContent(type="text", text=f"{p['name']}\n---\n{p['content']}")]
        
        return [TextContent(type="text", text="Prompt not found")]
    
    elif action == "create":
        if not name or not content:
            return [TextContent(type="text", text="Name and content required")]
        
        pid = mgr.create_prompt(
            name=name, 
            content=content, 
            category=category or "general"
        )
        return [TextContent(type="text", text=f"Created prompt: {name} (id: {pid[:8]})")]
    
    elif action == "delete":
        if not name:
            return [TextContent(type="text", text="Name required")]
        
        for p in mgr.list_prompts():
            if p["name"] == name:
                mgr.delete_prompt(p["id"])
                return [TextContent(type="text", text=f"Deleted: {name}")]
        
        return [TextContent(type="text", text="Prompt not found")]
    
    return [TextContent(type="text", text="Invalid action")]


@mcp.tool()
async def cookie(
    action: Literal["status", "get"],
    browser: Literal["chrome", "firefox", "edge"] = "chrome"
) -> list[TextContent]:
    """
    Check or refresh cookies.
    Args:
        action: status|get
        browser: chrome|firefox|edge (get only)
    """
    if action == "status":
        status = get_cookie_status()
        return [TextContent(type="text", text=f"Cookie: {'OK' if status.get('has_cookie') else 'Missing'}")]
    
    elif action == "get":
        success = get_cookie_from_browser(browser)
        return [TextContent(type="text", text=f"Cookie: {'Loaded' if success else 'Failed'}")]
    
    return [TextContent(type="text", text="Invalid action")]


def initialize_prompts():
    """Initialize with default prompts if none exist"""
    prompts_file = "prompts.json"
    if not os.path.exists(prompts_file):
        default_file = "prompts_default.json"
        if os.path.exists(default_file):
            shutil.copy(default_file, prompts_file)
            logger.info("Initialized with default prompts")


def main():
    """Run the optimized skill server"""
    initialize_prompts()
    mcp.run()


if __name__ == "__main__":
    main()
