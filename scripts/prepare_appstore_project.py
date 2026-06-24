#!/usr/bin/env python3
"""Create an App Store XcodeGen spec without Sparkle.

Sparkle is only used in non-App Store builds. Removing the embedded framework
after archive still leaves a dyld load command in the app binary, so the App
Store project must be generated without the package dependency.
"""

from pathlib import Path
import sys


SPARKLE_PACKAGE_BLOCK = """packages:
  Sparkle:
    url: https://github.com/sparkle-project/Sparkle
    from: "2.6.0"
"""

SPARKLE_DEPENDENCY_BLOCK = """      - package: Sparkle
        product: Sparkle
"""


def remove_required_block(content: str, block: str, label: str) -> str:
    if block not in content:
        raise SystemExit(f"Could not find {label} block in project.yml")
    return content.replace(block, "", 1)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/project-appstore.yml")
    if not output.is_absolute():
        output = repo_root / output

    content = (repo_root / "project.yml").read_text()
    content = remove_required_block(content, SPARKLE_PACKAGE_BLOCK, "Sparkle package")
    content = remove_required_block(content, SPARKLE_DEPENDENCY_BLOCK, "Sparkle dependency")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
