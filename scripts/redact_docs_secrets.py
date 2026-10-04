#!/usr/bin/env python3
"""Redact tokens and secrets from documentation."""

import re
import sys
from pathlib import Path


def redact_tokens(text: str) -> str:
    """Replace sensitive tokens with placeholders."""
    patterns = [
        (r"HomolAdmin-[a-z0-9]{20,}", "HomolAdmin-<redact>"),
        (r"admin-[a-z0-9-]{10,}", "admin-<redact>"),
        (r"SenhaSegura\d+!", "<redact>"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def process_file(file_path: Path) -> bool:
    """Process a single markdown file."""
    content = file_path.read_text()
    redacted = redact_tokens(content)

    if content != redacted:
        file_path.write_text(redacted)
        return True
    return False


def main() -> int:
    """Main entry point."""
    docs_dir = Path("docs")
    modified_count = 0

    for md_file in sorted(docs_dir.glob("*.md")):
        if process_file(md_file):
            print(f"✅ Redactado: {md_file}")
            modified_count += 1
        else:
            print(f"⏭️  Ignorado: {md_file}")

    if modified_count:
        print(f"\n✅ {modified_count} arquivo(s) redactado(s)")
        return 0

    print("\n✅ Nenhum segredo encontrado em docs/*.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
