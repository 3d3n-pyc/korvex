# For AI assistants

Korvex is young: AI assistants know little about it, and may know it
wrong. Give them its documentation:

## The documentation, for LLMs

The site also publishes Markdown, rebuilt with every change:

- [`llms.txt`](https://3d3n-pyc.github.io/korvex/llms.txt): an index of
  every page, with a line on each.
- [`llms-full.txt`](https://3d3n-pyc.github.io/korvex/llms-full.txt): the
  whole guide and reference in one file, to paste into a chat or give to
  a tool that reads URLs.
- Every page also exists as Markdown: add `index.md` to its URL, like
  <https://3d3n-pyc.github.io/korvex/routes/index.md>.

## A skill for coding agents

The repository has an [agent skill](https://github.com/3d3n-pyc/korvex/tree/main/.agents/skills/korvex):
a short `SKILL.md` that tells an agent how to write a Korvex app, with a
complete example and the rules that differ from FastAPI. Korvex's test
suite runs its examples.

It comes with Korvex. In your project's folder, run once:

```sh
korvex agents
```

It writes the skill, in the installed Korvex's version, to
`.agents/skills/korvex/` (Codex, Cursor and most agents) and
`.claude/skills/korvex/` (Claude Code). Rerun it after upgrading Korvex;
commit both folders.

Without Python at hand, the [skills CLI](https://github.com/vercel-labs/skills)
installs the latest one from GitHub: `npx skills add 3d3n-pyc/korvex`.

An agent that opens `korvex/__init__.py`, or runs `help(korvex)`, also
finds a note pointing it to `llms-full.txt` and `korvex agents`.

## Working on Korvex itself

[`AGENTS.md`](https://github.com/3d3n-pyc/korvex/blob/main/AGENTS.md), at
the root of the repository, gives coding agents the checks to run and the
rules to follow. Most agents read it on their own.
