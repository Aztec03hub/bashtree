# bashtree

Bash command structure from [tree-sitter-bash](https://github.com/tree-sitter/tree-sitter-bash): every simple command a command line would run, with its resolved words, assignments, redirects and context. It **parses only**: nothing is ever executed or expanded.

It holds no policy. It answers "what is in this command line", and each consumer decides what that means:
- [hopback](https://github.com/Aztec03hub/hopback) asks "does this start a new Claude Code session?" and errs towards no.
- bash-write-guard asks "can this write somewhere unexpected?" and errs towards yes.

```python
from bashtree import parse, unwrap, nested

r = parse("cd /w && sudo make >log 2>&1 &")
r.status                      # "ok" | "syntax-error" | "too-big"
for c in r.commands:          # source order, commands inside $( ) included
    c.name, [w.text for w in c.words]
    c.op_before, c.op_after   # None ; \n & && || ;;
    c.background, c.negated
    [a.kind for a in c.ancestors]          # root first: list, pipeline, if_body, ...
    [(x.op, x.target.text) for x in c.redirects]

u = unwrap(["sudo", "-u", "x", "git", "push"])   # words ("git", "push"), stopped None, options ((("-u", "x"),),)
nested(("bash", "-lc", "make"))                  # [Nested(via="bash", kind="text", text="make")]
```

## What you get

| Field | Meaning |
|---|---|
| `Command.kind` | `simple`, `declaration` (export/local/declare/readonly/typeset), `unset`, `assignment` (a standalone `X=1`), `redirect` (a statement that is only redirects, `>f` or `[[ x ]] >f`, which still creates or truncates its target) |
| `Command.words` | `Word(text, raw, literal, span)`. `text` has quotes removed and escapes resolved, including `$'...'` (cut at a NUL, as bash does) and `$"..."`. `literal` is False when expansion could change the word (`$v`, `$( )`, globs, braces, `~`). Known gaps: `$` followed by a digit, `$`, `-`, `!` or `@` right after a quote (`>a'b'$1`) is still marked literal, `$'\x{4d}'` is not decoded as bash does, and non-UTF-8 bytes in `$'..'` become U+FFFD. |
| `Command.assignments` | prefix (`X=1 cmd`), declared or standalone, each with `has_substitution` |
| `Command.redirects` | `Redirect(op, fd, target, heredoc_quoted, span, words, body)`; `body` is the text bash passes on stdin for `<<`/`<<-` (tabs stripped for `<<-`; `None` for an unquoted heredoc holding a substitution) and the word text for `<<<`; `words` are the words written after the target (`rm >f -rf x` has `-rf`, `x`), which the shell hands to the command and `Command.words` includes; `fd` is `"2"` in `2>f`, `"0"` in `0>f` and `"{v}"` in `{v}>f` (known gap: a glued option such as `ls -1>out` reports fd `"-1"` and loses the word, and an oversized number or a non-ASCII `{name}` is taken as an fd). Redirects come in source order, those written after a heredoc included. A trailing redirect on a pipeline or a `&&`/`\|\|` list is its last command's (`a \| b >f` and `cd /w && make >log` write from b and make), as in bash. A compound's redirects (`{ ...; } >f`, `done >f`) sit on its `redirected` ancestor, and a function's (`f() {...} >f`) on its `function_body` ancestor, since they apply on every call. |
| `Command.ancestors` | `list` (with `op` and `side`), `pipeline` (with `stage` and `count`), `redirected`, `if_condition`, `elif_condition` (runs only if the conditions before it failed), `if_body`, `while_condition`, `loop_header`, `loop_body`, `case_word`, `case_pattern`, `case_body`, `function_body`, `subshell`, `brace_group`, `command_substitution`, `heredoc_substitution`, `process_substitution`, `test_command`, `arithmetic` |
| `op_before` / `op_after` | what joins it to its neighbours: `None`, `;`, newline, `&`, `&&`, `\|\|`, or `;;`, `;&`, `;;&` in a case. In `a && b \|\| c`, b gets `("&&", "\|\|")` |
| `background` / `negated` | a trailing `&` applies to it, directly or via anything enclosing it. `negated` is a leading `!` on the command itself or its pipeline; for `! ( a )` the `!` is on the `subshell` ancestor's `negated`, not on `a`. |

`unwrap()` takes strings or Word objects; a non-literal word that looks like an assignment (`env "${a}_X=1" git push`) stops with `stopped='nonliteral-assignment'` (`words` from that word on). It peels `VAR=` prefixes and wrappers: `env` (including `env -S 'cmd args'`), `sudo`, `timeout`, `nice`, `nohup`, `setsid`, `exec`, `command`, `builtin`, `stdbuf`, `time`, `ionice`, `busybox`, `coproc` and `!`. A path-qualified wrapper is only seen through from a system directory (`/usr/bin/env`, `/usr//bin/env`; not `./sudo`, `/usr/local/bin/sudo` or any path with `..`), and `stopped` is then `"untrusted-path"`. `nested()` applies the same gate to shells, tmux and the rest: a program from any other path gives `unknown`. Options are read the way getopt reads them, from a table per wrapper: `-uroot` carries its value, `--fore` is `--foreground`. An option the table doesn't list is never guessed at. `stopped` says why `words` isn't the program that runs: `"query"` (`command -v`, `sudo -l`, `--help`), `"edit"` (`sudo -e` writes the files named), `"unsized-option"` (an unlisted option, `sudo -s`/`-i` operands holding `$`, or an `env -S` value with `$`, `\` or `#`; otherwise its pieces take the option's place and env reads on from them, as env does) `"nonliteral-program"` (the program word is `$S`, a glob or brace: `words` from it on) or `"too-deep"`. `env -S` lists `env` once in `wrappers`. `options` holds each wrapper's options and values, since some write (`time -o f`).

Conditions inside `[[ ]]` (`[[ a && $(b) ]]`) carry no list information: a command there just has a `test_command` ancestor.

`nested()` returns what a command runs in turn, reading options the same way:
- `-c` text for `bash`, `sh`, `zsh`, `dash`, `ksh`, `mksh`, `ash`, `yash`, `fish` (every `-c` and `-C`), `csh`, `tcsh`, `script` (its output file is in `words`), `su` and `runuser` (`runuser -u USER cmd` runs `cmd` directly) (for bash and its kin, the first operand after all the options, as bash takes it); `eval`;
- tmux: `-c`, `new-window`, `split-window`, `new-session`, `respawn-*`, `run-shell`, `pipe-pane`, `display-popup` and `if-shell`, by name, alias or unique prefix, and each command of a `;` chain; `send-keys`, as the lines it types (keys back to back, `Enter`, `C-m` or `C-j` ending a line). `send-keys -X copy-pipe*` and `pipe*` run their argument. A tmux command known never to run a shell command (`ls`, `kill-*`, `select-*` ...) gives nothing; any other (`bind-key`, `set-hook`, `set-option`, `source-file`, `paste-buffer`, a `#( )` format, a `-f` config file, a bare `send-keys Enter` that submits earlier typing) gives `unknown`;
- `screen` argv;
- `kind="unknown"` for what can't be known statically: a script file, stdin, `screen -R`, `-X` and `-c`, keys such as `Up`, `Tab` or `C-c`, a typed `!` (history expansion), an option no table lists, `find -exec`, and `xargs` (its `words` are the command it runs, before the words it reads). `su` and `runuser` with no command are `unknown` too (their shell reads stdin), and so are these launchers, whatever their options: `doas`, `pkexec`, `chroot`, `taskset`, `chrt`, `numactl`, `unshare`, `nsenter`, `setpriv`, `strace`, `ltrace`, `fakeroot`, `flock`, `watch`, `parallel`, `xvfb-run`, `systemd-run`, `ssh`, `docker`, `kubectl`, `xterm`, and `.` / `source` (a file or process substitution; the commands inside `source <(...)` are found as ordinary commands). Launchers not in that list and `find`'s own write actions (`-delete`, `-fprint`) are not reported.

## Known grammar limits

Measured on 162,750 real commands, and against real bash: `bench/differential.py` runs random scripts whose commands are do-nothing functions and compares what bash ran, the words it passed and the files it made with what `parse()` says. It finds none for `ok` results in about 18,000 scripts per batch. Beyond what bash runs, `ok` does not cover commands that bash builds from variable CONTENT later (`$((x))`, `${!x}`, `eval`-like builtins other than the ones `nested()` lists); and a `too-big` from the deadline can differ between an idle and a loaded machine, so treat it as "unknown". None of them is a reason to fork the grammar; each one is handled or reported. Where the grammar gets a command wrong without reporting an error, bashtree reports `syntax-error` instead. The main check is general rather than a list of shapes: any text no node of the tree accounts for, in a place where bash would expand it, must hold no unescaped `$(` or backtick.

| Input | What happens | Real commands |
|---|---|---|
| a heredoc line the grammar cannot parse (`cat <<'E' 2>&1 \| tail`, two heredocs on one line, 0.46% of real commands) | read by a fallback: each body is blanked (offsets unchanged) and the head parsed; refused as `syntax-error` when a terminator is missing or an unquoted body holds `$(`, a backtick or `${` | 817 of 853 `<<` failures now parse |
| `n<>file`, extglob patterns (`@(a\|b)`), an assignment with a redirect (`x=1 >f`), `f() while ...; done` | `syntax-error` | |
| a reserved word where a command name goes: `! { a; }` and some `if` lines, misparsed silently by the grammar; `then x` | `syntax-error` | 4 |
| a substitution the grammar leaves as text: `${x%$(a)}`, `` ${x:-a `b`} ``, `` `a` `` after `$x` in an unquoted heredoc, `'$(a)'` inside `"${x:-...}"` (where single quotes don't quote) | `syntax-error` | 6 |
| a backslash inside backticks (`` `echo \`a\`` ``, `` `a \$HOME` ``), which bash removes before parsing | `syntax-error` | 3 |
| words in a heredoc redirect: an argument after the delimiter (`cat <<E file`), or a body misread when a line opens with `` \` `` | `syntax-error` | 1 |
| a line continuation inside a word (`r\`, newline, `m`), or joining `$` and `(` inside quotes, or a digit or `&` and `>`; the grammar splits them | `syntax-error` | 0 |
| a NUL anywhere, or an unquoted carriage return, vertical tab, form feed or byte-order mark, which bash keeps in the word | `syntax-error` | 0 |
| a substitution starting a heredoc line after indentation (`\t$(date)`), which the grammar leaves as text | `syntax-error`, though bash runs it | 0 |
| a heredoc line that only starts with its delimiter (`E; touch f`, `E)`, `E >f`, an indented `E`): bash does not end the heredoc there, the grammar does | `syntax-error` | 0 |
| words after a redirect when the last command isn't a plain one (`{ a; } >f g`, `a \| (b) >f g`, `export >f A=1`) | `syntax-error` | 0 |
| the next line taken for part of a command: after a pipeline, `a \| b`, newline, `c 2>&1` parses as one command `b c` (3,900 real commands, 2.4%) | put right: the newline becomes a `;`, so every offset stays, and `op_before` of that command is `;` instead of a newline. If it can't be (a comment or continuation in the gap) | `syntax-error`, 14 |
| a `$` joined to the next line, `==` or `=~` outside `[[ ]]` (dropped, and it swallows the next line), a backtick inside backticks, a continuation opening a line | `syntax-error` | 22 |
| a line continuation inside an unquoted heredoc body: bash joins it, so a terminator can move | `syntax-error` | 0 |
| `{` or `}` glued to the next word (`{qfv}<f cmd`, `{a;}b`), where the grammar sees a brace group and bash a word | `syntax-error` | 0 |
| a `>&WORD` target (bash expands it twice), or a `>&`/`<&` target starting with `-` | `syntax-error` | 0 |
| an assignment glued to a comment, `$'\\'` ended at the wrong quote, empty backticks joining the words around them, white space inside a word, text no node of the tree accounts for | `syntax-error` | 0 |
| a word the grammar splits (`$a/$b.c`, a string then `\;`, `X=$A/$b.c cmd`), a lone `-` before a heredoc or redirect (`python3 - <<E`, 13% of real commands), `$(>f)`, a `\rm` opening a line | put right: the pieces are joined and the missing word is given back | |
| `;;` outside a case, or an `esac` that is only an argument (`case x in a) b esac`) | `syntax-error` | 0 |
| a quoted heredoc (`<<E\F`) whose body the grammar parsed as code | `syntax-error` | 0 |

The grammar's parse time is quadratic on many malformed shapes (40 KB of `a?` takes 7 s, 200 KB of `== ` 28 s), too many to cap one by one, and it can't be cancelled: tree-sitter's progress callback segfaults. So a command over 8 KB, or with any non-ASCII character, is parsed in a child interpreter, given up after 3 s (`DEADLINE`) as `too-big`; the child costs about 30 to 80 ms a parse, against 0.2 ms in process. It also keeps a crash in the grammar out of the caller: py-tree-sitter 0.26.0 segfaults on `${` followed by U+F0000 and up, in 14 bytes, and that command comes back `too-big`. A hook that must survive any crash can set `BASHTREE_ALWAYS_CHILD=1` to parse every command in the child. The child is limited to 2 GB of address space (`CHILD_MEMORY`), and a child that is killed by a signal or runs out of memory also gives `too-big`. `parse()` raises `RuntimeError` only if the child itself is broken (a bad install). Repairs of a swallowed newline (up to 50) each re-parse the whole text; the slowest shape measured takes about 1 s, inside the same deadline. Cheap counts run first: more than 256 `<<`, 1,000 `=(`, 10,000 brackets (`()[]{}`), 2,000 `|` or 1 MB gives `too-big` before any parse; the real maxima are 12, 19, 4,282, 849 and 78 KB. `bench/fuzz.py` fails on any input over 5 s. Every command carries its whole ancestor chain, so nesting deeper than 256 gives `too-big` too (the real maximum is 48), as does a line of more than 10,000 commands (the real maximum is 95).

Speed over the real corpus: mean 0.8 ms per command, p99 6 ms, max 0.2 s, on a machine at load 36 (2026-10-09). Importing it costs about 27 ms with interpreter start included.

## Install and test

Python 3.10+. Dependencies are pinned: `tree-sitter==0.26.0` and `tree-sitter-bash==0.25.1`, both MIT, with prebuilt wheels.

```bash
pip install .                      # or vendor the bashtree/ package, as hopback does
python tests/test_bashtree.py      # structure tests
python tests/mutate.py             # each listed guard, removed, must fail a test
python bench/fuzz.py [corpus.jsonl]   # 20k fuzz inputs, pathological cases, optional real corpus
```

The corpus is never in this repository. It is real commands, so they get parsed, never run.

## Guard notes

- `nested()` unwraps first (`sudo bash -c x`, `env bash -c x`); pass `unwrap_first=False` for a command already unwrapped. A wrapper `unwrap()` stops at gives `unknown`.
- `git_args(words)` skips git's global options (`-C path`, `-c name=val`, `--git-dir`, `--no-pager` ...) and returns `GitArgs(options, subcommand, rest, ok)`; `ok` False means an option not listed (git itself rejects the glued `-ccore.x=1` and `-C/x`). It takes strings or Word objects. `ok` is also False, with `unknown` the offending word, for a non-literal subcommand (`git $x push`) and for a `-c`/`--config-env` key that runs a command (`alias.*=!cmd`, `core.fsmonitor`, `core.pager`, `core.editor`, `core.sshCommand`, `core.hooksPath`, `credential.helper`, `diff.external`, `filter.*.smudge`, `gpg.program`, `sequence.editor`, ...; keys are case-insensitive), in which case `runs_command` is True. `subcommand` is the name as written, before alias resolution. `nested()` knows `chronic` and `ifne` as launchers; for any program outside your own known-safe table, treat `nested() == []` as unknown rather than as nothing.
- The tree cannot see what these change: after an `alias` (or `shopt -s expand_aliases`), a function definition, an `eval` of a non-literal, `source` / `.`, or `trap`, the guard must treat the command names that follow as unknown.
