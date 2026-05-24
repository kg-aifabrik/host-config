# Diagrams

Source of truth for every diagram referenced by docs and ADRs in this repo.

## Convention

- **Tool:** [Excalidraw](https://excalidraw.com). Open-source, hand-drawn aesthetic, JSON-based source files that diff cleanly in git.
- **Source files** (`.excalidraw`) are **committed alongside the exported SVG**. Both are versioned.
- **Naming:** kebab-case, descriptive. Example: `systems-overview.excalidraw` + `systems-overview.svg`.
- **Embedding in Markdown:** relative path. Example: `![Systems overview](../diagrams/systems-overview.svg)`. GitHub renders this inline.

## Workflow for adding a diagram

1. Open [excalidraw.com](https://excalidraw.com) (or the Excalidraw desktop app).
2. Draw. Use the [shared palette](#shared-color-palette) for consistency across diagrams.
3. File → Save as → `docs/diagrams/<name>.excalidraw` (this writes the source).
4. Export → SVG (Excalidraw menu) → `docs/diagrams/<name>.svg`.
5. Commit both files. Reference the SVG from your Markdown.

## Workflow for editing a diagram

1. Open the `.excalidraw` source file (drag-drop into excalidraw.com or open in desktop app).
2. Edit.
3. Re-export the SVG to the same path.
4. Commit both files.

## Shared color palette

For visual consistency across diagrams, use these colors:

| Concept | Color (hex) | Notes |
|---|---|---|
| Management VLAN | `#a5d8ff` (light blue) | bond0.100 |
| Storage VLAN | `#ffec99` (light yellow) | bond0.200 |
| Ingress VLAN | `#b2f2bb` (light green) | bond0.300 |
| RoCE / east-west | `#ffc9c9` (light red) | gpu0..gpu7 |
| GPU device | `#d0bfff` (light purple) | |
| Service boxes (host-config, nginx, Netbox) | `#dee2e6` (light gray) | |
| Kernel boundary | dashed gray line | |

These mirror the palette established in the companion research repo's diagrams so the visual language stays consistent across both.

## Why Excalidraw

Per ADR-0008: GitHub-rendered Markdown is the documentation system; no build step. Excalidraw produces SVG that embeds inline, and the JSON source diffs cleanly so review of a diagram change is meaningful. No proprietary format, no online-only dependency for viewing (exported SVG is self-contained).

## Seed diagrams (no Excalidraw source yet)

The initial set of diagrams landed in M0-4 (`systems-overview.svg`, `render-flow.svg`) was hand-authored in SVG for compactness — no `.excalidraw` source files exist for them yet. The first time someone needs to edit one, import the SVG into [excalidraw.com](https://excalidraw.com), make the edit, save the `.excalidraw` source alongside the SVG, re-export, commit both. From that point forward the diagram follows the standard workflow above.

This is a one-time exception for seed diagrams. New diagrams must be authored in Excalidraw from the start.
