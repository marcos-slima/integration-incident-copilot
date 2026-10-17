#!/usr/bin/env python3
"""
Full translation of DECISOES_DE_ARQUITETURA.md from Portuguese to English.
This script creates the complete English translation file.
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
content_start = 92  # 0-indexed: line 93 (the "---" separator)
content_end = 2749  # 0-indexed: line 2749
content_lines = source_lines[content_start:content_end]

print(f"Header lines: {len(translation_lines[:91])}")
print(f"Content lines to translate: {len(content_lines)}")
print(f"Total source lines: {len(source_lines)}")

# For now, let's just output the first section to understand the pattern
print("\n=== First section (lines 93-106) ===")
for i, line in enumerate(content_lines[:14], start=93):
    print(f"{i}: {line}")

# Save the first section to a file for manual translation
first_section = "\n".join(content_lines[:14])
first_section_file = Path(
    "/home/marcos-lima/MyProjects/GitHub/integration-incident-copilot/scripts/first_section.txt"
)
first_section_file.write_text(first_section, encoding="utf-8")
print(f"\nFirst section saved to {first_section_file}")
