import asyncio, os, sys, re
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
ENV = os.environ.copy()
ENV["GEMINI_TOOLS"] = "all"
ENV["PYTHONPATH"] = str(PROJECT_DIR / "src")

env_file = PROJECT_DIR / ".env"
if env_file.exists():
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

G, R, Y, C = "\033[92m", "\033[91m", "\033[93m", "\033[96m"
OK, FAIL, WARN, INFO = f"{G}✅\033[0m", f"{R}❌\033[0m", f"{Y}⚠️\033[0m", f"{C}📌\033[0m"


async def test():
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    params = StdioServerParameters(
        command=str(PROJECT_DIR / ".venv" / "bin" / "python"),
        args=["-m", "src.server"],
        env=ENV, cwd=str(PROJECT_DIR),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            results = {"pass": 0, "fail": 0, "warn": 0}

            async def run(name, tool, args=None):
                nonlocal results
                print(f"  {INFO} {name} ...", end=" ", flush=True)
                try:
                    if tool == "LIST":
                        r = await session.list_tools()
                        print(f"{OK} {len(r.tools)} tools")
                        results["pass"] += 1
                        return None

                    r = await session.call_tool(tool, args or {})
                    t = r.content[0].text if r.content else ""
                    s = t[:150].replace("\n", " ")

                    if "Error" in t or "失败" in t:
                        if "aborted" in t.lower() or "silently" in t.lower():
                            print(f"{WARN} Google abort: {s[:100]}")
                            results["warn"] += 1
                        else:
                            print(f"{FAIL} {s}")
                            results["fail"] += 1
                    else:
                        print(f"{OK} {s}")
                        results["pass"] += 1
                    return t
                except Exception as e:
                    msg = str(e)[:120]
                    if "timeout" in msg.lower():
                        print(f"{WARN} timeout")
                        results["warn"] += 1
                    else:
                        print(f"{FAIL} {msg}")
                        results["fail"] += 1
                    return None

            # ═══ 本地功能 ═══
            print(f"\n{G}── 📋 本地功能 ──\033[0m")
            await run("列出全部工具", "LIST")
            await run("Cookie 状态", "gemini_get_cookie_status")
            await run("功能概览", "gemini_list_features")
            await run("模型列表", "gemini_list_models")

            # ═══ 提示词 CRUD ═══
            print(f"\n{G}── 📝 提示词管理 ──\033[0m")
            t = await run("创建", "gemini_manage_prompts",
                          {"action": "create", "name": "test_prompt", "content": "You are a helpful assistant.", "category": "test"})
            pid = None
            if t:
                m = re.search(r'ID[:\s]+([\w-]+)', t)
                if m: pid = m.group(1)
            await run("列表", "gemini_manage_prompts", {"action": "list"})
            await run("分类", "gemini_manage_prompts", {"action": "list_categories"})
            if pid:
                await run("查看", "gemini_manage_prompts", {"action": "get", "prompt_id": pid})
                await run("更新", "gemini_manage_prompts", {"action": "update", "prompt_id": pid, "content": "updated"})
                await run("删除", "gemini_manage_prompts", {"action": "delete", "prompt_id": pid})

            # ═══ 对话 - 三模型测试 ═══
            print(f"\n{G}── 💬 对话 (三模型) ──\033[0m")
            await run("Fast 对话", "gemini_chat",
                      {"message": "Say just 'Ok fast' in English, nothing else.", "model": "fast"})
            await run("Thinking 对话", "gemini_chat",
                      {"message": "Say just 'Ok thinking' in English, nothing else.", "model": "thinking"})
            await run("Pro 对话", "gemini_chat",
                      {"message": "Say just 'Ok pro' in English, nothing else.", "model": "pro"})

            # ═══ 多轮对话 ═══
            print(f"\n{G}── 🔄 多轮对话 ──\033[0m")
            t = await run("创建会话", "gemini_start_chat", {"model": "fast"})
            sid = None
            if t:
                m = re.search(r'[a-f0-9]{8,}', t)
                if m: sid = m.group()
            if sid:
                await run("第1轮", "gemini_send_message", {"session_id": sid, "message": "My name is Alice"})
                await run("第2轮", "gemini_send_message", {"session_id": sid, "message": "What is my name?"})
                await run("重置会话", "gemini_reset_session", {"session_id": sid})

            # ═══ 媒体生成 ═══
            print(f"\n{G}── 🎨 媒体生成 ──\033[0m")
            await run("图像生成", "gemini_generate_media",
                      {"prompt": "A cute cartoon cat, simple style", "media_type": "image", "model": "fast"})
            await run("视频生成", "gemini_generate_media",
                      {"prompt": "A cat walking in a garden, 5 seconds", "media_type": "video", "model": "pro"})
            await run("音乐生成", "gemini_generate_music",
                      {"prompt": "A happy upbeat melody", "model": "thinking"})

            # ═══ 管理功能 ═══
            print(f"\n{G}── 📜 管理功能 ──\033[0m")
            await run("历史对话", "gemini_list_chats")
            await run("Gems 列表", "gemini_manage_gems", {"action": "list"})
            await run("客户端重置", "gemini_reset")

            # ═══ 总结 ═══
            total = results["pass"] + results["fail"] + results["warn"]
            print(f"\n{G}════════════════════════\033[0m")
            print(f"  {OK} 通过: {results['pass']}  {FAIL} 失败: {results['fail']}  {WARN} 警告: {results['warn']}  (共 {total})")
            print(f"{G}════════════════════════\033[0m")


asyncio.run(test())