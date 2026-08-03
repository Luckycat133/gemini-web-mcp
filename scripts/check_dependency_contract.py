"""Fail when runtime imports are not backed by declared dependency intent."""

if __package__:
    from .dependency_contract import PROJECT_ROOT, require_dependency_contract
else:
    from dependency_contract import (  # type: ignore[import-not-found,no-redef]
        PROJECT_ROOT,
        require_dependency_contract,
    )


def main() -> None:
    require_dependency_contract(PROJECT_ROOT)
    print("Dependency contract OK: every runtime third-party import is direct or extra-gated")


if __name__ == "__main__":
    main()
