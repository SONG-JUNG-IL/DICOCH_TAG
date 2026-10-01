# Third-Party Notices

The rules, agents, and skills under `.claude/` were copied verbatim from
**Everything Claude Code (ECC)** and are distributed under the MIT License below.

- Source: https://github.com/affaan-m/everything-claude-code
- Version: 2.2.2 (commit c70874f, 2026-09-29)
- Project-specific overrides are documented in `/CLAUDE.md` (section 6), not by editing these files.

## Included files

- `.claude/agents/python-reviewer.md`
- `.claude/agents/security-reviewer.md`
- `.claude/rules/common/code-review.md`
- `.claude/rules/common/coding-style.md`
- `.claude/rules/common/development-workflow.md`
- `.claude/rules/common/git-workflow.md`
- `.claude/rules/common/patterns.md`
- `.claude/rules/common/performance.md`
- `.claude/rules/common/security.md`
- `.claude/rules/common/testing.md`
- `.claude/rules/python/coding-style.md`
- `.claude/rules/python/patterns.md`
- `.claude/rules/python/security.md`
- `.claude/rules/python/testing.md`
- `.claude/skills/python-testing/SKILL.md`
- `.claude/skills/tdd-workflow/SKILL.md`
- `.claude/skills/verification-loop/SKILL.md`

Intentionally excluded: `rules/common/agents.md` (references plugin-scoped `ecc:` agents),
`rules/*/hooks.md` (hooks are not installed), `rules/python/fastapi.md` (not applicable).

## License

```
MIT License

Copyright (c) 2026 Affaan Mustafa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
