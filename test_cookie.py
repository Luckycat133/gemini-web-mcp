#!/usr/bin/env python3
"""
Gemini MCP Server 功能测试脚本
使用用户提供的Cookie进行测试
"""

import os
import sys
import asyncio
import logging

# Cookie values must be provided by the local environment.
if not os.environ.get("GEMINI_PSID"):
    print("⚠️ GEMINI_PSID 未设置；实际 API 测试将无法运行。")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_basic_chat():
    """测试基础对话功能"""
    print("\n" + "=" * 60)
    print("测试 1: 基础对话")
    print("=" * 60)
    
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        await initialize_client()
        
        # 使用 fast 模型进行测试
        config = MODEL_CONFIG["fast"]
        print(f"使用模型: {config['name']}")
        
        response = await client.generate_content(
            "你好，请简单介绍一下你自己。",
            model=config["name"]
        )
        
        print("\n✅ 响应成功！")
        print(f"\n回复内容:\n{response.text[:300]}...")
        
        if hasattr(response, "images") and response.images:
            print(f"\n📷 生成了 {len(response.images)} 张图片")
        
        return True
        
    except Exception as e:
        logger.error(f"基础对话测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_thinking_model():
    """测试 thinking 模型"""
    print("\n" + "=" * 60)
    print("测试 2: Thinking 模型")
    print("=" * 60)
    
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        await initialize_client()
        
        config = MODEL_CONFIG["thinking"]
        print(f"使用模型: {config['name']}")
        
        response = await client.generate_content(
            "请用3步计算 234 + 567 = ?，每一步都详细说明。",
            model=config["name"]
        )
        
        print("\n✅ 响应成功！")
        print(f"\n回复内容:\n{response.text[:300]}...")
        
        return True
        
    except Exception as e:
        logger.error(f"Thinking 模型测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_session_chat():
    """测试多轮对话会话"""
    print("\n" + "=" * 60)
    print("测试 3: 多轮对话会话")
    print("=" * 60)
    
    try:
        from src.client_wrapper import get_gemini_client, initialize_client, store_session, get_session
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        await initialize_client()
        
        config = MODEL_CONFIG["fast"]
        
        # 创建会话
        session = client.start_chat(model=config["name"])
        session_id = "test-session-001"
        store_session(session_id, session, "fast")
        
        print(f"会话 ID: {session_id}")
        
        # 第一轮对话
        print("\n第一轮: 我的名字叫张三")
        response1 = await session.send_message("我的名字叫张三")
        print(f"✅ 回复: {response1.text[:150]}...")
        
        # 第二轮对话 (测试上下文记忆)
        print("\n第二轮: 我叫什么名字？")
        response2 = await session.send_message("我叫什么名字？")
        print(f"✅ 回复: {response2.text}")
        
        if "张三" in response2.text:
            print("\n🎉 会话上下文记忆正常！")
            return True
        else:
            print("\n⚠️ 会话上下文可能有问题")
            return False
        
    except Exception as e:
        logger.error(f"会话测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_image_generation():
    """测试图像生成"""
    print("\n" + "=" * 60)
    print("测试 4: 图像生成")
    print("=" * 60)
    
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        await initialize_client()
        
        config = MODEL_CONFIG["fast"]
        
        response = await client.generate_content(
            "请生成一张可爱猫咪的卡通图片",
            model=config["name"]
        )
        
        print("\n✅ 请求发送成功！")
        
        if hasattr(response, "images") and response.images:
            print(f"\n🎨 成功生成 {len(response.images)} 张图片！")
            
            for i, img in enumerate(response.images, 1):
                print(f"\n图片 {i}:")
                if hasattr(img, "title") and img.title:
                    print(f"  标题: {img.title}")
                if hasattr(img, "url") and img.url:
                    print(f"  URL: {img.url[:60]}...")
            
            return True
        else:
            print("\n⚠️ 未生成图像（可能是地区限制）")
            print(f"回复内容: {response.text[:200]}...")
            return None
            
    except Exception as e:
        logger.error(f"图像生成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("Gemini MCP Server 功能测试")
    print("=" * 60)

    if not os.environ.get("GEMINI_PSID"):
        print("请先设置 GEMINI_PSID/GEMINI_PSIDTS 环境变量后再运行此脚本。")
        return
    
    results = []
    
    # 测试 1: 基础对话
    results.append(("基础对话", await test_basic_chat()))
    
    # 短暂延迟避免限流
    await asyncio.sleep(2)
    
    # 测试 2: Thinking 模型
    results.append(("Thinking 模型", await test_thinking_model()))
    
    # 短暂延迟避免限流
    await asyncio.sleep(2)
    
    # 测试 3: 多轮会话
    results.append(("多轮会话", await test_session_chat()))
    
    # 短暂延迟避免限流
    await asyncio.sleep(2)
    
    # 测试 4: 图像生成
    results.append(("图像生成", await test_image_generation()))
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test_name, result in results:
        if result is True:
            print(f"✅ {test_name}: 通过")
            passed += 1
        elif result is False:
            print(f"❌ {test_name}: 失败")
            failed += 1
        else:
            print(f"⚠️ {test_name}: 跳过/不可用")
            skipped += 1
    
    print(f"\n总计: {passed} 通过, {failed} 失败, {skipped} 跳过")
    
    if failed == 0:
        print("\n🎉 所有测试通过！Gemini MCP Server 工作正常！")
    else:
        print(f"\n⚠️ 有 {failed} 个测试失败，请检查。")


if __name__ == "__main__":
    try:
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n\n测试运行失败: {e}")
        import traceback
        traceback.print_exc()
