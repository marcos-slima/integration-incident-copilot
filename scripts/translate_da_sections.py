#!/usr/bin/env python3
"""
Translate DECISOES_DE_ARQUITETURA.md sections from Portuguese to English.
This script processes the content section (lines 93-2749) and creates the full English translation.
"""

from pathlib import Path

# Read the source file
source_file = Path(
    "/home/marcos-lima/MyProjects/GitHub/integration-incident-copilot/docs/DECISOES_DE_ARQUITETURA.md"
)
source_content = source_file.read_text(encoding="utf-8")
source_lines = source_content.split("\n")

# Read the existing English translation (header only)
translation_file = Path(
    "/home/marcos-lima/MyProjects/GitHub/integration-incident-copilot/docs/en/ARCHITECTURE_DECISIONS.md"
)
translation_content = translation_file.read_text(encoding="utf-8")
translation_lines = translation_content.split("\n")

# Extract the header (first 91 lines - up to and including the "---" separator)
header = "\n".join(translation_lines[:91])

# Extract the content to translate (lines 93-2749, which is lines 92-2748 in 0-indexed)
# Line 92 is "---", line 93 starts with "## Decisões"
content_start = 92  # 0-indexed: line 93
content_end = 2749  # 0-indexed: line 2749
content_to_translate = "\n".join(source_lines[content_start:content_end])

print(f"Header lines: {len(translation_lines[:91])}")
print(f"Content lines to translate: {len(source_lines[content_start:content_end])}")
print(f"Total source lines: {len(source_lines)}")
print(f"Total translation lines so far: {len(translation_lines)}")

# For now, let's just output the structure
print("\n=== Content to translate (first 500 chars) ===")
print(content_to_translate[:500])

# Save the content to translate to a file for manual translation
content_file = Path(
    "/home/marcos-lima/MyProjects/GitHub/integration-incident-copilot/scripts/content_to_translate.txt"
)
content_file.write_text(content_to_translate, encoding="utf-8")
print(f"\nContent saved to {content_file}")
