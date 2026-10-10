"""bashtree's own tests: text in, structure out. Policy tables (is this a
claude launch, is this write safe) live with each consumer.

    python tests/test_bashtree.py        (or: python -m pytest tests)
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bashtree import OK, SYNTAX_ERROR, TOO_BIG, git_args, nested, parse, unwrap  # noqa: E402
from bashtree.core import DEADLINE  # noqa: E402


def cmds(text):
    r = parse(text)
    assert r.status == OK, (text, r.status, r.reason)
    return r.commands


def names(text):
    """The words of every command, in source order."""
    return [[w.text for w in c.words] for c in cmds(text)]


def kinds(c):
    return [a.kind for a in c.ancestors]


# -- what runs, and where ---------------------------------------------------------
def test_words_and_quoting():
    assert names("echo 'a b' \"c d\" e\\ f") == [["echo", "a b", "c d", "e f"]]
    assert names('cd "a\nb"') == [["cd", "a\nb"]]                     # newline kept in "..."
    assert names('echo "a\\"b\\$c\\d"') == [["echo", 'a"b$c\\d']]      # only $ ` " \ escape in "..."
    assert names("echo $'a\\nb' $'\\x41' $'\\u00e9' $'\\xc3\\xa9'") == [["echo", "a\nb", "A", "é", "é"]]
    assert names('"/usr/bin/env" x') == [["/usr/bin/env", "x"]]
    assert names("cmd \\\n  arg") == [["cmd", "arg"]]                  # line continuation
    # The grammar splits a word at a line continuation, where bash joins it.
    for text in ("r\\\nm -rf x", "echo >f\\\nile", "FOO=a\\\nb cmd", "'r'\\\nm x"):
        assert parse(text).reason == "a line continuation inside a word", text
    assert names("a \\\n&& b") == [["a"], ["b"]] and names('echo "a\\\nb"') == [["echo", "ab"]]
    assert parse("rm x\r\nrm y").status == SYNTAX_ERROR              # bash keeps the CR in the word
    assert names("echo $'a\\0b' x") == [["echo", "a", "x"]]          # bash ends a word at a NUL
    # $"..." is a "..." string
    assert names('$"rm" -rf x') == [["rm", "-rf", "x"]] and names('echo $"abc" a$"b"') == [["echo", "abc", "ab"]]
    (c,) = cmds('cat > $"/etc/x"')
    assert (c.redirects[0].target.text, c.redirects[0].target.literal) == ("/etc/x", True)
    assert cmds('echo $"a$x"')[0].words[1].literal is False


def test_literal_flag():
    (c,) = cmds('echo plain "q*" \'$x\' $v ${v} ~/a *.c x? [ab] {a,b} "$v" a\\*b')
    lit = {w.raw: w.literal for w in c.words[1:]}
    assert [(w.text, w.literal) for w in cmds("rm {1..3} a{1..3} a=~/x a~b")[0].words[1:]] == \
        [("{1..3}", False), ("a{1..3}", False), ("a=~/x", False), ("a~b", True)]
    assert lit == {"plain": True, '"q*"': True, "'$x'": True, "$v": False, "${v}": False,
                   "~/a": False, "*.c": False, "x?": False, "[ab]": False, "{a,b}": False,
                   '"$v"': False, "a\\*b": True}, lit


def test_substitutions_are_commands():
    got = {tuple(w.text for w in c.words): kinds(c) for c in cmds("echo $(a) `b` <(c) >(d) $(( $(e) + 1 ))")}
    assert got == {("echo", "$(a)", "`b`", "<(c)", ">(d)", "$(( $(e) + 1 ))"): [],
                   ("a",): ["command_substitution"], ("b",): ["command_substitution"],
                   ("c",): ["process_substitution"], ("d",): ["process_substitution"],
                   ("e",): ["arithmetic", "command_substitution"]}, got


def test_heredocs():
    (cat, g) = cmds("cat <<E\n$(g)\nE")
    assert cat.redirects[0].op == "<<" and cat.redirects[0].heredoc_quoted is False
    assert kinds(g) == ["heredoc_substitution"]
    (cat,) = cmds("cat <<'E'\n$(g) is text\nE")                      # quoted: body is data
    assert cat.redirects[0].heredoc_quoted is True and cat.redirects[0].target.text == "E"
    assert names("cat <<-E\n\tx\n\tE\nnext") == [["cat"], ["next"]]
    assert names('git commit -m "$(cat <<\'EOF\'\nclaude rm -rf\nEOF\n)"')[1] == ["cat"]


def test_function_bodies_and_case():
    (a,) = cmds("f() { a; }")
    assert kinds(a) == ["function_body"]
    assert kinds(cmds("f() ( a )")[0]) == ["function_body"]
    assert kinds(cmds("function f { a; }")[0]) == ["function_body"]
    p, a = cmds("case $x in $(p)) a;; esac")
    assert kinds(p) == ["case_pattern", "command_substitution"] and kinds(a) == ["case_body"]
    assert names("case $x in claude) echo;; esac") == [["echo"]]      # a pattern isn't a command
    (w,) = cmds("case $(w) in a) ;; esac")
    assert kinds(w) == ["case_word", "command_substitution"]


def test_if_loops_tests():
    got = [(c.name, kinds(c)) for c in cmds("if a; then b; elif c; then d; else e; fi")]
    # An elif condition runs only if the ones before it failed: not "if_condition".
    assert got == [("a", ["if_condition"]), ("b", ["if_body"]), ("c", ["elif_condition"]),
                   ("d", ["if_body"]), ("e", ["if_body"])], got
    got = [(c.name, kinds(c)) for c in cmds("while a; do b; done; until c; do d; done")]
    assert got == [("a", ["while_condition"]), ("b", ["loop_body"]),
                   ("c", ["while_condition"]), ("d", ["loop_body"])], got
    assert kinds(cmds("for ((;;)) { a; }")[0]) == ["loop_body", "brace_group"]
    got = [(c.name, kinds(c)) for c in cmds("for i in $(s); do b; done")]
    assert got == [("s", ["loop_header", "command_substitution"]), ("b", ["loop_body"])], got
    (t,) = cmds("[[ -f $(t) ]]")
    assert kinds(t) == ["test_command", "command_substitution"]
    assert names("[[ -x claude ]]") == []
    for text in ("then rm x", "a; fi", "do x", "done"):
        assert parse(text).status == SYNTAX_ERROR, text                # a reserved word as a name


def test_comments():
    # R7/R8 repros from bash-write-guard: comment and quote masking.
    assert names("echo $(a) # cd /x") == [["echo", "$(a)"], ["a"]]
    assert names("echo \"a ) b\" $(c ')' d)") == [["echo", "a ) b", "$(c ')' d)"], ["c", ")", "d"]]
    assert names("echo ${x:- #} y") == [["echo", "${x:- #}", "y"]]
    assert names("echo $((1<<2)) z") == [["echo", "$((1<<2))", "z"]]
    # Substitutions the grammar leaves as text, and backslashes in backticks.
    for text in ("echo ${x%$(rm y)}", 'echo "${x#$(rm y)}"', "echo ${x:-`rm y`}", "echo ${x%<(rm y)}",
                 "x=${y%$(rm z)}", "cat <<E\n`rm y`\nE", "cat <<E\nfoo `rm y` bar\nE",
                 "echo `echo \\`rm y\\``", "echo `rm \\$HOME/x`"):
        assert parse(text).status == SYNTAX_ERROR, text
    assert parse("cat <<E\n\\`a\\` x <(y)\nE").reason == "words in a heredoc redirect"   # body misread
    assert parse("cat <<E file\nx\nE").reason == "words in a heredoc redirect"   # an argument lost
    for text in ("cat <<'E'\n`rm y`\nE", "cat <<E\nx \\`a\\` <(y) \\$(b)\nE", "echo ${x%a} ${x:-a}", "echo `date`"):
        assert parse(text).status == OK, text
    assert names("echo ${x:-$(rm y)}")[1] == ["rm", "y"]
    assert names("# it's a comment\ncd /w") == [["cd", "/w"]]
    assert names("echo a#b") == [["echo", "a#b"]]                     # mid-word # isn't a comment
    assert names("echo $ a") == [["echo", "$", "a"]]                  # a lone $ is a word
    assert names("echo '#' x") == [["echo", "#", "x"]]



def test_text_the_grammar_left_unparsed():
    # Round 4: substitutions in text the grammar didn't parse, wherever it is.
    for text in ("cat <<E\n$x `rm y`\nE", "cat <<E\n${x} `rm y`\nE", "cat <<E\nline1\n$x\n`rm y`\nE",
                 'git commit -m "$(cat <<EOF\nfix $x `rm y`\nEOF\n)"',
                 "echo ${x:-a `rm y` b}", 'echo "${x:-a `rm y` b}"', "echo ${x/a/b `rm y`}",
                 "x=${y:-a `rm y` b}", "cat > ${y:-a `rm y` b}",
                 "echo \"${x:-'$(rm y)'}\"", "echo \"${x:+'`rm y`'}\"", "x=\"${y:-'$(rm y)'}\"",
                 "cat <<E\n${y:-'$(rm y)'}\nE"):
        assert parse(text).reason == "a substitution the grammar left as text", text
    for text in ("cat <<'E'\n$x `rm y`\nE", "echo ${x:-'$(rm y)'}", "echo '${x:-$(rm y)}'", "echo ${x:-a b}",
                 'echo "a <(b)"', "cat <<E\nx <(y)\nE", "echo \"${x:-$(echo '$(a)')}\""):   # $( ) quotes anew
        assert parse(text).status == OK, text
    # A line continuation joins `$` and `(`, a digit and `>`, `&` and `>`.
    for text in ('echo "$\\\n(rm x)"', 'echo "$\\\n{x}"', 'x="$\\\n(rm x)"', "echo 2\\\n>f", "echo a&\\\n>f"):
        assert parse(text).reason == "a line continuation inside a word", text
    assert names("echo a\\\\\nrm x") == [["echo", "a\\"], ["rm", "x"]]   # `\\` then a newline: two lines
    assert names('echo "a\\\nb"') == [["echo", "ab"]]
    for text in ("rm\x00 x", "echo a\x0b; rm x", "\ufeffrm x", "rm x\x0c"):
        assert parse(text).status == SYNTAX_ERROR, repr(text)
    for digit in ("\u00b2", "\u0663"):                                # not ASCII: a word, not an fd
        (c,) = cmds(f"echo hi {digit}>f")
        assert [w.text for w in c.words] == ["echo", "hi", digit] and c.redirects[0].fd is None
    assert names("echo $'a\\c@b' $'\\cA'") == [["echo", "a", "\x01"]]


# -- list position, pipelines, & and ! ----------------------------------------
def test_lists():
    a, b, c = cmds("a && b || c &")
    assert (a.op_before, a.op_after, b.op_before, b.op_after, c.op_before, c.op_after) == \
        (None, "&&", "&&", "||", "||", "&")
    assert a.background and b.background and c.background
    assert [(x.kind, x.op, x.side) for x in b.ancestors] == [("list", "||", 0), ("list", "&&", 1)]
    x, y = cmds("x; y")
    assert (x.op_after, y.op_before) == (";", ";")
    x, y = cmds("x # note\ny")
    assert (x.op_after, y.op_before) == ("\n", "\n")
    x, y = cmds("x & y")
    assert x.background and not y.background and x.op_after == "&"
    a, b = cmds("[[ -d x ]] && cd x; b")
    assert a.name == "cd" and a.op_before == "&&" and a.op_after == ";"


def test_pipelines_and_negation():
    x, y, z = cmds("! x | y; z")
    assert x.negated and y.negated and not z.negated
    assert [(a.stage, a.count) for a in y.ancestors] == [(1, 2)]
    a, b = cmds("! a && b")
    assert a.negated and not b.negated
    (a,) = cmds("! ( a )")
    assert not a.negated and a.ancestors[0].negated                    # the group is negated
    assert parse("! { a; }").status == SYNTAX_ERROR                    # misparsed by the grammar
    (a,) = cmds("(a) &")
    assert a.background and a.ancestors[0].background


def test_assignments_and_declarations():
    (c, d) = cmds("X=1 Y=$(d) cmd")
    assert [(a.name, a.value, a.has_substitution) for a in c.assignments] == \
        [("X", "1", False), ("Y", "$(d)", True)] and c.name == "cmd"
    (s,) = cmds("X=1 Z=$(q)")[:1]
    assert s.kind == "assignment" and s.name is None and [a.name for a in s.assignments] == ["X", "Z"]
    # bash only takes an identifier as a name; `1=2 cmd` runs a command named `1=2`.
    (c,) = cmds("1=2 claude x")
    assert (c.kind, c.name, [w.text for w in c.words], c.assignments) == \
        ("simple", "1=2", ["1=2", "claude", "x"], ()), c
    got = cmds("1x=2")
    assert [(c.kind, c.name, c.assignments) for c in got] == [("simple", "1x=2", ())], got
    (c,) = cmds("a[1]=2 x")
    assert c.name == "x" and c.assignments[0].name == "a[1]", c
    got = cmds("A+=b")
    assert len(got) == 1 and got[0].kind == "assignment" and got[0].assignments[0].append, got
    e, l_ = cmds("export A=1 B; local c")
    assert (e.kind, e.name, [w.text for w in e.words], [a.name for a in e.assignments]) == \
        ("declaration", "export", ["export", "B"], ["A"])
    assert (l_.name, [w.text for w in l_.words]) == ("local", ["local", "c"])
    (u,) = cmds("unset A")
    assert (u.kind, u.name) == ("unset", "unset")


def test_redirects():
    (c,) = cmds('cmd >> "$o" <in 2>&1 &>all >|clob')
    got = [(r.op, r.fd, r.target.text, r.target.literal) for r in c.redirects]
    assert got == [(">>", None, "$o", False), ("<", None, "in", True), (">&", "2", "1", True),
                   ("&>", None, "all", True), (">|", None, "clob", True)], got
    (c,) = cmds(">out cmd")
    assert [r.target.text for r in c.redirects] == ["out"] and c.name == "cmd"
    (c,) = cmds("f >&-")
    assert c.redirects[0].op == ">&-" and c.redirects[0].target is None
    (c,) = cmds('cmd <<<"s"')
    assert c.redirects[0].op == "<<<" and c.redirects[0].target.text == "s"
    # A compound's redirects sit on its ancestor, not on each command inside.
    a, b = cmds("{ cd a; b; } > f")
    assert not a.redirects and a.ancestors[0].kind == "redirected"
    assert [r.target.text for r in a.ancestors[0].redirects] == ["f"]
    # Redirect-only statements still create or truncate their target.
    for text in (">f", "[[ -f a ]] > f", "(( 1 )) > f"):
        got = [(c.kind, [r.target.text for r in c.redirects]) for c in cmds(text)]
        assert got == [("redirect", ["f"])], (text, got)
    for text in ("cmd 0>f", "cmd 0<<<x", "cmd 12>f"):
        (c,) = cmds(text)
        assert [w.text for w in c.words] == ["cmd"] and c.redirects[0].fd in ("0", "12"), text
    got = [(c.kind, c.name) for c in cmds("a; >f; b")]
    assert got == [("simple", "a"), ("redirect", None), ("simple", "b")], got
    # A pipeline's trailing redirect belongs to its last stage, as in bash.
    a, b = cmds("a | b > f")
    assert not a.redirects and [r.target.text for r in b.redirects] == ["f"]
    assert not any(x.kind == "redirected" for x in a.ancestors + b.ancestors)
    a, b = cmds("a >f | b >g")
    assert [r.target.text for r in a.redirects] == ["f"] and [r.target.text for r in b.redirects] == ["g"]
    (a,) = cmds("! a > f")
    assert [r.target.text for r in a.redirects] == ["f"] and a.negated
    # A function's redirects apply whenever it is called: on its body.
    (e,) = cmds("f() { echo; } > out")
    assert [r.target.text for r in e.ancestors[0].redirects] == ["out"] and not e.redirects
    (c,) = cmds("echo hi {v}>f")
    assert [w.text for w in c.words] == ["echo", "hi"] and c.redirects[0].fd == "{v}"
    # Redirects after a heredoc: the grammar nests them inside the heredoc's node.
    (c,) = cmds("cat <<E >f 2>g\nx\nE")
    assert [(r.op, r.target.text) for r in c.redirects] == [("<<", "E"), (">", "f"), (">", "g")]
    (a,) = cmds("{ a; } <<E >f\nx\nE")
    assert [r.target.text for r in a.ancestors[0].redirects] == ["E", "f"]
    # A list's trailing redirect is its last command's: `cd /w && make >log`.
    cd, make = cmds("cd /w && make >log")
    assert not cd.redirects and [r.target.text for r in make.redirects] == ["log"]
    assert not any(x.kind == "redirected" for x in cd.ancestors + make.ancestors)
    a, b, c = cmds("a && b | c >f")
    assert not a.redirects and not b.redirects and [r.target.text for r in c.redirects] == ["f"]
    a, b = cmds("a && { b; } >f")
    assert not any(x.kind == "redirected" for x in a.ancestors) and b.ancestors[1].kind == "redirected"
    # In source order, leading or trailing.
    (c,) = cmds(">f cmd 2>&1")
    assert [r.op for r in c.redirects] == [">", ">&"]
    (b,) = cmds("while x; do b; done > f")[1:]
    assert [x.kind for x in b.ancestors] == ["redirected", "loop_body"]


# -- status --------------------------------------------------------------------
def test_status():
    assert parse("echo 'unterminated").status == SYNTAX_ERROR
    assert parse("cat <<a; cat <<b\na\nb").status == OK              # was a grammar gap, read by the heredoc fallback
    assert parse("cat <<a; cat <<b\na\nb").commands[1].redirects[0].body == ""
    assert parse("x 3<>h").status == SYNTAX_ERROR                      # another
    assert parse("x <<a " * 300).status == TOO_BIG
    assert parse("a=(" * 1001).status == TOO_BIG
    assert parse("").status == OK and parse("").commands == ()
    assert len(cmds(" && ".join(["a"] * 250))) == 250              # deep, but within MAX_DEPTH
    assert parse(" && ".join(["a"] * 5000)).status == TOO_BIG      # no recursion error either
    assert parse("a;" * 10001).status == TOO_BIG                      # MAX_COMMANDS
    assert parse("{ " + "a;" * 6000 + " }\n" + "{ " + "a;" * 6000 + " }").status == TOO_BIG  # spread out
    assert len(cmds("env " * 16000 + "x")[0].words) == 16001 and parse("x " + "$a" * 12000).status == OK
    assert parse("))" * 5001).status == TOO_BIG and parse("$[" * 50000).status == TOO_BIG   # MAX_BRACKETS
    # The grammar always runs in a child, given up at DEADLINE.
    r = parse("a?" * 40000)
    assert (r.status, r.reason) == (TOO_BIG, f"parsing took over {DEADLINE} s"), r
    assert parse("echo " + "a" * 20000).commands[0].words[1].text == "a" * 20000        # the child's result
    # The child imports this same package, also when the parent found it through '' (its cwd).
    code = "from bashtree import parse; print(parse('echo ' + 'a' * 20000).status)"
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                         capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "ok", out.stderr[-300:]
    assert parse("a|" * 2001 + "a").status == TOO_BIG                # MAX_PIPES, before the grammar
    assert parse("x " + "é" * (1 << 19)).status == TOO_BIG           # MAX_BYTES counts bytes
    assert len(cmds("#c\n" * 2000 + "a")) == 1 and len(cmds("a|" * 2000 + "a")) == 2001
    for slow in ("$(" * 1500 + "x" + ")" * 1500, " && ".join(["a"] * 5000),
                 "if a; then " * 500 + "b" + "; fi" * 500, '"$(' * 3000 + "x",
                 "#c\n" * 20000 + "a", "a|" * 20000 + "a", "a;" * 100000, "a;" * 500000, "(" * 60000 + "x", "`" * 60000 + "x", "env " * 16000 + "x",
                 "x <<a " * 256 + "\n" + "a\n" * 256, "a=(" * 1000 + "x",
                 "))" * 5000, "(a)" * 3333, "$[" * 5000, "((a" * 3333):
        t = time.monotonic()
        parse(slow)
        assert time.monotonic() - t < 2 * DEADLINE, slow[:30]                # generous: CI boxes are loaded


# -- helpers ------------------------------------------------------------------
def test_unwrap():
    u = unwrap(["sudo", "-u", "x", "git", "push"])
    assert (u.words, u.wrappers, u.stopped) == (("git", "push"), ("sudo",), None)
    assert unwrap(["timeout", "-s", "KILL", "5", "make"]).words == ("make",)
    assert unwrap(["env", "-i", "A=1", "git", "x"]).words == ("git", "x")
    assert unwrap(["env", "-u", "HOME", "git"]).words == ("git",)       # -u takes a value
    assert unwrap(["sudo", "-uroot", "git", "push"]).words == ("git", "push")   # attached value
    assert unwrap(["sudo", "-nu", "root", "git"]).words == ("git",)
    assert unwrap(["env", "-S", "git push", "x"]).words == ("git", "push", "x")
    assert unwrap(["nice", "-n", "5", "nohup", "git"]).wrappers == ("nice", "nohup")
    assert unwrap(["X=1", "git"]).words == ("git",)
    assert unwrap(["command", "-v", "git"]).stopped == "query"
    assert unwrap(["sudo", "-l", "git"]).stopped == "query"
    assert unwrap(["env", "--weird", "git"]).stopped == "unsized-option"
    assert unwrap(["env"] * 20 + ["git"]).stopped == "too-deep"
    # getopt bundles: a value-taking letter ends the bundle; its value is never read as flags.
    u = unwrap(["sudo", "-ujenkins", "rm", "x"])
    assert (u.words, u.wrappers, u.stopped) == (("rm", "x"), ("sudo",), None)
    assert unwrap(["sudo", "-p", "-l", "rm", "x"]).words == ("rm", "x") and unwrap(["sudo", "-p", "-l", "rm"]).stopped is None
    assert unwrap(["sudo", "-k", "rm", "x"]).stopped is None            # -k with a command runs it
    assert unwrap(["sudo", "-e", "/etc/hosts"]).stopped == "edit"
    assert unwrap(["sudo", "-h"]).stopped == "query" and unwrap(["sudo", "-hbox", "rm"]).stopped is None
    assert unwrap(["sudo", "-R", "/chroot", "rm", "x"]).words == ("rm", "x")
    assert unwrap(["sudo", "-Q", "rm"]).stopped == "unsized-option"    # an unlisted letter isn't guessed
    assert unwrap(["sudo", "A=1", "rm"]).words == ("rm",)
    # env: -S however it is spelled, quotes, a lone -, any NAME=VALUE
    for opts in (["-Srm -rf x"], ["-iS", "rm -rf x"], ["--split=rm -rf x"], ["-S", "-i rm -rf x"]):
        assert unwrap(["env", *opts]).words == ("rm", "-rf", "x"), opts
    assert unwrap(["env", "-S", 'sh -c "rm -rf x"']).words == ("sh", "-c", "rm -rf x")
    assert unwrap(["env", "-S", "rm $HOME"]).stopped == "unsized-option"
    assert unwrap(["env", "-", "rm", "x"]).words == ("rm", "x") and unwrap(["env", "a-b=1", "rm"]).words == ("rm",)
    assert unwrap(["env", "-a", "foo", "rm"]).words == ("rm",)
    assert unwrap(["timeout", "--preserve-status", "--foreground", "5", "rm"]).words == ("rm",)
    assert unwrap(["timeout", "--fore", "5", "rm"]).words == ("rm",)   # a unique --prefix
    assert unwrap(["./sudo", "rm"]).words == ("./sudo", "rm") and unwrap(["/usr/bin/sudo", "rm"]).words == ("rm",)
    assert unwrap(["nice", "A=1", "rm"]).words == ("A=1", "rm")         # only env and sudo take NAME=VALUE
    assert unwrap(["nice", "-10", "rm"]).words == ("rm",)
    assert unwrap(["time", "!", "rm", "x"]).words == ("rm", "x") and unwrap(["coproc", "rm"]).words == ("rm",)
    assert unwrap(["builtin", "eval", "x"]).words == ("eval", "x") and unwrap(["ionice", "-p", "1"]).stopped == "query"
    assert unwrap(["time", "-o", "/etc/x", "rm"]).options == ((("-o", "/etc/x"),),)
    # Round 4: options after env -S belong to the program; env splits on \v and \f too.
    assert unwrap(["env", "-S", "git", "-C", "/x", "commit"]).words == ("git", "-C", "/x", "commit")
    assert unwrap(["env", "-i", "-S", "cp", "-u", "a", "b"]).words == ("cp", "-u", "a", "b")
    assert unwrap(["env", "-S", "rm\vx"]).words == ("rm", "x") and unwrap(["env", "-S", "rm\fx"]).words == ("rm", "x")
    assert unwrap(["env", "-S", ""]).words == () and unwrap(["env", "-S", "", "rm"]).words == ("rm",)
    assert unwrap(["sudo", "-H", "pip", "install"]).words == ("pip", "install")
    assert unwrap(["sudo", "--no-update", "rm"]).words == ("rm",)
    assert unwrap(["/usr/local/bin/sudo", "rm"]).wrappers == ()
    # Round 5: nested() puts the same path gate on shells, tmux and the rest.
    assert nested(["/bin/bash", "-c", "x"]) == [_n("bash", "text", text="x")]
    assert nested(["./bash", "-c", "x"]) == [_n("bash", "unknown", words=("-c", "x"))]
    assert nested(["/tmp/tmux", "new", "x"]) == [_n("tmux", "unknown", words=("new", "x"))]
    assert nested(["/usr/local/bin/find", ".", "-exec", "rm", "{}", ";"]) == [_n("find", "unknown", words=(".", "-exec", "rm", "{}", ";"))]
    assert nested(["./foo", "-c", "x"]) == []


def test_nested():
    assert nested(("bash", "-lc", "cd /w && make")) == [_n("bash", "text", text="cd /w && make")]
    assert nested(("bash", "script.sh", "-c", "x"))[0].kind == "unknown"
    assert nested(("script", "-qc", "make x", "/dev/null")) == [_n("script", "text", text="make x", words=("/dev/null",))]
    assert nested(("sh",))[0].kind == "unknown"                       # reads stdin
    assert nested(("bash", "-o", "pipefail", "-c", "make")) == [_n("bash", "text", text="make")]
    assert nested(("bash", "+O", "extglob", "-c", "make")) == [_n("bash", "text", text="make")]
    assert nested(("bash", "--rcfile", "f", "-c", "make")) == [_n("bash", "text", text="make")]
    assert nested(("ksh", "-c", "make")) == [_n("ksh", "text", text="make")]
    assert nested(("su", "root", "-c", "make")) == [_n("su", "text", text="make")]
    assert [n.kind for n in nested(("su", "root"))] == ["unknown"]
    assert nested(("eval", "cd", "/x"))[0].text == "cd /x"
    assert nested(("tmux", "new-window", "-d", "-n", "w", "make x"))[0].text == "make x"
    assert nested(("tmux", "split-window", "make", "x"))[0].words == ("make", "x")
    assert nested(("tmux", "send-keys", "-t", "w", "make x", "Enter"))[0].text == "make x"
    assert nested(("tmux", "send-keys", "-t", "w", "make x")) == []    # no Enter: nothing runs
    assert nested(("screen", "-dmS", "s", "make")) == [_n("screen", "words", words=("make",))]
    assert nested(("screen", "-r", "s")) == []
    assert nested(("xargs", "rm"))[0].kind == "unknown"
    assert [n.words for n in nested(("find", ".", "-exec", "rm", "{}", ";"))] == [("rm", "{}")]
    assert nested(("git", "push")) == []
    # a shell's -c text is its first operand after ALL its options
    assert nested(("bash", "-c", "-e", "rm x"))[0].text == "rm x"
    assert nested(("bash", "-c", "--", "rm x"))[0].text == "rm x"
    assert nested(("bash", "-co", "pipefail", "rm x"))[0].text == "rm x"
    assert nested(("bash", "-eo", "pipefail", "-c", "rm x"))[0].text == "rm x"
    assert nested(("bash", "-C", "-c", "rm x"))[0].text == "rm x"      # noclobber, no value
    assert nested(("fish", "-c", "rm x"))[0].text == "rm x" and nested(("tcsh", "-c", "rm x"))[0].text == "rm x"
    assert nested(("su", "-lc", "rm x"))[0].text == "rm x" and nested(("su", "-crm x"))[0].text == "rm x"
    assert nested(("su", "--command=rm x"))[0].text == "rm x" and nested(("runuser", "-u", "a", "-c", "rm"))[0].text == "rm"
    assert nested(("eval", "--", "rm x"))[0].text == "rm x"
    # screen: getopt bundles, -X, -R
    assert nested(("screen", "-dmSrun", "rm", "x"))[0].words == ("rm", "x")
    assert nested(("screen", "-s", "/bin/sh", "rm", "x"))[0].words == ("rm", "x")
    assert nested(("screen", "-S", "s", "-X", "screen", "rm"))[0].kind == "unknown"
    assert nested(("screen", "-R", "x", "rm", "y")) == [_n("screen", "unknown", words=("x", "rm", "y"))]
    assert nested(("tmux", "split-window", "-p", "30", "rm", "x"))[0].words == ("rm", "x")
    # tmux: prefixes, run-shell and kin, global -c and -T, ; chains
    assert nested(("tmux", "new-w", "rm x")) == [_n("tmux", "text", text="rm x")]
    for sub in ("run-shell", "run", "pipe-pane", "popup"):
        assert nested(("tmux", sub, "rm x"))[0].text == "rm x", sub
    assert [n.kind for n in nested(("tmux", "if-shell", "true", "kill-server"))] == ["text", "unknown"]
    assert nested(("tmux", "-c", "rm x")) == [_n("tmux", "text", text="rm x")]
    assert nested(("tmux", "-T", "256", "new-window", "rm x"))[0].text == "rm x"
    assert [n.text for n in nested(("tmux", "new-window", "-d", "a", ";", "split-window", "rm x"))] == ["a", "rm x"]
    assert [n.text for n in nested(("tmux", "new-window", "a;", "neww", "b"))] == ["a", "b"]
    assert nested(("tmux", "new-session", "-f", "x", "rm x"))[0].text == "rm x"
    assert nested(("tmux", "n", "x"))[0].kind == "unknown" and nested(("tmux", "display", "x")) == []
    # send-keys: keys typed back to back; each line ending runs a line
    assert nested(("tmux", "send-keys", "cd /", "Enter", "rm -rf x", "Enter"))[0].text == "cd /\nrm -rf x"
    assert nested(("tmux", "send-keys", "r", "m x", "Enter"))[0].text == "rm x"
    assert nested(("tmux", "send-keys", "rm", "Space", "-rf", "C-m"))[0].text == "rm -rf"
    assert nested(("tmux", "send-keys", "rm x", "C-j"))[0].text == "rm x"
    assert nested(("tmux", "send-keys", "rm x\n"))[0].text == "rm x"
    assert nested(("tmux", "send-keys", "Up", "Enter"))[0].kind == "unknown"
    assert nested(("tmux", "send-keys", "-l", "rm x", "Enter")) == []
    # xargs: its options sized, its command named
    assert nested(("xargs", "-I{}", "-n", "1", "rm", "{}")) == [_n("xargs", "unknown", words=("rm", "{}"))]
    assert nested(("fish", "-c", "")) == [_n("fish", "text", text="")] and nested(("su", "-c", ""))[0].text == ""
    assert nested(("bash", "+c", "f"))[0].kind == "unknown"             # +c is no -c
    for cmd in (("bind-key", "x", "run-shell", "rm y"), ("set-hook", "-g", "after-new-window", "run rm y"),
                ("detach-client", "-E", "rm y"), ("set-option", "-g", "default-command", "rm y"),
                ("source-file", "f"), ("lock-server",), ("display-message", "#(rm y)"), ("ls", "-F", "#(rm y)")):
        assert [n.kind for n in nested(("tmux", *cmd))] == ["unknown"], cmd
    for cmd in (("ls",), ("kill-session", "-t", "x"), ("select-window", "-t", "1"), ("attach",)):
        assert nested(("tmux", *cmd)) == [], cmd
    assert nested(("tmux", "send-keys", "echo x > /tm", "Tab", "f", "Enter"))[0].kind == "unknown"
    assert nested(("tmux", "send-keys", "!!", "Enter"))[0].kind == "unknown"
    assert nested(("xargs",))[0].words == ("echo",) and nested(("xargs", "-Z", "rm"))[0].words == ()


def _n(via, kind, text="", words=()):
    from bashtree import Nested
    return Nested(via, kind, text, words)


def _words(text):
    r = parse(text)
    assert r.status == OK, (text, r.status, r.reason)
    return [[w.text for w in c.words] for c in r.commands]


def test_round5():
    # R5-01: words after a redirect's target are the command's arguments.
    assert _words("rm >/dev/null -rf /x") == [["rm", "-rf", "/x"]]
    assert _words("sudo >f rm x") == [["sudo", "rm", "x"]]
    assert _words("cat 2>f g h") == [["cat", "g", "h"]]
    assert _words("echo >&2 x") == [["echo", "x"]]
    assert _words("echo hi >f 2") == [["echo", "hi", "2"]]
    assert _words("cat >f <g h") == [["cat", "h"]]
    assert _words(">f rm a b") == [["rm", "a", "b"]]
    # the grammar splits `$S/a-$w.txt` in two; they touch, so they are one target
    for text in ("echo hi > $S/a-$w.txt", "a | b > $S/a-$w.txt"):
        assert [x.target.raw for c in parse(text).commands for x in c.redirects] == ["$S/a-$w.txt"], text
    assert _words("echo hi > $S/a-$w.txt") == [["echo", "hi"]] and _words("echo >f a$b.c d") == [["echo", "a$b.c", "d"]]
    assert _words("cd /x && grep -n y 2>/dev/null a/*.py 2>/dev/null") == [["cd", "/x"], ["grep", "-n", "y", "a/*.py"]]
    assert _words("a | b >f g") == [["a"], ["b", "g"]]
    # the grammar took the next line's words into a redirect (reduced from a real command)
    assert _words("2>&1|''|-f1|-u>/tmp/w505-files.txt\n/tmp/w505-files.txt&&/tmp/w505-files.txt")[-2:] == \
        [["/tmp/w505-files.txt"], ["/tmp/w505-files.txt"]]          # put right: the newline was taken for white space
    assert parse("a | b > f\nwc -l").status == OK
    for text in ("{ a; } >f g", "(a) >f g", "if a; then b; fi >f g"):
        assert parse(text).status == SYNTAX_ERROR, text
    # R5-05: a heredoc ends at a line that is exactly the delimiter.
    for text in ("cat <<E\nx\nE \ntouch f\nE", "cat <<E\nx\nE; touch f\nE", "cat <<E\nx\nE&&touch f",
                 "cat <<E\nx\nE >f", "echo $(cat <<E\nx\nE)", "cat <<E\nx\n  E\nE\n", "cat <<E\nx\n\tE\nE\n", "cat <<-E\nx\n \tE\nE\n"):
        assert parse(text).status == SYNTAX_ERROR, text
    assert _words("cat <<E\nx\nE\n") == [["cat"]] and _words("cat <<-E\n\tx\n\t\tE\n") == [["cat"]]
    assert _words("cat <<E\nx\nE") == [["cat"]]                         # no final newline
    # R5-07: `;;` outside a case, and an esac that is only an argument.
    for text in ("touch f;; rm g", "{ a;; }", "case x in a) touch f esac; rm g", "case x in a) echo \";\" esac"):
        assert parse(text).status == SYNTAX_ERROR, text
    for text in ("case x in a) b;; esac", "case x in esac", "case x in a) b\nesac", "case x in a) b; esac",
                 "case x in a) b # c\nesac"):
        assert parse(text).status == OK, text
    # R5-10: names are ASCII, so `aé=1` is a command's name, not an assignment.
    assert _words("a\u00e9=1 touch f") == [["a\u00e9=1", "touch", "f"]]
    assert unwrap(["a\u00e9=1", "git"]).words == ("a\u00e9=1", "git")
    # R5-11: a backslash-quoted delimiter makes the body literal; the grammar's reading as code is refused.
    assert parse("cat <<E\\F\n$(touch f)\nEF\n").status == SYNTAX_ERROR
    # R5-16: a heredoc's fd
    assert parse("cat 3<<E\nx\nE\n").commands[0].redirects[0].fd == "3"
    # R5-19: a continuation between two parens
    assert parse("echo $(\\\n(touch f))").status == SYNTAX_ERROR


def test_round5_helpers():
    # R5-08: equivalent spellings of a system directory; `..` is never trusted
    assert unwrap(["/usr//bin/env", "claude"]).words == ("claude",)
    assert unwrap(["//usr/bin/env", "claude"]).words == ("claude",)
    assert unwrap(["/usr/bin/./env", "claude"]).words == ("claude",)
    assert unwrap(["/bin/../usr/bin/env", "x"]).stopped == "untrusted-path"
    assert unwrap(["./sudo", "x"]).stopped == "untrusted-path" and unwrap(["sudo", "x"]).stopped is None
    assert nested(["/usr//bin/bash", "-c", "x"]) == [_n("bash", "text", text="x")]
    assert [n.kind for n in nested(["./eval", "a"])] == ["unknown"] and [n.kind for n in nested(["eval", "a"])] == ["text"]
    # R5-02: the shell sudo -s/-i starts expands `$` in its operands
    assert unwrap(["sudo", "-s", "echo $(touch f)"]).stopped == "unsized-option"
    assert unwrap(["sudo", "-i", "make"]).words == ("make",)
    # R5-03: runuser -u runs its command directly
    assert nested(["runuser", "-u", "root", "--", "touch", "f"]) == [_n("runuser", "words", words=("touch", "f"))]
    assert [n.kind for n in nested(["runuser", "-u", "root"])] == ["unknown"]
    # R5-04: screen's list flags only count before the command
    assert nested(["screen", "-dmS", "x", "claude", "-list"]) == [_n("screen", "words", words=("claude", "-list"))]
    assert nested(["screen", "-ls"]) == []
    # R5-06: copy-mode commands that pipe to a shell command
    assert nested(["tmux", "send-keys", "-X", "copy-pipe-and-cancel", "touch f"]) == [_n("tmux send-keys", "text", text="touch f")]
    assert nested(["tmux", "send-keys", "-X", "cancel"]) == []
    # R5-09: fish runs every -c and -C
    assert [n.text for n in nested(["fish", "-C", "rm a", "-c", "true"])] == ["rm a", "true"]
    # R5-12: paste-buffer types into a shell; -f and -c name config files; a bare Enter submits typed text
    assert [n.kind for n in nested(["tmux", "paste-buffer"])] == ["unknown"]
    assert [n.kind for n in nested(["tmux", "-f", "/x", "new", "-d"])] == ["unknown"]
    assert [n.kind for n in nested(["screen", "-c", "/x", "-dm"])] == ["unknown"]
    assert [n.kind for n in nested(["tmux", "send-keys", "Enter"])] == ["unknown"]
    assert nested(["tmux", "send-keys", "ls", "Enter"]) == [_n("tmux send-keys", "text", text="ls")]
    # R5-13: script's output files
    assert nested(["script", "-qc", "make", "out.log"]) == [_n("script", "text", text="make", words=("out.log",))]


def test_child_killed_or_out_of_memory():
    import subprocess as sp
    from unittest import mock
    big = "echo " + "a " * 5000
    def fake(code, err=b""):
        return mock.patch("bashtree.core.subprocess.run", return_value=sp.CompletedProcess([], code, b"", err))
    with fake(-9):
        r = parse(big)
    assert r.status == TOO_BIG and "signal 9" in r.reason
    with fake(1, b"Traceback...\nMemoryError\n"):
        assert parse(big).status == TOO_BIG
    with fake(1, b"Traceback...\nNameError\n"):
        try:
            parse(big)
        except RuntimeError:
            pass
        else:
            raise AssertionError("a broken child must raise, not answer")
    # a real runaway: with a small cap the child dies or runs out of memory, and that is a status
    from bashtree import core
    with mock.patch.object(core, "CHILD_MEMORY", 96 << 20):
        r = parse("a;" * 300000)
    assert r.status == TOO_BIG and ("killed" in r.reason or "needed over" in r.reason
                                   or "took over" in r.reason), r.reason   # a loaded machine may hit the deadline first


def test_guards_round5():
    """Guards that no test failed without (found by removing each one)."""
    import tempfile
    from bashtree.helpers import Opts, _tmux_split, getopt
    assert [a.has_substitution for a in parse("A=<(true) cmd").commands[0].assignments] == [True]
    assert _words("echo $'\\777' $'\\U00110000'") == [["echo", "\udcff", "\\U00110000"]]   # octal is a byte; no such char
    assert _words('echo "a\rb"') == [["echo", "a\rb"]] and parse("echo a # x\ry").status == OK   # CR inside an opaque node
    assert unwrap(["/sbin/sudo", "x"]).wrappers == ("sudo",) and unwrap(["/usr/sbin/env", "x"]).wrappers == ("env",)
    assert getopt(["--force=1"], Opts(longs="force"))[2] is False                    # a flag takes no value
    assert getopt(["--out"], Opts(longs="out="))[2] is False and getopt(["-o"], Opts(values="o"))[2] is False
    assert getopt(["-a", "--", "-b"], Opts("ab")) == ([("-a", None)], ["-b"], True)
    assert getopt(["--split-string", "x", "-a", "rest"], Opts("a", longs="split-string="), until={"--split-string"}) == \
        ([("--split-string", "x")], ["-a", "rest"], True)
    assert _tmux_split(["a", "b\\;", "c", ";", "d"]) == [["a", "b;", "c"], ["d"]]       # `\;` is a literal semicolon
    assert [n.kind for n in nested(["tmux", "run-shell", "-C", "x"])] == ["unknown"]    # -C: a tmux command
    assert [n.kind for n in nested(["tmux", "send-keys", "-H", "41"])] == ["unknown"]
    assert nested(["tmux", "send-keys", "-M"]) == []
    assert unwrap(["command", "-V", "git"]).stopped == "query" and unwrap(["sudo", "-K"]).stopped == "query"
    assert nested(["bash", "--", "-c", "x"]) == [_n("bash", "unknown", words=("-c", "x"))]   # after --, -c is a file
    assert [n.kind for n in nested(["su", "--bogus"])] == ["unknown"]
    assert [n.kind for n in nested(["screen", "-Z"])] == ["unknown"]
    # The parse child runs isolated: a module planted in the working directory isn't imported.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "re.py").write_text("raise SystemExit('planted')\n")
        code = ("import sys; sys.path.insert(0, %r); from bashtree import parse; "
                "print(parse('echo ' + 'a ' * 5000).status)" % str(Path(__file__).resolve().parents[1]))
        out = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp, capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == OK, out.stderr[-300:]


def test_round6():
    """Each found by running random scripts under bash and comparing (bench/differential.py)."""
    refused = [
        "echo `date` `rm -f y`", "`a`\n`b`",                                  # one lexeme for two backtick pairs
        "ls\n\\\nrm -rf x", "cd /w\n\\\nrm -rf y",                               # a continuation opening a line
        "cat <<EE\nE\\\nE\necho HIDDEN\nEE", "cat <<E\nfoo\\\nE\necho SHOWN\nE",    # a continuation near a heredoc
        "echo a ==\nrm -rf x", "rm ==",                                       # `==` dropped, and the newline after it
        "M1 >f $\nM6 >f -rf x",                                                  # `$` joined to the next line
        "a | (b) >f rm -rf y", "a && { b; } >f rm y", "a | [[ x ]] >f rm",        # words after a redirect, last stage not a command
        "echo a >f b \\\n\n2>&1 rm -rf x",                  # the next line taken into a command
        "export >f A=$(x) B", "local >f a=1 b=2",                                # would be words, not assignments
    ]
    for text in refused:
        assert parse(text).status == SYNTAX_ERROR, text
    assert parse("a | FOO=1 >f rm -rf /").status == SYNTAX_ERROR and parse("a | ! x=1 >f rm -rf y").status == SYNTAX_ERROR
    assert _words("rm >&- x") == [["rm", "x"]] and parse("rm >&- x").commands[0].redirects[0].target is None
    # the grammar takes the newline after a pipeline for white space; it is put right, offsets unchanged
    assert _words("ps aux | grep -v grep\njobs -l 2>/dev/null") == [["ps", "aux"], ["grep", "-v", "grep"], ["jobs", "-l"]]
    assert _words("x=1 y=2\nrm -rf z") == [[], ["rm", "-rf", "z"]]
    assert parse("a | b\n# c\nd 2>&1").status == OK and _words("a | b # c\nd 2>&1") == [["a"], ["b"], ["d"]]
    c = parse("ps | grep x\njobs -l 2>&1").commands[-1]
    assert c.span == (len("ps | grep x\n"), len("ps | grep x\njobs -l"))    # still the offsets of the text given
    assert _words("echo a\nrm b") == [["echo", "a"], ["rm", "b"]] and _words("x=1\nrm -rf z") == [[], ["rm", "-rf", "z"]]
    assert [n.kind for n in nested(["su", "root", "/tmp/x.sh"])] == ["unknown"] and [n.kind for n in nested(["su", "root"])] == ["unknown"]
    assert [n.kind for n in nested(["su", "-", "root"])] == ["unknown"] and nested(["su", "-", "root", "x.sh"])[0].words == ("x.sh",)
    assert nested(["bash", "-oc", "pipefail", "rm x"]) == [_n("bash", "text", text="rm x")]
    assert nested(["bash", "-Oc", "extglob", "rm x"]) == [_n("bash", "text", text="rm x")]
    # guards found without a test
    assert parse("echo a").status == OK and parse("").status == OK
    try:
        parse(b"echo")
    except TypeError:
        pass
    else:
        raise AssertionError("parse() takes a str")
    assert nested(["tmux", "send-keys", "-M"]) == [] and nested(["screen", "-wipe"]) == []
    assert _words("echo {v}>f") == [["echo"]] and parse("echo {v}>f").commands[0].redirects[0].fd == "{v}"   # an fd
    assert _words("echo {v} >f") == [["echo", "{v}"]]                                                   # a word
    assert parse("! a >f").commands[0].negated


def test_round7():
    """Found by bench/differential.py against real bash, and by review."""
    # Repaired or recovered: bash and the tree now agree.
    assert _words("echo hi\n\\rm -rf x") == [["echo", "hi"], ["rm", "-rf", "x"]]        # \rm opening a line
    assert _words("M1 a\n\\M2 x") == [["M1", "a"], ["M2", "x"]]
    (c,) = parse("X=$A/$b.c M2").commands                                                # a word split before the 2nd $
    assert [w.text for w in c.words] == ["M2"] and c.assignments[0].value == "$A/$b.c"
    assert _words("cp $W/$f.yml /tmp/$f.bak") == [["cp", "$W/$f.yml", "/tmp/$f.bak"]]
    assert _words('M1 "q r"\\$') == [["M1", "q r$"]] and _words('M1 ")"\\* x') == [["M1", ")*", "x"]]
    assert _words("python3 - <<'EOF'\nprint(1)\nEOF") == [["python3", "-"]]               # a lone - is in no node
    assert _words("tar xf - 2>/dev/null") == [["tar", "xf", "-"]] and _words("cat - >f") == [["cat", "-"]]
    assert _words("M1 2&>f x") == [["M1", "2", "x"]] and parse("M1 2&>f x").commands[0].redirects[0].fd is None
    r = parse("echo $(>f)")
    assert [c.kind for c in r.commands] == ["simple", "redirect"] and r.commands[1].redirects[0].target.text == "f"
    assert _words("FOO=(a)b claude") == [["claude"]]                                      # the rest of the value
    assert [c.name for c in parse("cat <<E\n$((1+2))\nE").commands if not c.inside("arithmetic")] == ["cat"]   # arithmetic
    (c,) = parse("f=$C/$n.md").commands
    assert c.kind == "assignment" and c.words == () and c.assignments[0].value == "$C/$n.md"
    # Refused: the tree and bash disagree and we can't tell which commands run.
    for text in (
        "M1 $'\\\\'; M2; M3 \\'",                             # $'\\' ended at the wrong quote
        "Z=(a)#x M2 y",                                      # an assignment glued to a comment
        "echo hi >&'$(rm -rf x)'", "echo hi >&$x",           # >&WORD is expanded twice
        "echo hi >& -rf x", "echo hi <& -rf x",              # bash reads the - as a close marker
        "echo hi | FOO=1 2>&- claude", "! FOO=1 <&- claude",
        "echo `` claude",                                    # empty backticks join the words around them
        'echo >f $\n"claude"', "M1 >$ a",                    # $ and a string across white space
        "M1 \\(\\\n\\!", "M1 a\\;\\\nb",                     # a continuation after an escaped delimiter
        "M1 <<E >qf\n\\\\x\nM13\nE",                         # a heredoc body opening with a backslash
        "M1 a\\\tb", "M1 [ ]",                               # white space inside a word
        'M2 <<< "qr"\\#c x',                                 # a word glued to a redirect
        "{qfv}<qin M1\n}", "M1 &{M3;}",                      # `{qfv}` is a word, not `{`
        "M1 0<&-\n\\\nM2 >f",                               # words on the line after `<&-`
        "coproc QCP M13 '!' \\\\\n\\\nM14",                  # a continuation opening a line
    ):
        assert parse(text).status == SYNTAX_ERROR, text
    # helpers
    assert unwrap(["time", "-p", "QC=1", "M2"]).words == ("M2",) and unwrap(["!", "FOO=1", "claude"]).words == ("claude",)
    assert unwrap(["command", "FOO=1", "claude"]).words == ("FOO=1", "claude")               # not a keyword
    assert unwrap(["sudo", "-i"]).stopped == "unsized-option" and unwrap(["sudo", "-s"]).stopped == "unsized-option"
    assert nested(["trap", "M2", "EXIT"]) == [_n("trap", "text", text="M2")] and nested(["trap", "-p"]) == []
    assert nested(["trap", "--", "M2", "EXIT"]) == [_n("trap", "text", text="M2")] and nested(["trap", "-", "EXIT"]) == []
    assert [n.kind for n in nested(["let", "a[$(M2)]"])] == ["unknown"] and nested(["let", "a=1"]) == []
    assert [n.kind for n in nested(["declare", "a[`M2`]=1"])] == ["unknown"] and nested(["read", "x"]) == []


def test_r8a_quoted_subscripts_literal_flag_fd_and_dollar():
    # R8-1: arithmetic and subscripts run a substitution that single quotes do not protect.
    for s in ["(( 'a[$(M1)]' ))", "[[ 'a[$(M1)]' -eq 1 ]]", "[[ -v 'a[$(M1)]' ]]", "echo $(( 'a[`M1`]' ))",
              "echo $[ 'a[$(M1)]' ]", "a['$(M1)']=1", "A=(['$(M1)']=1)", "echo ${a['$(M1)']}",
              "echo ${!a['$(M1)']}", "declare a[\\`M1\\`]=1", "A=([a[\\`M1\\`]]=1)", "(( x = 'a[$(M1)]' + 1 ))",
              "A=([x]=1 [`M1`]=2)"]:
        assert parse(s).status == SYNTAX_ERROR, s
    assert _words("echo 'a $(x)'") == [["echo", "a $(x)"]] and _words("A=(a b)") == [[]]
    # R8-3: `$1`, `$$`, `$-` after a quote expand, so the word is not literal.
    for s in ["set -- zz; M1 >a'b'$1", "M1 >/tmp/f.$$", "M1 a$$", "M1 'a'b$1", "M1 a\"b\"$1", "M1 1'b'$-x",
              "M1 a'b'$@x", "M1 a'b'$!x", "qv=zz; M1 a'b'$qv\\ y"]:
        c = cmds(s)[-1]
        assert not (c.redirects[0].target if c.redirects else c.words[-1]).literal, s
    assert cmds("M1 'a$1' b")[0].words[1].literal and cmds("M1 \"a>(b)\"")[0].words[1].literal
    # R8-8 / R8-9: a descriptor bash does not take is refused, not dropped from the words.
    for s in ["ls -1>out", "head -n1>out file", "sort -ofile1>/dev/null in", "M1 -2>&1", "M1 -1<<E\nz\nE",
              "M1 99999999999>qf", "M1 2147483648>qf"]:
        assert parse(s).status == SYNTAX_ERROR, s
    assert _words("M1 {aé}>qf") == [["M1", "{aé}"]] and cmds("M1 2147483647>qf")[0].redirects[0].fd == "2147483647"
    assert cmds("M1 {v}>f")[0].redirects[0].fd == "{v}"
    # R8-10: an escaped $ glued to a here-string.
    for s in ["M1 <<< $q\\$ x", "M1 <<< 'a'\\$ x", "M1 <<< \"a\"\\$"]:
        assert parse(s).status == SYNTAX_ERROR, s
def test_r8b_hidden_commands():
    # R8-2: $'\x{HH}' is a hex escape (low byte), so these are the commands M1 and rm.
    assert _words("$'\\x{4d}1'") == [["M1"]] and _words("$'M\\x{31}' a") == [["M1", "a"]]
    assert _words("$'\\x{72}m' -rf x") == [["rm", "-rf", "x"]] and _words("$'\\x{4141}'") == [["A"]]
    # R8-14: bytes that are not UTF-8 stay distinct; \c\\ eats both backslashes.
    assert _words("echo $'\\xc3' $'\\xff'") == [["echo", "\udcc3", "\udcff"]]
    assert _words("echo $'\\c\\\\'") == [["echo", "\x1c"]]
    # R8-4: a continuation after `=` leaves the value empty; the next word is the command.
    for t in ("QC=\\\n M1 a b", "QC=\\\n\nM1", "A=1 QC=\\\n rm -rf x", "QC+=\\\n M1 a", "{ QC=\\\n M1 a; }"):
        assert parse(t).status == SYNTAX_ERROR, t
    assert _words("QC=1 M1 a") == [["M1", "a"]]
    # R8-5: the delimiter is what bash reads, so an operator in it means the text is not what it seems.
    for t in ("M1 <<E|M2\nz\nE|M2\n", "M1 <<E;M2\nz\nE;M2\n", "M1 <<E>qf\nz\nE>qf\n", "M1 <<E&&M2\nE&&M2\nM3\n"):
        assert parse(t).status == SYNTAX_ERROR, t
    assert _words("M1 <<'E|M2'\nz\nE|M2\n") == [["M1"]]
    # R8-6: a quoted string whose node starts at a continuation, hiding a CR.
    for t in ("M1 'x''x''x'$\\\r\n'M2' a b", "M1 a'x'$\\\r\n'M2' a", 'M1 "x""x""x"$\\\r\n\'M2\' a'):
        assert parse(t).status == SYNTAX_ERROR, t
    # R8-11: an escaped blank on a heredoc's command line is a word, not nothing.
    for t in ("M1 <<E \\ \nz\nE\n", "M1 <<E | M2 'a' \\ \nz\nE\n"):
        assert parse(t).status == SYNTAX_ERROR, t
    assert _words("M1 <<E | M2 a\nz\nE\n") == [["M1"], ["M2", "a"]]
def test_r8c_child_launchers_guards():
    """R8-7, R8-12, R8-13."""
    code = "from bashtree import parse; print(parse('echo hi >f${\\U0010ffff}').status)"
    for _ in range(2):                                   # the crash is nondeterministic; the child keeps it out
        out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip() in (TOO_BIG, SYNTAX_ERROR), out
    assert parse(">qj >qk #c\nM4").status == SYNTAX_ERROR              # newline after a comment
    assert parse("M3 >qf qa\\\\\n\\\n qb").status == SYNTAX_ERROR        # a continuation opens a line
    assert parse('e>"" $((1\n)) x').status == OK                       # a newline inside one word
    assert parse("M1 >f a\nM2 b").status == OK
    for prog in ("doas", "pkexec", "chroot", "taskset", "chrt", "numactl", "unshare", "nsenter", "setpriv",
                 "strace", "ltrace", "fakeroot", "flock", "watch", "parallel", "xvfb-run", "systemd-run",
                 "ssh", "docker", "kubectl", "xterm"):
        assert nested([prog, "x"]) == [_n(prog, "unknown", words=("x",))], prog
    assert [n.kind for n in nested(["su", "--bogus", "-c", "M2"])] == ["unknown"]
    assert nested(["runuser", "-l", "u"])[0].kind == "unknown" and nested(["su"])[0].kind == "unknown"


def test_guards_after_the_r8_merge():
    try:
        parse(b"echo")
    except TypeError:
        pass
    else:
        raise AssertionError("parse() takes a str")
    assert nested(["su", "--bogus"]) == [_n("su", "unknown", words=("--bogus",))]   # a bad option: all the words, unread
    assert nested(["tmux", "send-keys", "-M", "ls", "Enter"]) == []            # mouse events, not typed keys
    assert parse("M1 2147483647>qf").status == OK and parse("M1 2147483648>qf").status == SYNTAX_ERROR
    assert _words("M1 2147483648 >qf") == [["M1", "2147483648"]]               # too big for an fd: an argument


def test_r9i_git_args_nested_wrappers_source():
    g = git_args(["git", "-C", "/abs", "push"])
    assert (g.options, g.subcommand, g.rest, g.ok, g.unknown) == ((("-C", "/abs"),), "push", (), True, None)
    assert git_args(["git", "-c", "user.name=v", "push", "--force"]).options == (("-c", "user.name=v"),)
    assert git_args(["git", "-c", "user.name=v", "push", "--force"]).rest == ("--force",)
    g = git_args(["git", "-ccore.x=1", "push"])                 # git itself rejects the glued form
    assert (g.ok, g.unknown, g.subcommand) == (False, "-ccore.x=1", None)
    assert git_args(["git", "--git-dir=/x", "push"]).options == (("--git-dir", "/x"),)
    assert git_args(["git", "--git-dir", "/x", "push"]).subcommand == "push"
    assert git_args(["git", "--config-env=user.name=V", "push"]).options == (("--config-env", "user.name=V"),)
    g = git_args(["git", "--no-pager", "-C", "x", "commit", "-m", "m"])
    assert g.options == (("--no-pager", None), ("-C", "x")) and g.subcommand == "commit" and g.rest == ("-m", "m")
    assert git_args(["git", "--exec-path", "push"]).subcommand == "push"      # attached value only
    g = git_args(["git", "--bogus", "push"])
    assert (g.ok, g.unknown, g.rest) == (False, "--bogus", ("--bogus", "push"))
    assert not git_args(["git", "-C"]).ok and git_args(["git"]).subcommand is None
    assert nested(("sudo", "bash", "-c", "rm x")) == [_n("bash", "text", "rm x")]
    assert nested(("env", "bash", "-c", "x")) == [_n("bash", "text", "x")]
    assert nested(("sudo", "bash", "-c", "rm x"), unwrap_first=False) == []
    assert nested(("sudo", "-e", "f")) == [_n("sudo", "unknown", words=("f",))]
    assert nested(("source", "f")) == [_n("source", "unknown", words=("f",))]
    assert nested((".", "f")) == [_n(".", "unknown", words=("f",))]
    assert nested(("trap", "--", "touch x", "EXIT")) == [_n("trap", "text", "touch x")]
    ws = parse('env "${a}${b}_COUNT=1" git push').commands[0].words
    u = unwrap(ws)
    assert u.stopped == "nonliteral-assignment" and u.words[-2:] == ("git", "push"), u
    assert unwrap(parse('A=$x env B=$y sudo -u r git push').commands[0].words).words == ("git", "push")
    assert unwrap(parse('sudo "${a}_X=1" git').commands[0].words).stopped == "nonliteral-assignment"
    assert unwrap(parse('"${a}_X=1" git').commands[0].words).stopped == "nonliteral-assignment"
    assert unwrap(["env", "${a}_X=1", "git"]).words == ("git",)       # plain strings as before
    assert nested(("trap", "-l")) == [] and nested(("trap", "EXIT")) == []
def test_r9h_heredoc_line_with_redirect_and_pipe_falls_back():
    t = "cat <<'EOF' 2>&1 | tail\nx $(y)\nEOF\nls -l"
    cs = cmds(t)
    assert [[w.text for w in c.words] for c in cs] == [["cat"], ["tail"], ["ls", "-l"]]
    assert cs[0].redirects[0].body == "x $(y)\n" and cs[0].redirects[0].heredoc_quoted is True
    assert cs[0].redirects[0].target.text == "EOF" and [r.op for r in cs[0].redirects] == ["<<", ">&"]
    assert [t[c.words[0].span[0]:c.words[0].span[1]] for c in cs] == ["cat", "tail", "ls"]   # offsets unchanged
    t = "python - <<'EOF' 2>&1 | grep -v W\nprint(1)\nEOF\ntouch z"
    assert [[w.text for w in c.words] for c in cmds(t)] == [["python", "-"], ["grep", "-v", "W"], ["touch", "z"]]
    assert cmds("cat <<EOF >f | cat\nhi $x\nEOF\n")[0].redirects[0].body == "hi $x\n"
    assert cmds("cat <<-EOF >f | cat\n\thi\n\t\tEOF\n")[0].redirects[0].body == "hi\n"
    assert cmds("cat <<-EOF >f | cat\n\thi\n\t\tEOF\n")[0].redirects[0].op == "<<-"
    two = cmds("cat <<a >f | cat; cat <<b | wc\n1\na\n2\nb\nls")
    assert [r.body for c in two for r in c.redirects if r.op == "<<"] == ["1\n", "2\n"]
    # refusals: a missing or loose terminator, and an unquoted body holding commands
    for bad in ("cat <<'EOF' 2>&1 | tail\nx\n", "cat <<'EOF' 2>&1 | tail\nx\nEOF \n",
                "cat <<EOF >f | cat\nhi $(rm x)\nEOF\n", "cat <<EOF >f | cat\nhi `rm x`\nEOF\n",
                "cat <<EOF >f | cat\n${x:-$(rm y)}\nEOF\n",
                "echo $((1<<3)); cat <<'EOF' 2>&1 | tail\nx\n3\nEOF\n"):
        assert parse(bad).status == SYNTAX_ERROR, bad
    assert parse("cat <<EOF >f | cat\nhi $(rm x)\nEOF\n").reason == "a heredoc body the fallback cannot read"
    assert cmds("cat <<'EOF' 2>&1 | tail\n$(rm x) `y`\nEOF\n")[0].redirects[0].body == "$(rm x) `y`\n"
    # the normal parse and here-strings carry the body too
    assert cmds("cat <<E\nx\nE")[0].redirects[0].body == "x\n"
    assert cmds("cat <<E\nx $(y)\nE")[0].redirects[0].body is None
    assert cmds("bash <<< 'touch hit'")[0].redirects[0].body == "touch hit"


def test_r9h_continuation_in_opener():
    for bad in ('"M25" <<"E" ; M24 \\\n\'\\n\'\n$((1+$(M15)))\nE\n\nM26\t&>>qj\t> qf',
                "cat <<E 2>&1 | cat \\\nx\nbody\nE\n",
                "cat <<a | cat\n1\na\ncat <<b 2>&1 | cat \\\nz\n2\nb\n"):
        r = parse(bad)
        assert r.status == SYNTAX_ERROR and r.reason == "a heredoc body the fallback cannot read", (bad, r)


def test_r10k_git_args_commands_and_nonliteral():
    for k in ("alias.p=!touch x", "core.fsmonitor=x", "CORE.SshCommand=x", "core.pager=x", "core.editor=x",
              "core.hooksPath=x", "credential.helper=x", "credential.https://h.helper=x", "diff.external=x",
              "diff.foo.textconv=x", "filter.f.smudge=x", "gpg.program=x", "gpg.ssh.program=x",
              "sequence.editor=x", "pager.log=x", "include.path=x", "alias.p=$x"):
        g = git_args(["git", "-c", k, "p"])
        assert (g.ok, g.runs_command, g.unknown, g.subcommand) == (False, True, k, None), k
    g = git_args(["git", "--config-env=alias.p=VAR", "p"])
    assert (g.ok, g.runs_command) == (False, True)
    g = git_args(["git", "-c", "user.name=x", "-c", "alias.p=push", "p"])
    assert (g.ok, g.runs_command, g.subcommand) == (False, True, None)   # r11b: any alias.* is unsafe
    ws = parse('git -c core.pager=x status').commands[0].words
    assert git_args(ws).runs_command and git_args(parse("git -C d push").commands[0].words).subcommand == "push"
    for bad in ('git $x push', 'git "$x" push', 'git pu* x', 'git p{a,b}', 'git `x` push'):
        g = git_args(parse(bad).commands[0].words)
        assert not g.ok and g.subcommand is None and not g.runs_command, bad
    for bad in ("$x", "p`x`", "pu*", "p{a,b}"):
        assert not git_args(["git", bad, "push"]).ok, bad


def test_r10k_unwrap_nonliteral_program_and_launchers():
    for ws in (["sudo", "$S", "bash", "-c", "x"], ["env", "$E", "bash", "-c", "x"], ["$S", "x"], ["env", "A=1", "pu*"]):
        u = unwrap(ws)
        assert u.stopped == "nonliteral-program" and u.words[-1] != "bash" or u.words[0] in ("$S", "pu*"), u
        assert nested(ws)[0].kind == "unknown", ws
    u = unwrap(parse('sudo "$S" bash').commands[0].words)
    assert u.stopped == "nonliteral-program" and u.words == ("$S", "bash")
    assert unwrap(["sudo", "[", "-f", "x", "]"]).stopped is None and unwrap(["sudo", "git"]).stopped is None
    assert nested(["$S", "x"], unwrap_first=False) == [_n("$S", "unknown", words=("x",))]
    assert unwrap(["env", "-S", "touch a"]).wrappers == ("env",)
    assert unwrap(["env", "-S", "env -i touch a"]).wrappers == ("env", "env")
    assert unwrap(["env", "-S", "touch a"]).words == ("touch", "a")
    assert nested(["chronic", "sh", "-c", "x"]) == [_n("chronic", "unknown", words=("sh", "-c", "x"))]
    assert nested(["ifne", "-n", "sh"])[0].kind == "unknown"
def test_r10j_eq_operators_are_words():
    for s, w in (("echo == x", ["echo", "==", "x"]), ("echo a == b", ["echo", "a", "==", "b"]),
                 ("echo =~ y", ["echo", "=~", "y"]), ("rm == x", ["rm", "==", "x"]),
                 ("echo == $d", ["echo", "==", "$d"]), ("echo; echo == x == y", None)):
        r = parse(s)
        assert r.status == "ok", (s, r)
        if w:
            assert [x.text for x in cmds(s)[0].words] == w, s
    assert "ok" == parse("echo ==$(b)").status and "b" in [c.name for c in cmds("echo ==$(b)")]
    assert parse("[[ a == b ]]").status == "ok"
    # the operator at a line end swallows the line in the grammar: refused
    for bad in ("echo ==\nrm -rf x", "echo x ==", "echo ==\\\n x"):
        assert parse(bad).status == SYNTAX_ERROR, bad


def test_r10j_heredoc_body_unknown_on_multiline_opener():
    for s in ("bash <<E | w 'a\nE\nb' ; h1\nh9 evil\nE\n", 'bash <<E | w "a\nE\nb"\nh9\nE\n',
              "bash <<E | w $(h1\nE\nh2\n)\nh9\nE\n"):
        r = parse(s)
        assert r.status == SYNTAX_ERROR or r.commands[0].redirects[0].body is None, s
    assert cmds("cat <<E\nE\n")[0].redirects[0].body == ""
    assert cmds("cat <<E\nx\nE\n")[0].redirects[0].body == "x\n"


def test_r11a_opener_spanning_shapes_never_give_a_body():
    for s in ("cat <<E; : \"'\" 'a\nE\nb'\nH\nE\n", "cat <<E; : '\"' \"a\nE\nb\"\nH\nE\n",
              "cat <<E; : \\' 'a\nE\nb'\nH\nE\n", 'cat <<E; : $(echo ")" ; echo\nE\n)\nH\nE\n',
              "cat <<E; : $(echo ')'\nE\n)\nH\nE\n", "cat <<E; : $(echo \\)\nE\n)\nH\nE\n",
              "cat <<E; : `echo a\nE\nb`\nH\nE\n", "cat <<E; : <(a\nE\n)\nH\nE\n",
              "cat <<E; : $'a\\'\nE\n'\nH\nE\n", 'cat <<E; : "a\\"\nE\nb" "c"\nH\nE\n',
              "bash <<E; : \"'\" 'a\nE\nb'\nh9 evil\nE\n"):
        r = parse(s)
        assert r.status == SYNTAX_ERROR or all(x.body is None for c in r.commands for x in c.redirects), s
    # the E inside the quoted string is never blanked out of a word that is reported
    r = parse("cat <<E; : 'a\nE\nb'\nH\nE\n")
    assert r.status == SYNTAX_ERROR or "a\nE\nb" in [w.text for c in r.commands for w in c.words]
    assert cmds("cat <<'E' | w\nx\nE\n")[0].redirects[0].body == "x\n"
    assert cmds("cat <<-E\n\tx\n\tE\n")[0].redirects[0].body == "x\n"
    assert [x.body for x in cmds("cat <<A <<B\n1\nA\n2\nB\n")[0].redirects] == ["1\n", "2\n"]
    assert cmds("bash <<E\nh\nE\n")[0].redirects[0].body == "h\n"
    assert cmds("cat <<E 2>&1 | tail\nx\nE\n")[0].redirects[0].body == "x\n"
def test_r11b_git_inverted_allowlist_and_alias():
    for k in ("trailer.x.command=touch hit", "remote.o.uploadpack=touch hit", "remote.o.receivepack=touch hit",
              "gpg.ssh.defaultKeyCommand=touch hit", "core.alternateRefsCommand=touch hit",
              "difftool.kdiff3.path=./tool.sh", "mergetool.m.path=x", "man.m.path=x",
              "protocol.ext.allow=always", "remote.e.url=ext::sh -c touch% hit", "totally.unknown=1",
              "alias.p=push", "http.sslVerify=false"):
        g = git_args(["git", "-c", k, "fetch", "o"])
        assert (g.ok, g.runs_command, g.unknown) == (False, True, k), k
    for k in ("user.name=x", "color.ui=always", "core.quotepath=false", "advice.detachedHead=false", "pull.rebase=true"):
        assert git_args(["git", "-c", k, "status"]).ok, k
    g = git_args(["git", "-c", "alias.p=push", "p", "origin", "main"])
    assert (g.ok, g.runs_command, g.subcommand) == (False, True, None)
    for rest in (["fetch", "ext::sh -c touch% hit"], ["ls-remote", "ext::x"], ["clone", "fd::17/x"],
                 ["fetch", "--upload-pack=ext::x", "o"]):
        g = git_args(["git"] + rest)
        assert (g.ok, g.runs_command) == (False, True), rest
    assert git_args(["git", "commit", "-m", "fix a:b"]).ok


def test_r11b_unwrap_nonliteral_operand():
    for cmd in ("timeout $T sh -c 'touch hit'", "nice -n $N sh -c 'touch hit'", "env -u $U sh -c 'touch hit'",
                "timeout -s $S sh -c 'touch hit'", "sudo -u $U sh -c 'touch hit'", 'timeout "$T" sh -c x'):
        u = unwrap(parse(cmd).commands[0].words)
        assert u.stopped == "nonliteral-operand" and u.words[0] in ("timeout", "nice", "env", "sudo"), cmd
        assert nested(parse(cmd).commands[0].words)[0].kind == "unknown", cmd
    u = unwrap(parse("timeout 5 sh -c x").commands[0].words)
    assert (u.stopped, u.words) == (None, ("sh", "-c", "x"))
    assert unwrap(["nice", "-n", "5", "sh"]).stopped is None


def test_r12_git_exec_path():
    g = git_args(["git", "--exec-path=.", "foo"])
    assert (g.ok, g.runs_command) == (False, True)
    assert git_args(["git", "--exec-path", "status"]).ok


def test_r12_git_unquoted_expansion_value():
    for cmd in ("git -C $d status", "git --git-dir=$d status", "git --work-tree=`x` status",
                "git --namespace=$d status", "git --attr-source=$d status"):
        g = git_args(parse(cmd).commands[0].words)
        assert (g.ok, g.runs_command) == (False, True), cmd
    assert git_args(parse('git -C "$d" status').commands[0].words).ok
    assert git_args(parse("git -C /x status").commands[0].words).ok


def test_r12_env_split_keeps_operand_check():
    u = unwrap(parse("env -u $U -S 'echo hi'").commands[0].words)
    assert u.stopped == "nonliteral-operand"
    assert unwrap(parse("env -S 'echo hi'").commands[0].words).words == ("echo", "hi")


def test_r12_runuser_nonliteral_user():
    n = nested(parse("runuser -u $U -- echo hi").commands[0].words)
    assert n[0].kind == "unknown"


def test_r11b_launchers_listed():
    for p in ("unbuffer", "sshpass", "gosu", "su-exec", "bwrap", "faketime", "chpst", "nsenter"):
        assert nested([p, "x"])[0].kind == "unknown", p


def test_merge_guards_r11():
    r = git_args(["git", "-c", "USER.NAME=x", "push"])                    # git folds key case; the allowlist is lowercase
    assert r.ok and not r.runs_command and r.subcommand == "push"
    assert not git_args(["git", "-c", "core.sshCommand=x", "push"]).ok


def test_child_only_when_needed():
    from unittest import mock
    from bashtree import core
    with mock.patch.object(core, "_parse_in_child", wraps=core._parse_in_child) as child:
        assert parse("echo caf\u00e9 \u65e5\u672c").status == OK       # non-ASCII, in the BMP: in process
        assert child.call_count == 0
        assert parse("echo hi >f${\U0010ffff}").status in (TOO_BIG, SYNTAX_ERROR)   # outside the BMP: the child
        assert child.call_count == 1


def test_r12_b7_command_after_heredoc_is_not_hidden():
    for t in ("cat <<E && {\nE\n:; }\ntouch hit\nE\n", "cat <<A && cat <<B\nx\nA\ny\nB\ntouch hit\nA\n",
              "cat <<E | {\nE\n:; }\ntouch hit\nE\n"):
        assert parse(t).status == SYNTAX_ERROR, t
    assert cmds("cat <<A; cat <<B\nx\nA\ny\nB\n")[1].redirects[0].body == "y\n"
    assert cmds("cat <<E | tail\nhi\nE\nls\n")[0].redirects[0].body == "hi\n"


def test_r12_b2c_wrong_body_is_refused():
    for t in ("cat >o <<E ; a=(x\nE\ny)\nH\nE\n", "cat >o <<E ; ((1+\nE\n+1))\nH\nE\n",
              "cat >o <<E && : \\\nE\n\nH\nE\n"):
        assert parse(t).status == SYNTAX_ERROR, t


def test_r12_b2c_declare_and_same_delimiter():
    for t in ("cat <<E; declare -a a=(\nE\n)\nH\nE\n", "cat <<EOF && cat <<EOF\na\nEOF\nb\nEOF\n"):
        assert parse(t).status == SYNTAX_ERROR, t


def test_r12_spanning_opener_body_is_none():
    for t in ("cat <<E && echo \"a\nb\"\nhi\nE\n", "cat <<E | echo \"a\nb\"\nhi\nE\n"):
        assert cmds(t)[0].redirects[0].body is None, t


def test_child_has_a_memory_cap():
    from unittest import mock
    import subprocess as sp
    from bashtree import core
    seen = []
    def fake(args, **kw):
        seen.append(args[3])
        return sp.CompletedProcess(args, 0, core.pickle.dumps(core.Result(OK, ())), b"")
    with mock.patch("bashtree.core.subprocess.run", side_effect=fake):
        core._parse_in_child(b"echo hi")
    assert "RLIMIT_AS" in seen[0] and str(core.CHILD_MEMORY) in seen[0]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # noqa: BLE001 - counted and reported, the run fails
                failed += 1
                print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")
    sys.exit(1 if failed else 0)
