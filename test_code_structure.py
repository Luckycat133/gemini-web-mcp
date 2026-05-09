#!/usr/bin/env python3
"""
测试代码结构 - 验证所有模块导入和工具注册
"""

import sys
import os

print("=" * 60)
print("Gemini MCP Server v2.0 代码结构测试")
print("=" * 60)

all_passed = True

# 测试 1: 模块导入
print("\n[1/4] 测试模块导入...")
modules = [
    ("src.server", "服务器主入口"),
    ("src.client_wrapper", "客户端封装"),
    ("src.constants", "常量配置"),
    ("src.tools.chat", "对话工具"),
    ("src.tools.research", "研究工具"),
    ("src.tools.media", "媒体工具"),
    ("src.tools.file", "文件工具"),
    ("src.tools.manage", "管理工具"),
]

for module_name, description in modules:
    try:
        __import__(module_name)
        print(f"  ✅ {description}")
    except Exception as e:
        print(f"  ❌ {description}: {e}")
        all_passed = False

# 测试 2: 客户端封装
print("\n[2/4] 测试客户端封装...")
try:
    from src.client_wrapper import (
        get_gemini_client,
        initialize_client,
        store_session,
        get_session,
        remove_session,
        reset_client
    )
    print("  ✅ 所有函数导入成功")
except Exception as e:
    print(f"  ❌ {e}")
    all_passed = False

# 测试 3: 常量配置
print("\n[3/4] 测试常量配置...")
try:
    from src.constants import MODEL_CONFIG, RPC, ENDPOINTS
    print(f"  ✅ 模型配置: {list(MODEL_CONFIG.keys())}")
    print(f"  ✅ RPC 常量数量: {len(RPC.__members__)}")
    print(f"  ✅ API 端点数量: {len(ENDPOINTS)}")
except Exception as e:
    print(f"  ❌ {e}")
    all_passed = False

# 测试 4: 工具注册
print("\n[4/4] 测试工具注册...")
try:
    from mcp.server.fastmcp import FastMCP
    from src.tools.chat import register_chat_tools
    from src.tools.research import register_research_tools
    from src.tools.media import register_media_tools
    from src.tools.file import register_file_tools
    from src.tools.manage import register_manage_tools
    
    # 创建测试服务器
    test_server = FastMCP("Test Server")
    
    # 注册所有工具
    register_chat_tools(test_server)
    register_research_tools(test_server)
    register_media_tools(test_server)
    register_file_tools(test_server)
    register_manage_tools(test_server)
    
    print("  ✅ 所有工具注册成功")
except Exception as e:
    print(f"  ❌ {e}")
    import traceback
    traceback.print_exc()
    all_passed = False

# 总结
print("\n" + "=" * 60)
print("测试总结")
print("=" * 60)

if all_passed:
    print("\n🎉 所有代码结构测试通过！")
    print("\n项目结构验证成功，可以正常使用！")
    sys.exit(0)
else:
    print("\n⚠️ 部分测试失败，请检查错误")
    sys.exit(1)
