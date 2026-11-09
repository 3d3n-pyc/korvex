# For AI assistants

Korvex is young, so AI assistants know little about it, and what they
know may be out of date. Give them its documentation:

## The documentation, for LLMs

The site publishes itself as Markdown, rebuilt with every change:

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
complete example and the rules that differ from FastAPI. Its examples run
in Korvex's test suite, so it stays true.

Copy the `korvex` folder into your project's `.agents/skills/` (or
`.claude/skills/` for Claude Code), or install it with the
[skills CLI](https://github.com/vercel-labs/skills):

```sh
npx skills add 3d3n-pyc/korvex
```

## Working on Korvex itself

[`AGENTS.md`](https://github.com/3d3n-pyc/korvex/blob/main/AGENTS.md), at
the root of the repository, gives coding agents the checks to run and the
rules to follow. Most agents read it on their own.
