#!/usr/bin/env python3
"""
Cookie Manager 测试脚本
"""

import os
import sys
import logging

# 设置测试用的环境变量
os.environ["GEMINI_PSID"] = "test_psid_123"
os.environ["GEMINI_PSIDTS"] = "test_psidts_456"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_cookie_manager_import():
    """测试导入 Cookie Manager"""
    print("\n" + "="*60)
    print("测试 1: 导入 Cookie Manager")
    print("="*60)
    
    try:
        from src.cookie_manager import (
            CookieManager,
            CookieStatus,
            CookieData,
            get_cookie_manager,
            init_cookie_manager
        )
        print("✅ Cookie Manager 导入成功")
        return True
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cookie_manager_init():
    """测试 Cookie Manager 初始化"""
    print("\n" + "="*60)
    print("测试 2: Cookie Manager 初始化")
    print("="*60)
    
    try:
        from src.cookie_manager import CookieManager
        
        cm = CookieManager()
        print("✅ Cookie Manager 初始化成功")
        
        cookie = cm.get_cookie()
        print(f"  Cookie 数据: {cookie}")
        
        if cookie:
            print(f"  PSID: {cookie.psid[:20]}...")
            print(f"  PSIDTS: {cookie.psidts[:20]}...")
        
        return True
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cookie_status():
    """测试 Cookie 状态检查"""
    print("\n" + "="*60)
    print("测试 3: Cookie 状态检查")
    print("="*60)
    
    try:
        from src.cookie_manager import get_cookie_manager
        
        cm = get_cookie_manager()
        status, info = cm.get_cookie_status()
        
        print(f"✅ 状态获取成功")
        print(f"  状态: {status.value}")
        print(f"  详细信息: {info}")
        
        needs_refresh = cm.needs_refresh()
        print(f"  需要刷新: {needs_refresh}")
        
        return True
    except Exception as e:
        print(f"❌ 状态检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cookie_update():
    """测试 Cookie 更新"""
    print("\n" + "="*60)
    print("测试 4: Cookie 更新")
    print("="*60)
    
    try:
        from src.cookie_manager import get_cookie_manager
        
        cm = get_cookie_manager()
        
        new_psid = "updated_psid_789"
        new_psidts = "updated_psidts_012"
        
        success = cm.update_cookie(new_psid, new_psidts, source="test")
        print(f"✅ Cookie 更新: {success}")
        
        cookie = cm.get_cookie()
        print(f"  新 PSID: {cookie.psid}")
        print(f"  新 PSIDTS: {cookie.psidts}")
        print(f"  来源: {cookie.source}")
        
        return True
    except Exception as e:
        print(f"❌ 更新失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_client_wrapper_integration():
    """测试 Client Wrapper 集成"""
    print("\n" + "="*60)
    print("测试 5: Client Wrapper 集成")
    print("="*60)
    
    try:
        from src.client_wrapper import (
            COOKIE_MANAGER_AVAILABLE,
            get_cookie_status
        )
        
        print(f"✅ COOKIE_MANAGER_AVAILABLE: {COOKIE_MANAGER_AVAILABLE}")
        
        if COOKIE_MANAGER_AVAILABLE:
            status = get_cookie_status()
            print(f"  状态信息: {status}")
        
        return True
    except Exception as e:
        print(f"❌ 集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_env_vars():
    """测试环境变量转换"""
    print("\n" + "="*60)
    print("测试 6: 环境变量转换")
    print("="*60)
    
    try:
        from src.cookie_manager import get_cookie_manager
        
        cm = get_cookie_manager()
        env_vars = cm.to_env_vars()
        
        print(f"✅ 环境变量转换成功")
        print(f"  环境变量: {env_vars}")
        
        return True
    except Exception as e:
        print(f"❌ 环境变量转换失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("Cookie Manager 测试套件")
    print("="*60)
    
    tests = [
        ("导入测试", test_cookie_manager_import),
        ("初始化测试", test_cookie_manager_init),
        ("状态检查", test_cookie_status),
        ("Cookie 更新", test_cookie_update),
        ("Client Wrapper 集成", test_client_wrapper_integration),
        ("环境变量转换", test_env_vars),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ 测试 '{test_name}' 异常: {e}")
            results.append((test_name, False))
    
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {test_name}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️ 有 {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
