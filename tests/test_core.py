#!/usr/bin/env python3
"""Test core modules without external dependencies."""

def test_client_wrapper():
    """Test client wrapper module imports."""
    print("Testing client_wrapper...")
    from src import client_wrapper
    print("✓ client_wrapper imported")
    
    # Test that functions exist
    assert hasattr(client_wrapper, 'validate_config')
    assert hasattr(client_wrapper, 'get_gemini_client')
    assert hasattr(client_wrapper, 'initialize_client')
    assert hasattr(client_wrapper, 'store_session')
    assert hasattr(client_wrapper, 'get_session')
    assert hasattr(client_wrapper, 'remove_session')
    assert hasattr(client_wrapper, 'clear_sessions')
    assert hasattr(client_wrapper, 'cleanup_expired_sessions')
    assert hasattr(client_wrapper, 'reset_client')
    assert hasattr(client_wrapper, 'list_sessions')
    print("✓ All expected functions exist")


def test_constants():
    """Test constants module."""
    print("Testing constants...")
    from src import constants
    print("✓ constants imported")
    
    # Test that MODEL_CONFIG exists
    assert hasattr(constants, 'MODEL_CONFIG')
    assert 'fast' in constants.MODEL_CONFIG
    assert 'thinking' in constants.MODEL_CONFIG
    assert 'pro' in constants.MODEL_CONFIG
    print("✓ MODEL_CONFIG is valid")
    
    # Test that RPC class exists
    assert hasattr(constants, 'RPC')
    print("✓ RPC class exists")


def test_tools_structure():
    """Test that all tools modules exist and can be imported."""
    print("Testing tools modules...")
    
    # Test each tool module
    import src.tools.chat
    print("✓ src.tools.chat imported")
    
    import src.tools.media
    print("✓ src.tools.media imported")
    
    import src.tools.file
    print("✓ src.tools.file imported")
    
    import src.tools.research
    print("✓ src.tools.research imported")
    
    import src.tools.manage  # noqa: F401  (import for side effect test)
    print("✓ src.tools.manage imported")


if __name__ == "__main__":
    print("Running core tests...\n")
    test_client_wrapper()
    test_constants()
    test_tools_structure()
    print("\n✅ All core tests passed!")
