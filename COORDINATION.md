# Coordination

bashtree has two consumers, maintained separately:
- **hopback**, which decides whether a command starts a new Claude Code session. It errs towards "no".
- **bash-write-guard**, a Claude Code PreToolUse hook that decides whether a command can write somewhere unexpected. It errs towards "unsafe".

There is one shared version. Neither consumer keeps a private fork.

## Decisions
- 2026-10-08: no fork of tree-sitter-bash. We pin upstream (tree-sitter 0.26.0, tree-sitter-bash 0.25.1) and fork only if a grammar bug blocks a consumer and can't be handled here. Even then, the fix goes upstream as a pull request.
- 2026-10-08: no policy in bashtree. Each consumer decides what a command means.
- 2026-10-08: caps and grammar misparses are reported as a status (`too-big`, `syntax-error`), never as an ok result with commands missing.
- 2026-10-08: hopback vendors a pinned copy in `hopback/_vendor/bashtree`, with its source commit recorded in `VENDORED`. The guard consumes it however suits the hook.
- 2026-10-09: no license file, like hopback: the code is public to read, all rights reserved.
- 2026-10-09: an `elif` condition gets its own ancestor kind, `elif_condition`, because it runs only if the conditions before it failed. A pipeline's trailing redirect belongs to its last stage. Redirect-only statements are commands of kind `redirect`.
- 2026-10-09: where the grammar gets a command wrong without an error node (a line continuation inside a word, a substitution left as text, a backslash in backticks, words in a heredoc redirect, a reserved word as a command name, a carriage return), parse() reports `syntax-error`, so `ok` never hides a command.
- 2026-10-09: a command over 8 KB, or with non-ASCII text, is parsed in a child interpreter with a 3 s deadline (`too-big` past it): the grammar is quadratic on too many malformed shapes to cap, and can't be cancelled in-process.
- 2026-10-09: tmux commands not known to be harmless give `unknown`, never `[]`.
- 2026-10-09: helpers read options from a getopt table per program and never guess at an option the table doesn't list: unwrap() stops with `unsized-option`, nested() gives kind `unknown`.
- 2026-10-09: words written after a redirect target (`rm >f -rf x`) belong to the command: the grammar hangs them on the redirect, and `Command.words` takes them back (`Redirect.words` keeps them too). A heredoc ends only at a line that is exactly its delimiter, `;;` only closes a case item, and a program from a path outside the system directories is `unknown` in both unwrap() and nested(), so `ok` still never hides a command.
- 2026-10-09: the grammar takes the newline after a pipeline for white space when a command with a redirect follows (`a | b`, newline, `c 2>&1` is one command `b c`, in 2.4% of real commands). parse() puts it right by turning that newline into `;`, which keeps every offset; what it cannot put right is `syntax-error`. `bench/differential.py` checks the contract against real bash: every command bash runs must be one parse() found.
- 2026-10-09: the parse child has a 2 GB address-space cap; a child killed by a signal, or out of memory, gives `too-big`.

## Process
- Every change is made by one consumer's maintainer and reviewed by the other before it lands.
- Tell the other maintainer about every change that affects them.
- Before a change lands: `tests/test_bashtree.py` passes, `tests/mutate.py` kills every mutant, `bench/fuzz.py` reports no crash or hang, and `bench/differential.py 20000` finds no command bash ran that parse() lacks.

## Status
- First cut extracted from hopback. hopback runs on it, with identical verdicts on 162,750 real commands.
- Next: the guard's maintainer reviews the API, then contributes the parse-relevant subset of the guard's cases as text -> structure tests.

## Open questions
- (none yet)
