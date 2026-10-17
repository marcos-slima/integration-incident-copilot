#!/usr/bin/env python3
"""
Script to translate DECISOES_DE_ARQUITETURA.md from Portuguese to English.
This is a manual translation task - the script helps with the structure.
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

# The header (lines 1-89) is already translated
# We need to translate lines 90-2749 (content section)

# Extract the header (first 89 lines)
header = "\n".join(translation_lines[:89])

# Extract the content to translate (lines 90-2749, which is lines 89-2748 in 0-indexed)
content_to_translate = "\n".join(source_lines[89:2749])

print(f"Header lines: {len(translation_lines[:89])}")
print(f"Content lines to translate: {len(source_lines[89:2749])}")
print(f"Total source lines: {len(source_lines)}")
print(f"Total translation lines so far: {len(translation_lines)}")

# The content section starts with "## Decisões" and ends with "---"
# We need to translate this section from Portuguese to English

# For now, let's just output what we have
print("\n=== Content to translate (first 100 lines) ===")
print(content_to_translate[:1000])
