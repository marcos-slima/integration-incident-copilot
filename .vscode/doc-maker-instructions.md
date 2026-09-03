# Documentation Maker — VS Code Agent Instructions

This project uses **Documentation Maker** for automated, commit-tracked documentation.

## Documentation paths

| What                  | Path                                          |
|-----------------------|-----------------------------------------------|
| Project structure     | `code-docs/structure/file-structure.md`     |
| Dependency graph      | `code-docs/structure/dependencies.md`       |
| Per-file docs         | `code-docs/files/<path>.md`                 |

## Agent guidance

- Before explaining a file: read its doc from `code-docs/files/`.
- After a significant change: suggest updating the corresponding file doc.
- For new files: follow the format in existing `code-docs/files/` docs.
- Each file doc has a **Change History** table at the bottom — append only, never delete rows.

## Commands

- `Ctrl+Alt+D I` / `Cmd+Alt+D I` — Full initial documentation scan
- `Ctrl+Alt+D U` / `Cmd+Alt+D U` — Update docs for files changed since last commit
- Command Palette → **Documentation Maker: Scaffold Skill Files** — Recreate these files
