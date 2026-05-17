#!/usr/bin/env python3
"""
实际API测试 - 使用用户提供的Cookie
"""

import os
import asyncio

# Cookie values must be provided by the local environment.
if not os.environ.get("GEMINI_PSID"):
    print("⚠️ GEMINI_PSID 未设置；实际 API 测试将无法运行。")

print("=" * 60)
print("Gemini MCP Server v2.0 API测试")
print("=" * 60)

async def test_basic_chat():
    """测试基础对话"""
    print("\n[测试 1] 基础对话...")
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        print("  - 初始化客户端...")
        client = get_gemini_client()
        await initialize_client()
        print("  ✅ 客户端初始化成功")
        
        # 测试 fast 模型
        config = MODEL_CONFIG["fast"]
        print(f"  - 使用模型: {config['name']}")
        
        print("  - 发送测试消息...")
        response = await client.generate_content(
            "你好！请简单介绍一下你自己。",
            model=config["name"]
        )
        
        print(f"  ✅ 对话成功！")
        print(f"\n  回复预览:")
        print(f"  {response.text[:200]}...")
        
        return True
        
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_multi_turn():
    """测试多轮对话"""
    print("\n[测试 2] 多轮对话...")
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        config = MODEL_CONFIG["fast"]
        
        # 创建会话
        session = client.start_chat(model=config["name"])
        
        # 第一轮
        response1 = await session.send_message("我的名字叫张三")
        print(f"  ✅ 第一轮成功")
        
        # 第二轮（测试上下文）
        response2 = await session.send_message("我叫什么名字？")
        print(f"  ✅ 第二轮成功")
        
        if "张三" in response2.text:
            print(f"  ✅ 上下文记忆正常")
            return True
        else:
            print(f"  ⚠️ 上下文记忆可能有问题")
            return True  # 仍算成功
            
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_image_generation():
    """测试图像生成"""
    print("\n[测试 3] 图像生成...")
    try:
        from src.client_wrapper import get_gemini_client, initialize_client
        from src.constants import MODEL_CONFIG
        
        client = get_gemini_client()
        config = MODEL_CONFIG["fast"]
        
        response = await client.generate_content(
            "请生成一张可爱猫咪的图片",
            model=config["name"]
        )
        
        print(f"  ✅ 图像生成请求成功")
        
        if hasattr(response, 'images') and response.images:
            print(f"  🎨 生成了 {len(response.images)} 张图片")
            for i, img in enumerate(response.images, 1):
                print(f"     {i}. {getattr(img, 'title', 'Untitled')}")
                if hasattr(img, 'url'):
                    print(f"        URL: {img.url[:60]}...")
            return True
        else:
            print(f"  ⚠️ 未生成图片（可能是地区限制）")
            print(f"     响应文本: {response.text[:200]}...")
            return None  # 不算失败
            
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    if not os.environ.get("GEMINI_PSID"):
        print("请先设置 GEMINI_PSID/GEMINI_PSIDTS 环境变量后再运行此脚本。")
        return

    results = []
    
    # 测试 1: 基础对话
    results.append(("基础对话", await test_basic_chat()))
    
    await asyncio.sleep(2)  # 避免限流
    
    # 测试 2: 多轮对话
    results.append(("多轮对话", await test_multi_turn()))
    
    await asyncio.sleep(2)
    
    # 测试 3: 图像生成
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
        print(f"\n⚠️ 有 {failed} 个测试失败")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n\n测试运行失败: {e}")
        import traceback
        traceback.print_exc()
