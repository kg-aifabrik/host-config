# ADR-0008: GitHub-rendered Markdown for docs; SVG diagrams via Excalidraw

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

Documentation needs to be: readable, durable, easily authored, easily reviewed, available where readers already are. Diagrams need to be: editable by anyone, versioned, embedded inline.

The question: do we adopt a documentation site framework (mkdocs Material, sphinx, Docusaurus), or stay with plain Markdown rendered by GitHub?

## Decision

- **Plain Markdown in `docs/`**, rendered natively by GitHub.
- **No build step, no doc-site framework, no GitHub Pages deployment.**
- **Diagrams in `docs/diagrams/`**: SVG files committed alongside their Excalidraw `.excalidraw` source files. Both versioned. Markdown references SVGs with relative paths.

## Consequences

**Easier:**
- Zero infrastructure: no doc-site build pipeline, no hosting outage risk, no doc CI to maintain.
- Edit in any text editor; GitHub renders immediately.
- Diagrams: open `.excalidraw` in [excalidraw.com](https://excalidraw.com), edit, re-export SVG, commit. JSON source diffs cleanly so diagram review is meaningful.
- Discoverability is natural — GitHub's file tree is the navigation.

**Harder:**
- No auto-generated API reference. Contributors find function docs in source. Acceptable at our scope; revisit when we have many public APIs.
- No client-side search across docs. GitHub's repo search suffices for now.
- No global navigation sidebar. Each doc links to siblings explicitly.

**Risks introduced:**
- If we someday want a polished doc site (mkdocs Material is the obvious upgrade), we'd be adding a build step. Mitigation: Markdown is mkdocs-compatible by default, so the migration is additive.

**Triggers for re-evaluation:**
- When we publish a public-facing version (this repo is private).
- When the docs reach a scale where readers genuinely need full-text search and a sidebar nav.

## Alternatives Considered

- **mkdocs Material + mkdocstrings** — feature-rich, beautiful, but requires a build pipeline and GH Pages deployment. Overkill for a private internal repo.
- **sphinx** — heavyweight; reStructuredText is more friction than Markdown.

## References

- Plan §3 (Stack choices), §4 (Repo structure), §8.4 (SVG convention).
- Excalidraw: https://excalidraw.com
- Related ADRs: 0010 (GitHub Actions for CI).
