#!/usr/bin/env python3
"""Simple import test for the project."""

def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")

    # Test src package
    import src
    print("✓ src imported")

    import src.auth
    print("✓ src.auth imported")

    import src.server
    print("✓ src.server imported")

    # Test tools
    import src.tools
    print("✓ src.tools imported")

    import src.tools.chat
    print("✓ src.tools.chat imported")

    import src.tools.image
    print("✓ src.tools.image imported")

    import src.tools.media
    print("✓ src.tools.media imported")

    import src.tools.file
    print("✓ src.tools.file imported")

    import src.tools.research
    print("✓ src.tools.research imported")

    import src.tools.manage
    print("✓ src.tools.manage imported")

    print("\n✅ All imports successful!")
    print("\nNote: Full functionality requires gemini-webapi and mcp packages.")
    print("Install them with: pip install gemini-webapi mcp")


if __name__ == "__main__":
    test_imports()
