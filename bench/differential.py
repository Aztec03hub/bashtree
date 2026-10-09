"""ROUND 8 WIDENED COPY. Copy over bench/differential.py to run it (run it only through bench/sweep.sh).

New modes: xgen (generator with round 8 statement templates mixed in), xmut (plus 1-3 random fragment edits), xbnd (plus operator /
white-space boundary swaps), xboth (half and half), xcont (backslash-newline variants at token boundaries), list (the scripts of
bench/listfile.py: S = [...], hand-written, markers only). SHOW=1 in list mode also logs each script's parse and bash result to
bench/show.out. New checks: quoted substitutions in arithmetic and subscript contexts, ANSI-C brace-hex escapes, expansions in words the
grammar splits (literal flag), assignment and heredoc delimiter glue, eval and trap texts are parsed too (their commands count as known),
the literal prefix of every word list is compared with bash (not only all-literal commands), a redirect fd must be a number or {name},
a word's raw text must be the source text at its span.
Original header follows.

Differential test against real bash: does `ok` ever hide a command, or get words or
redirect targets wrong?  (Widened in review round 7.)

   Original description: does `ok` ever hide a command, or get words or
redirect targets wrong? Run with the bashtree venv python:

    python bench/differential.py N SEED [mode]      mode: gen (default) | mut | both

Safety: every command word is a marker function Mi() that only appends its argv
to a log inside the throwaway directory; PATH is empty; stdin is /dev/null;
cwd is a fresh temp dir. Nothing from a corpus is ever run.

Checks per script, when parse() says ok:
 1. every marker bash ran is the name of a parsed command ("hidden"), after unwrap()
 2. for a marker with literal-only words, bash's argv == parse()'s word texts ("words")
 3. every file bash created in cwd is the literal target of some parsed redirect ("redir")
A problem is reported only if it repeats on a second bash run (two background jobs can tear
a log record). Nothing is filtered out: both classes it once skipped are fixed.
Modes: gen = grammar-built scripts; mut = the same with 1-3 random edits (fragments such as
\\<newline>, `, $(, CR, #, ;;, <<E); both = half and half. BASHTREE_ROOT picks the checkout.
New shapes against bench/differential.py: case/esac with ;& and ;;&, function forms with
redirects, [[ ]] and (( )) with substitutions, here-strings, process substitution, coproc,
time, !, |&, brace/subshell redirects, declare/export/unset forms, array assignments, comments
in odd places, CRLF, continuations in many places, Unicode words, quoting variety, command
names written quoted or escaped, one or two heredocs with 10 delimiter forms and 25 body forms.
"""
import multiprocessing as mp
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.environ.get("BASHTREE_ROOT", str(Path(__file__).resolve().parents[1])))
from bashtree import OK, parse, unwrap  # noqa: E402
from bashtree.helpers import nested  # noqa: E402

BASH = shutil.which("bash")

WORDS = [
    "qa", "qb", "-qf", "--qopt=v", "1", "'q r'", '"q r"', '"q\\"r"', "'a'\"b\"c", "a\\ b", "$'x\\ty'", "$'\\x41\\u00e9'",
    '$"abc"', "a\\\\b", '"a\\\\b"', '"a\\$b"', '"a\\`b"', '"\\a"', "'\\a'", "'a\\'", "é", "日本語", "\U0001f600", "'*'", '"*"',
    "\\*", "$'a\\nb'", "$'a\\0b'", "''", '""', "a''b", 'a""b', "'it'\\''s'", "qa=qb", "x#y", "\\#c", "'#c'", "a\\;b", "';'",
    '"a b"c', "$'\\''", "$'\\\\'", "'\\n'", '"\\n"', '"$"', "'$'", "\\$", "\\`", "a\\\"b", "%s", "-", "--", "=", "a=", "==",
    "=~", "\\(", "'('", '")"', "\\<", "\\>", "'>f'", "\\&", "\\|", '"tab\there"', "café", "​", "\xa0",
    '$"x$"', '"$(:)"', "!", "\\!", "'!'", "{", "}", "'{'", "\\}", "[", "]", "[[", "]]", "((", "case", "esac", "if", "fi", "do",
    "done", "then", "in", "function", "time", "coproc", ";;", "'q'\"r\"s", '"\\\\\\""', "\\\\", "a\\\nb",
]
OUTR = [">qf", ">>qg", ">|qh", "&>qi", "&>>qj", "2>qk", "1>ql", "3>qm", "{qfd}>qn", ">&qo", "2>&1", ">&-", "2>&-", "<>qp",
        "> qf", "2> 'q r'", '>"q s"', ">q\\ t", ">$'u\\tv'", ">'w'x", ">w'x'", ">>$'y'", "&>'zz'", ">&2", ">/dev/null"]
INR = ["<qin", "0<qin", "3<qin", "<&-", "<<<qa", "<<<'q r'", '<<<"x y"', "<< EOF", "<<<$(M)", "< qin", "{qfv}<qin", "<&0", "0<&-"]



# ---- round 8: statement templates and mutations (was bench/gen8.py) -------------------------------------
import re  # noqa: E402
SUBS = ["'a[$(§)]'", "$'a[$(§)]'", '"a[$(§)]"', "a[$(§)]", "a['$(§)']", "'a[`§`]'", 'a["$(§)"]', "'$(§)'",
        "1+'a[$(§)]'", "a[\\$(§)]", "'a['\"$(§)\"']'", "a[$'$(§)']", "a[\\`§\\`]", "a[$\"$(§)\"]", "a['$(§)'][1]"]
SUBCTX = ["(( @ ))", "[[ @ -eq 1 ]]", "[[ -v @ ]]", "echo $(( @ ))", "echo $[ @ ]", "@=1", "A=([@]=1)", "echo ${a[@]}",
          "echo ${!a[@]}", "echo ${x:@}", "echo ${x:1:@}", "declare @=1", "[[ @ == a ]]", "for ((i=@;;)); do break; done",
          "((@)) && qa", "let @", "x=$(( @ ))", "echo ${#a[@]}", "unset @", "printf -v @ x", "read @", "export @=1",
          "QX=1 @=2 qa", "local @", "echo ${a[@]:-q}", "echo ${a[@]+q}", "echo ${a[@]:@}", "[[ x -lt @ ]]", "echo ${x:-@}",
          "a[@]+=1", "a[@]=(1)", "declare -a a[@]", "[[ @ -gt @ ]]", "(( a[@]++ ))", "(( @ , @ ))", "$(( @ ))", "echo $((@))"]

PATS = ["$(§)", '"$(§)"', "a|$(§)", "[$(§)]", "*$(§)*", "'$(§)'", "a$(§)b", "`§`", "$(§)|$(§)", "$'$(§)'", "@($(§))", "$(§)*"]
ITEMS = ["$(§)", "`§`", "a $(§) b", "<(§)", '"$(§)"', "a${x:-$(§)}", "\\$(§)", "$((1))$(§)", "{a,$(§)}"]
LOOPS = ["for qi in @; do ¤; break; done", "select qs in @; do ¤; break; done", "for qi in @\ndo\n¤\nbreak\ndone",
         "for qi in @;do ¤;break;done", "for qi in @; { ¤; break; }", "for qi in @ ; do ¤ ; break ; done",
         "for qi in @\n\ndo ¤; break; done", "for qi\ndo ¤; break; done", "for qi do ¤; break; done", "for qi in @; do\n\\\n¤; break; done"]
FNHEAD = ["qfn()", "function qfn", "function qfn()", "qfn ()", "qfn(  )", "qfn\n()", "function qfn\n", "function  qfn  ()", "function qfn() "]
FNBODY = ["{ ¤; }", "( ¤ )", "if ¤; then ¤; fi", "while ¤; do ¤; break; done", "[[ -n $(§) ]]", "(( 1 + $(§) ))", "case x in x) ¤;; esac",
          "for qi in 1; do ¤; done", "{ ¤\n}", "{\n¤\n}", "(¤)", "{ ¤; } >qg", "{ ¤; } <<E\nz\nE"]
POST = ["", " >qf", " 2>&1", " <<E\nz\nE", " >qf 2>qg", " <qin", " > qf", " &>qf", " 2>&1 >qf", " <<E >qf\nz\nE", " >qf <<E\nz\nE",
        " >&2", " 2>/dev/null", " &", " | ¤", " && ¤"]

HD = ["§ <<E\n$(§ <<F\nz\nF\n)\nE",
      "§ <<E\n$(§ <<<qa)\nE",
      "§ <<E\n$(§ <<E\nz\nE\n)\nE",
      "§ <<E\n$(§ <<'F'\n$(§)\nF\n)\nE",
      "§ <<E\n`§ <<F\nz\nF\n`\nE",
      "§ <<E\n${x:-$(§ <<<q)}\nE",
      "§ <<'E'\n$(§ <<F\nF\n)\nE",
      "§ $(§ <<E\nz\nE\n) <<F\ny\nF",
      "§ <<< $(§ <<< x)",
      "§ <<E\n$(§ <<-F\n\tz\n\tF\n)\nE",
      "§ <<-E\n\t$(§)\n\tE",
      "§ <<E\n$(§\n)\nE\n§",
      "§ <<E | §\n$(§)\nE",
      "§ <<E\n$(§) $(§)\nE",
      "§ <<E\n$(§ <<F\n$(§ <<G\nz\nG\n)\nF\n)\nE",
      "§ <<E\n$(§ <<<$(§ <<<q))\nE",
      "§ <<E\n$(case x in x) § <<F\nz\nF\n;; esac)\nE",
      "§ <<E\n$( § <<F\nz\nF\n\n)\nE\n§",
      "§ <<E; § <<<q\nz\nE",
      "§ <<E && § <<F\nz\nE\ny\nF",
      "§ <<E\n\\$(§)\n$(§)\nE",
      "§ <<E\n$(§)\\\n$(§)\nE",
      "§ <<E\n$((1+$(§ <<F\nz\nF\n)))\nE",
      "§ <<\"E\"\n$(§ <<F\nF\n)\nE",
      "§ <<E\n#$(§)\nE",
      "§ <<E\n'$(§ <<F\nF\n)'\nE",
      "§ <<E\n\"$(§ <<F\nF\n)\"\nE",
      "{ § <<E\n$(§)\nE\n}",
      "$(§ <<E\n$(§)\nE\n)",
      "(§ <<E\nz\nE\n)",
      "f() { § <<E\nz\nE\n}; f",
      "if § <<E\nz\nE\nthen §; fi",
      "while § <<E; do break; done\nz\nE",
      "§ <<E $(§)\nz\nE",
      "§ <<E >$(§)\nz\nE"]

PFX = ["time ¤ | ¤", "time -p ¤ && ¤", "! time ¤ | ¤", "time ! ¤", "coproc ¤ | ¤", "coproc QC { ¤; } >qf", "time { ¤; } 2>&1",
       "! { ¤; } | ¤", "! ( ¤ ) && ¤", "time -p ! ¤", "! ! ¤", "time time ¤", "coproc coproc ¤", "time\n¤", "!\n¤", "time ¤ &",
       "coproc QC ( ¤ )", "time -p -- ¤", "time -- ¤", "time -x ¤", "! QX=1 ¤", "time QX=1 ¤", "coproc QX=1 ¤", "time ¤ <<E\nz\nE", "! ¤ <<E\nz\nE", "time ¤ >qf | ¤",
       "! (( 1 ))", "time [[ x ]]", "time ((1))", "time (¤)", "coproc (¤) >qf", "coproc { ¤; } 2>&1 | ¤", "time $(§)", "! $(§)", "coproc $(§)", "time >qf", "! >qf", "time\n>qf"]

GLUE = ["§;§", "§&&§", "§||§", "(§)|§", "{ §;}|§", "§|(§)", "if §;then §;fi", "while :;do §;break;done", "(§);§", "§>qf", "§>qf §", "§ 2>qf",
        "§ 2>&1>qf", "§&>qf", "§&", "[[ a ]]&&§", "[[ a ]]||§", "((1))&&§", "((1));§", "§ <(§)<(§)", "case a in a)§;;esac", "case a in a) §;esac",
        "§ 2>&1|§", "§|&§", "§ >&2|§", "{ §;} >qf", "( § )>qf", "§>&2", "§<qin", "§ 0<qin", "§;;", "§ ;&", "§|", "{ §; }&&§", "for qi in 1;do §;done", "! §",
        "!§", "§&&", "§ &&\n§", "§ ||\n\n§", "§ |\n#c\n§", "§|\n§", "§ &\n§", "§&§", "§&\n§", "§;\n§", "§ ;\n;§", "§\n;§", "{ §\n;}", "{\n§\n}", "{§}", "{ § }",
        "(§)(§)", "(§) §", "§ (§)", "§ {", "§ }", "} §", "{ ; § }", "{ § ;; }", "§ ;;&", "§ && ! §", "§ | ! §", "§ |& ! §", "§ & ! §", "! § && !§",
        "§ | { §; }", "§ | ( § )", "§ | while :; do §; break; done", "§ | if :; then §; fi", "§ | case x in x) §;; esac", "§ | [[ x ]]", "§ | ((1))",
        "§ | time §", "§ | coproc §", "§ |\ntime §", "{ §; } | §", "§ && { §; }", "§ && ( § )", "§ || { §; } >qf", "§ & { §; }"]

DECL = ["declare -a QA=([$(§)]=1)", "declare QA[$(§)]=1", "export QA[$(§)]=1", "local -a QA=($(§))", "readonly QA=$(§) QB=$(§)",
        "declare -A QA=(['$(§)']=1)", "typeset -i QA='$(§)'", "unset 'QA[$(§)]'", "unset QA[$(§)]", "declare -p QA[$(§)]", "declare -f $(§)",
        "export -n QA=$(§)", "declare QA=$(§) >qf", "export >qf QA=$(§)", "declare -x QA=\"$(§)\"$(§)", "declare QA=(a\nb)", "declare QA=(a\n$(§)\n)",
        "declare QA=(\n# c\n$(§)\n)", "QA=(a\n$(§))", "QA=(a #c\n b)\n§", "QA+=(x $(§))", "QA=($(§) $(§))", "QA=(\"$(§)\")", "export QA=$(§)\n§", "export QA\n§ >qf",
        "export\nQA=$(§)", "export QA=$(§) QB", "declare QA=b$(§)c", "declare \"QA=$(§)\"", "declare 'QA=$(§)'", "export QA= $(§)", "export QA=$(§)$(§)",
        "declare QA=$'x'$(§)", "unset QA $(§)", "unset -v QA$(§)", "unset QA >qf", "export QA >qf $(§)", "readonly -p >qf", "local QA >qf $(§)"]

CMT = ["§ #c\n§", "§;#c\n§", "§&&#c\n§", "§ &&\n#c\n§", "§ |#c\n§", "§ | #c\n#d\n§", "§ \\\n#c\n§", "§#c\n§", "§ '#'#c\n§",
       "§ a#b\n§", "§ \"a\"#b\n§", "§ $(#c\n§\n)", "§ <<E #c\nz\nE\n§", "§ >qf#c\n§", "§ >qf #c\n§", "§ ;#c\n;§", "§\n#c\n\n#d\n§",
       "{ §; #c\n}", "( § #c\n)", "if § #c\nthen § #d\nfi", "case a in #c\na) §;; #d\nesac", "§ | #c\n§", "§ |\n#c\n§ >qf", "§ | § # c\n§ 2>&1",
       "§ | §\n# c\n§ >qf", "§ | §  # c\\\n§ >qf", "§ | §\n\n# c\n§ >qf", "§ \\#c\n§", "§ #\\\n§", "§ # c \\\n§", "§ a \\\n#c\n§", "§ ;;\n#c",
       "# c\n§", "#!/bin/bash\n§", "§ # c\n# d\n§ | §\n§ 2>&1"]

WORDCTX = ["§ @", "§ @ qa", "§ qa @", "§ >@", "§ >qf @", "QX=@ §", "§ <<< @", "§ 2>&1 @", "§ @ >qf", "§ @ @", "§ @@", "§ qa@", "§ <@", "§ >&@", "§ {qfd}>@"]
WORDS8 = ["$qv$qv", "${qv}x", "$qv/$qv.c", "x$(§)y", "$(§)$(§)", "`§``§`", "$qv\\;", "\"$qv\"\\;", "$qv\\$", "'a'$qv", "$'a'$qv", "$\"a\"$qv", "a$\"b\"",
          "~/$qv", "x=$qv/$qv", "{a,b}$qv", "$qv{a,b}", "$((1))$qv", "$((1))x", "\\$qv", "a\\\n$qv", "$qv\\\nb", "$q\\\nv", "${qv\\\n}", "$\\\nqv",
          "$qv-$qv.c", "$qv.$qv", "$qv:$qv?", "$qv=$qv*", "\"$qv\"$qv.c", "$(§)$qv.c", "`§`$qv.c", "${qv}$qv.c", "$qv$(§)-$qv.c", "'a'\\;", "$qv\\*", "\"a\"\\#c",
          "a\\ b", "a\\\tb", "a\\\"b", "\\\\$qv", "$qv\\\\", "-", "- ", "--", "-\\\n", "x -", "$'-'", "'-'", "\"-\"", "\\-"]

BR = ["[[ qa =~ ^$(§)$ ]]", "[[ qa == $(§)* ]]", "[[ ( -n $(§) ) && ! -z x ]]", "[[ $(§) < $(§) ]]", "[[ -n x ]] && §", "[[ ! -n x ]]\n§",
      "[[ -f $(§) || -d `§` ]]", "[[ $(§) ]] >qf", "[[ x ]] 2>&1 | §", "[[ x ]] <<E\nz\nE", "(( $(§) ? 1 : 2 ))", "(( x = $(§), y = 2 ))", "(( x++ )) >qf",
      "(( 1 )) | §", "(( $(§) )) <qin", "[[\nx\n]]", "[[ x\n]]", "[[ x && \n y ]]", "[[ x == y ]] ; §", "[[ $(§ ;) ]]", "[[ x =~ ($(§)) ]]", "[[ x =~ 'a b' ]]",
      "[[ x == [$(§)]* ]]", "[[ $'x' == x ]]", "[[ x == \"$(§)\" ]]", "[[ -z \"${x:-$(§)}\" ]]", "[[ x =~ a\\ b ]]", "[[ x =~ a|b ]]", "[[ x =~ a\\|b ]]",
      "[[ x == x ]]\n[[ y == y ]]\n§", "(( 1 )); §", "(( x[$(§)] ))", "(( \"$(§)\" ))", "(( `§` ))", "((\n1\n))", "(( 1\n))", "(( a \\\n+ 1 ))", "[[ a \\\n== a ]]"]

CMPD = ["{ §; } >qf §", "( § ) >qf", "{ §; } >qf 2>&1", "{ §; } <<E\nz\nE", "if §; then §; fi >qf", "for qi in 1; do §; done >qf", "while §; do break; done >qf",
        "case a in a) §;; esac >qf", "{ §; } >qf | §", "( § ) 2>&1 | §", "{ §; } 2>&1 &", "{ §; } >qf <<E\nz\nE", "{ §; } <<E >qf\nz\nE", "( § ) <<E\n$(§)\nE",
        "{ §; } >qf >qg", "{ §; } >$(§)", "{ §; } > >(§)", "{ §; } 2>qf 3>qg", "{ § ; } \\\n>qf", "{ §; }\n>qf", "{ §; } >qf\n§", "{ §; } &>qf", "{ §; } &", "{ §; } &&  §",
        "if §; then §; fi 2>&1 | §", "if § >qf; then §; fi", "if §; then § >qg; else §; fi >qf", "while § >qf; do break; done", "until §; do break; done 2>&1",
        "case $(§) in a) §;; esac <<E\nz\nE", "for ((i=0;i<1;i++)); do § >qf; done >qg", "select qs in 1; do §; break; done >qf", "{ § >qf; } >qg",
        "f() { § >qf; } >qg; f", "f() ( § ) <qin; f", "f() { §; } >qf 2>&1; f >qg", "f() { §; }; f >qf", "f() { §; }; f | §", "function f { §; } >qf; f"]

REDIRS = ["§ >qf{a,b}", "§ >{qf,qg}", "§ >q*", "§ >~/qf", "§ > $HOME/qf", "§ >${qv:-qf}", "§ >$(§)", "§ >`§`", "§ >\"$(§)\"", "§ >&$qv", "§ >&${qv}",
          "§ 2>&$(§)", "§ >&2$qv", "§ >&2x", "§ >&'2'", "§ >&-x", "§ 3>&-", "§ <&$qv", "§ >&\"1\"", "§ >& 1", "§ >&\\2", "§ 2>& 1", "§ >&1-", "§ >&2-",
          "§ >&qf", "§ >& qf", "§ &>& qf", "§ 2>&qf", "§ >&2 qf", "§ >&- qf", "§ <&- qf", "§ <&0 qf", "§ <& qf", "§ >&'$(§)'", "§ >&\"$(§)\"",
          "§ >&1 2>&1", "§ >qf >&2", "§ >&2 >qf", "§ 1>&2 2>qf", "§ 2>qf 1>&2", "§ >|qf", "§ >| qf", "§ >|$(§)", "§ <>qf", "§ 3<>qf", "§ <>$(§)",
          "§ {qfd}>qf", "§ {qfd}>&2", "§ {qfd}<&-", "§ {qfd}>&-", "§ {qf d}>qf", "§ {1}>qf", "§ {qfd}>>qf", "§ {qfd}<<<q", "§ 12>qf", "§ 9999999999>qf",
          "§ 1>qf 2>qf", "§ >qf>qg", "§ >qf>>qg", "§ >qf<qin", "§ >qf&>qg", "§ &>qf>qg", "§ &>>qf", "§ &> qf", "§ >> qf", "§ 2>> qf", "§ 2>>qf",
          "exec >qf", "exec {qfd}>qf", "exec 3>qf; § >&3", "exec 3>&1", "exec <qin", ">qf", ">qf >qg", "> qf", ">>qf", "2>qf", "<qin", "<qin >qf", "> $(§)", ">$(§)"]

EVALS = ["eval '§'", "eval § a", "eval \"§\" \"§\"", "eval -- §", "eval '§; §'", "eval '§ >qf'", "eval '§ | §'", "eval '§' '§'", "eval §\\ a", "eval \"$(§)\"", "eval '$(§)'",
         "trap '§' EXIT", "trap -- '§' EXIT", "trap '§; §' EXIT", "trap '§' 0", "trap \"§\" EXIT", "trap '§ >qf' EXIT", "trap '' EXIT; §", "trap -- § EXIT", "trap '§' EXIT ERR",
         "trap §\\ a EXIT", "trap '§' DEBUG", "trap '§' ERR; false", "eval 'eval §'", "eval \"eval '§'\"", "eval 'trap § EXIT'", "command eval '§'", "builtin eval '§'",
         "eval '§\n§'", "eval $'§\\n§'", "eval '(§)'", "eval '{ §; }'", "eval \"echo hi; §\"", "eval 'if :; then §; fi'", "eval ':' '&&' §", "eval ':; §' >qf", "eval '§' <<E\nz\nE",
         "eval\n§", "eval ''", "eval $'\\x{4d}1'", "eval 'a=1 §'", "eval 'a=(1) §'", "eval '$(§) x'", "eval \\§", "eval '\\§'", "eval \"\\\\§\""]


SEPS = ["", " ", "  ", "\n", "\\\n", " \\\n", "\t", ";", " ; ", "\n\n", "\r\n", " #c\n", "\\\n\\\n", " && ", " || ", " | ", " & ", ";;", "\\\n ", " \\\n\\\n", "&", "|", "||", "&&",
        "\n#c\n", " \\\n#c\n", " |\n", " &&\n", ";\n", "\\\n\n", "\n\\\n", "\n ", "\t\n", " \r\n", "$", "\\", "'", '"', "`", "(", ")", "{", "}", "!", " !", "! "]


def _g8_fill(g, tpl, sub=None):
    out = []
    for ch in tpl:
        if ch == "§":
            out.append(g.m())
        elif ch == "¤":
            out.append(g.simple())
        elif ch == "@" and sub is not None:
            out.append(sub)
        else:
            out.append(ch)
    return "".join(out)


def _g8_extra(g):
    r = g.rng
    k = r.randint(0, 17)
    if k == 17:
        return _g8_fill(g, r.choice(EVALS))
    if k == 0:
        return _g8_fill(g, r.choice(SUBCTX), _g8_fill(g, r.choice(SUBS)))
    if k == 1:
        t = r.choice([";;", ";&", ";;&", "\n;;", " ;;"])
        return _g8_fill(g, "case %s in %s) ¤%s\nesac" % (r.choice(["qa", "$(§)", "'x'", "\"$qv\""]), _g8_fill(g, r.choice(PATS)), t))
    if k == 2:
        return _g8_fill(g, r.choice(LOOPS), _g8_fill(g, r.choice(ITEMS)))
    if k == 3:
        return _g8_fill(g, r.choice(FNHEAD) + " " + r.choice(FNBODY) + r.choice(POST) + r.choice([";", "\n", " ;\n"]) + "qfn")
    if k == 4:
        return _g8_fill(g, r.choice(HD))
    if k == 5:
        out = g.simple()
        for _ in range(r.randint(1, 4)):
            out += r.choice([" |& ", " & ", " && ", " || ", " | ", " ; ", "\n", " &\n", " |\n", " |\n\n", " | \\\n", " &&\\\n"]) + r.choice([g.simple(), g.simple(), "! " + g.simple(), "time " + g.simple()])
        return out
    if k == 6:
        return _g8_fill(g, r.choice(PFX))
    if k == 7:
        return _g8_fill(g, r.choice(GLUE))
    if k == 8:
        t = r.choice(["> >(§)", "2> >(§)", "&> >(§)", "< <(§)", "<<< <(§)", ">> >(§)", "{qfd}> >(§)", ">(§)", "<(§)", "> >(§) ¤", "2>&1 > >(§)", "<>(§)", ">&>(§)", "&>>(§)",
                      "> >(§) > >(§)", ">(§) >(§)", "> >(§ | §)", "< <(§ >qf)", "> >(§ <<E\nz\nE\n)"])
        return _g8_fill(g, "§ " + t)
    if k == 9:
        n = r.choice([200, 9000, 9000, 20000])
        return g.m() + " " + r.choice(["q", "é", "a\\ ", "$'x'", "\"b\"", "$qv", "\\\n"]) * n + " " + g.simple()
    if k == 10:
        return _g8_fill(g, r.choice(DECL))
    if k == 11:
        return _g8_fill(g, r.choice(CMT))
    if k == 12:
        return _g8_fill(g, r.choice(WORDCTX), _g8_fill(g, r.choice(WORDS8)))
    if k == 13:
        sep = r.choice(["\r\n", "\t", "\x0c", "\xa0", "\ufeff", "\u2028", "\x85", "\u200b", "\x1f", "\x7f", "\r", "\x0b", "\u00ad", "\u3000"])
        return g.m() + sep + g.m() + r.choice(["", sep + g.simple(), "\n" + g.simple()])
    if k == 14:
        return _g8_fill(g, r.choice(BR))
    if k == 15:
        return _g8_fill(g, r.choice(CMPD))
    return _g8_fill(g, r.choice(REDIRS))


_GAP = re.compile(r"[ \t]+|\n+|;;|;|&&|\|\||\|&|\||&")


def _g8_boundary(rng, s):
    """Replace 1-3 operator or white-space runs by another separator (or none)."""
    for _ in range(rng.choice([1, 1, 2, 3])):
        gaps = list(_GAP.finditer(s))
        if not gaps:
            return s
        m = rng.choice(gaps)
        s = s[:m.start()] + rng.choice(SEPS) + s[m.end():]
    return s


_CONT = ["\\\n", "\\\n ", " \\\n", "\\\n\\\n", "\\\n\n", "\\\n\t", "\\\r\n", "\\\n#c\n", "\\ \n", "\\\n\\\n "]
_TOK = re.compile(r"[=$(){}<>\[\]'\"`\\|&;!#~*?]|[ \t]+|\n")


def _g8_contmut(rng, s):
    """Insert 1-3 backslash-newline variants at token boundaries (and now and then anywhere)."""
    for _ in range(rng.choice([1, 1, 2, 3])):
        if rng.random() < 0.8:
            pts = [m.start() for m in _TOK.finditer(s)] + [m.end() for m in _TOK.finditer(s)]
        else:
            pts = list(range(len(s) + 1))
        if not pts:
            return s
        i = rng.choice(pts)
        s = s[:i] + rng.choice(_CONT) + s[i:]
    return s


class Gen:
    def __init__(self, rng):
        self.rng, self.n = rng, 0

    def m(self):
        self.n += 1
        r = self.rng.random()
        n = f"M{self.n}"
        if r < 0.04:
            return f"'{n}'"
        if r < 0.07:
            return f'"{n}"'
        if r < 0.09:
            return f"\\{n}"
        if r < 0.10:
            return f"M''{self.n}"
        if r < 0.11:
            return f'$"{n}"'
        if r < 0.12:
            return f"$'{n}'"
        return n

    def ws(self):
        r = self.rng.random()
        if r < 0.80:
            return " "
        if r < 0.86:
            return "  "
        if r < 0.90:
            return "\t"
        if r < 0.95:
            return " \\\n"
        if r < 0.97:
            return " \\\n  "
        return " "

    def word(self):
        r = self.rng
        x = r.random()
        if x < 0.05:
            return f"$({self.m()})"
        if x < 0.07:
            return f"`{self.m()}`"
        if x < 0.08:
            return f'"x$({self.m()})y"'
        if x < 0.09:
            return f"<({self.m()})"
        if x < 0.10:
            return f"$(( {r.randint(1, 5)} + 1 ))"
        if x < 0.11:
            return f"${{qv:-$({self.m()})}}"
        if x < 0.12:
            return f'"${{qv:-{r.choice(WORDS[:12])}}}"'
        if x < 0.15:
            return r.choice(WORDS) + r.choice(WORDS)
        return r.choice(WORDS)

    def red(self):
        r = self.rng
        return r.choice(OUTR if r.random() < 0.7 else INR)

    def simple(self, allow_red=True):
        r = self.rng
        parts = []
        if r.random() < 0.12:
            parts.append(r.choice(["qx=1", "QA=$(%s)" % self.m(), "QB=qc", "QC=", "QD+=x", "QE=(a b)", "QF[2]=z", "QG='a b'"]))
        if r.random() < 0.06 and allow_red:
            parts.append(self.red())
        parts.append(self.m())
        for _ in range(r.randint(0, 4)):
            parts.append(self.red() if (allow_red and r.random() < 0.25) else self.word())
        return self.ws().join(parts)

    def cond(self):
        r = self.rng
        k = r.randint(0, 6)
        if k == 0:
            return f"[[ qa == qa && $({self.m()}) ]]"
        if k == 1:
            return f"[[ -z $({self.m()}) || qa =~ q ]]"
        if k == 2:
            return f"(( $({self.m()}) + 1 ))"
        if k == 3:
            return f"! {self.simple()}"
        if k == 4:
            return f"{self.simple()} && {self.simple()}"
        if k == 5:
            return f"{self.simple()} | {self.simple()}"
        return self.simple()

    def compound(self, depth):
        r = self.rng
        k = r.randint(0, 24)
        body = lambda: self.seq(depth + 1, 1, 3)  # noqa: E731
        post = r.choice(["", "", "", " >qf", " 2>&1", " <qin", " >qg 2>qh", " <<EOF\nz\nEOF"])
        sep = r.choice(["; ", "\n", " ;\n", ";\n\n"])
        if k == 0:
            return "{ %s; }%s" % (body(), post)
        if k == 1:
            return "( %s )%s" % (body(), post)
        if k == 2:
            return "if %s; then %s; fi%s" % (self.cond(), body(), post)
        if k == 3:
            return "if %s; then%s%s%selif %s; then %s%selse %s%sfi%s" % (self.cond(), sep, body(), sep, self.cond(), body(), sep, body(), sep, post)
        if k == 4:
            return "for qi in 1 2 $(%s); do%s%s%sdone%s" % (self.m(), sep, body(), sep, post)
        if k == 5:
            pats = r.choice(["qa) %s ;; *) %s ;;", "qa) %s ;& *) %s ;;", "qa) %s ;;& *) %s ;;", "(qa|qb) %s ;; (*) %s", "qa) %s;; qb) %s;;"])
            return "case %s in%s%s%sesac%s" % (r.choice(["qa", "$(%s)" % self.m(), "'q'", '"$qv"']), sep, pats % (body(), body()), sep, post)
        if k == 6:
            return "while %s; do%s%s; break;%sdone%s" % (self.cond(), sep, body(), sep, post)
        if k == 7:
            return "[[ qa == qa ]] && %s" % self.simple()
        if k == 8:
            return "qfn() { %s; }%s qfn" % (body(), r.choice([";", "\n", ";\n"]))
        if k == 9:
            return "$(%s)" % body()
        if k == 10:
            return "function qfn%s { %s; }%s; qfn" % (r.choice(["", "()", " ()"]), body(), post)
        if k == 11:
            return "qfn() ( %s )%s; qfn" % (body(), post)
        if k == 12:
            return "until %s; do%s%s; break;%sdone" % (self.cond(), sep, body(), sep)
        if k == 13:
            return "for ((qi=0; qi<2; qi++)); do%s%s%sdone%s" % (sep, body(), sep, post)
        if k == 14:
            return "for qi in 1 2; { %s; }" % body()
        if k == 15:
            return "time %s" % r.choice([self.simple(), "{ %s; }" % body(), "-p %s" % self.simple(), "%s | %s" % (self.simple(), self.simple())])
        if k == 16:
            return "! %s" % r.choice([self.simple(), "( %s )" % body(), "{ %s; }" % body()])
        if k == 17:
            return "coproc %s" % r.choice([self.simple(), "QCP { %s; }" % body(), "QCP %s" % self.simple()])
        if k == 18:
            return "%s %s %s" % (self.simple(), r.choice(["|&", "|", "&&", "||", ";", "&"]), self.simple())
        if k == 19:
            return "(( 1 + $(%s) ))%s" % (self.m(), post)
        if k == 20:
            return "{ %s; } %s" % (body(), r.choice([">qf", "2>&1 | " + self.simple(), "<qin", "&"]))
        if k == 21:
            return "(%s)%s" % (body(), post)
        if k == 22:
            return "select qs in a; do %s; break; done" % body()
        if k == 23:
            return "case $(%s) in\n%s)\n%s\n;;\nesac" % (self.m(), r.choice(["a", "*", "a|b"]), body())
        return "{ %s\n}" % body()

    def declare(self):
        r = self.rng
        kw = r.choice(["export", "declare", "local", "readonly", "typeset", "declare -a", "declare -x", "export -n", "unset", "export -f"])
        items = [r.choice(["QX=$(%s)" % self.m(), "QY=1", "QZ", "QA=(a $(%s))" % self.m(), "'QQ'=1", "QW='a b'", "-QV", "QU=\"$(%s)\"" % self.m(),
                           "QT[$(%s)]=1" % self.m()]) for _ in range(r.randint(1, 3))]
        if kw == "unset":
            items = [r.choice(["QX", "QY", "qa"]) for _ in items]
        return f"{kw} " + " ".join(items) + r.choice(["", "", " >qf", " 2>&1"]) + ("; " + self.simple() if r.random() < 0.5 else "")

    def heredoc(self):
        r = self.rng
        delim = r.choice(["E", "'E'", '"E"', "\\E", "-E", "-'E'", "E'x'", "'E'\"F\"", "EOF", "-EOF", "E1"])
        dash = delim.startswith("-")
        raw = delim.lstrip("-")
        word = raw.replace("'", "").replace('"', "").replace("\\", "")
        lead = "<<-" if dash else "<<"
        bodies = [
            "qa", "$(%s)" % self.m(), "`%s`" % self.m(), "\\\n%s" % self.m(), "\t%s" % self.m(), "%s" % self.m(),
            "x \\\ny", "\\$(%s)" % self.m(), "a\\\n$(%s)" % self.m(), "\t$(%s)" % self.m(), "${qv:-$(%s)}" % self.m(),
            "$qv`%s`" % self.m(), "'$(%s)'" % self.m(), '"$(%s)"' % self.m(), "x\n\n\ny", "", "\\\\\n%s" % self.m(),
            "a\n%s\nb" % word, " %s" % word, "%s x" % word, "%s;%s" % (word, self.m()), "$((1+$(%s)))" % self.m(),
            "$( %s\n )" % self.m(), "\\`%s\\`" % self.m(), "$'x'", "# %s" % self.m(),
        ]
        end = r.choice([word] * 5 + ["\t" + word, " " + word, word + " ", word + ";" + self.m(), word + "&&" + self.m(),
                                      word + ")", word + ">qf", "\t\t" + word, word + "\r", word + "x"])
        tail = r.choice(["", "", "", " >qf", " | " + self.simple(), " && " + self.simple(), " 2>&1", " " + self.word(), " ; " + self.simple(), " & ",
                         " <<F2\nz\nF2", " > qf", " </dev/null"])
        first = self.m() + (" " + self.word() if r.random() < 0.3 else "")
        s = f"{first} {lead}{delim}{tail}\n{r.choice(bodies)}\n{end}"
        if r.random() < 0.15:
            second = r.choice(["F", "'F'"])
            s = f"{first} {lead}{delim} {lead}{second}{tail}\n{r.choice(bodies)}\n{word}\nq\nF"
        if r.random() < 0.1:
            s = "$(" + s + "\n)"
        if r.random() < 0.1:
            s = "{ " + s + "\n}"
        return s

    xp = 0.0

    def stmt(self, depth):
        r = self.rng
        if self.xp and r.random() < self.xp:
            return _g8_extra(self)
        x = r.random()
        if x < 0.30:
            s = self.simple()
        elif x < 0.50 and depth < 3:
            s = self.compound(depth)
        elif x < 0.60:
            s = self.heredoc()
        elif x < 0.68:
            s = r.choice([" | ", " |& ", " |\n", " |\n\n", " | \\\n"]).join(self.simple() for _ in range(r.randint(2, 3)))
            if r.random() < 0.4:
                s += r.choice(["\n" + self.simple() + " 2>&1", "\n" + self.simple() + " >qf", " >qf", "\n" + self.simple()])
        elif x < 0.72:
            s = self.simple() + " " + self.red() + " " + r.choice(["qa", "$qv", "$", "qa$qv.txt", "-qrf qx", "'q r' qz", '"a"b qw', "qa\\\n qb"])
        elif x < 0.76:
            s = self.declare()
        elif x < 0.80:
            s = "# " + self.m() + r.choice(["", " `", " $(", " '", " \\"])
            s += "\n" + self.simple()
        elif x < 0.84:
            s = self.simple() + " \\\n " + self.simple()
        elif x < 0.88:
            s = r.choice(["qx=1 qy=2", "qx=1", "QA=(a b)", "QA=$(%s)" % self.m(), "QB+=1", "QC[1]=x", "1=2", "qx=1 1=2", "qx=1 >qf"]) + r.choice(["", " ", "\n"]) + self.simple()
        elif x < 0.91:
            s = self.simple() + r.choice([" #c", " #c\n", "#c", " ;#c\n", "\n#c\n", "\n  # c\n", " \\\n#c\n"]) + self.simple()
        elif x < 0.94:
            s = r.choice(["\\\n", "", " ", "\t", "\\\n\\\n"]) + " " + self.simple()
        elif x < 0.97:
            s = self.simple() + " " + r.choice(["<<<", ">", ">>", "<"]) + r.choice(["$(%s)" % self.m(), "<(%s)" % self.m(), "qa$(%s)" % self.m(), "'$(%s)'" % self.m()]) + " " + self.word()
        else:
            s = r.choice(["!", "time", "time -p", "coproc", "! !"]) + " " + self.simple()
        return s

    def seq(self, depth, lo=1, hi=5):
        r = self.rng
        out = self.stmt(depth)
        for _ in range(r.randint(lo, hi) - 1):
            out += r.choice(["\n", "; ", " && ", " || ", " & ", "\n\n", "\n\\\n", ";\n", " &\n", ";", "&", "\n#c\n", "  ;  ", " |\n", "\r\n", " ;; "]) + self.stmt(depth)
        return out


FRAGS = ["\\\n", "\n", "\r", "\r\n", "\x0b", "\x0c", "﻿", "#", " # x\n", ";;", "&", "|", "`", "$(", ")", "'", '"', "\\", "<<E", "{", "}", "((",
         "))", "$", "${", "!", " ", "\t", "<(", ">(", "\\\n\\\n", ";", "&&", "||", "|&", "$'", "$\"", "[[", "]]", "<<-", "<<<", ">&", "\x00", "\x7f", " ",
         " ", "=", "+=", "==", "=~", "case", "esac", "in", ";&", ";;&", "coproc", "function", "then", "fi", "done", "{ ", " }"]


def mutate(rng, s):
    for _ in range(rng.choice([1, 1, 1, 2, 3])):
        if not s:
            return s
        i = rng.randrange(len(s) + 1)
        k = rng.random()
        if k < 0.7:
            s = s[:i] + rng.choice(FRAGS) + s[i:]
        elif k < 0.85:
            s = s[:i] + s[i + rng.randint(1, 3):]
        else:
            j = rng.randrange(len(s) + 1)
            s = s[:i] + s[j:j + rng.randint(1, 6)] + s[i:]
    return s


DEFS = "".join(f'''M{i}() {{ printf "%s\\0" "M{i}" "$@" $'\\1' >> "$QLOG"; }}\n''' for i in range(1, 80))


def run_bash(script):
    with tempfile.TemporaryDirectory(prefix="bashtree-r7-", ignore_cleanup_errors=True) as tmp:
        work = os.path.join(tmp, "work")
        os.mkdir(work)
        log = os.path.join(tmp, "log")
        Path(work, "qin").write_text("in\n")
        before = set(os.listdir(work))
        proc = subprocess.Popen([BASH, "--norc", "--noprofile", "-c", DEFS + script.replace("\x00", "")], cwd=work,
                                env={"PATH": "/nonexistent", "HOME": tmp, "QLOG": log, "TMPDIR": tmp, "LC_ALL": "C.UTF-8"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)       # its own process group, so all of it can be killed
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            return None
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)           # always: a script may leave background jobs behind
            except ProcessLookupError:
                pass
            proc.communicate()
        run = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        calls = []
        if os.path.exists(log):
            for rec in open(log, "rb").read().split(b"\x01\0"):
                if rec:
                    calls.append([a.decode("utf-8", "surrogateescape") for a in rec.split(b"\0")[:-1] if True])
        created = sorted(set(os.listdir(work)) - before)
        return calls, created, run.returncode, run.stderr


def check(script):
    """-> list of problem strings (only those that repeat on a second bash run)."""
    res = parse(script)
    if res.status != OK:
        return None
    p1 = check1(res, script)
    if p1:
        p2 = check1(res, script)
        return [x for x in (p1 or []) if x in (p2 or [])]
    return p1


def _subcommands(cmds, depth=0):
    out = []
    for c in cmds:
        if c.words and depth < 3:
            for n in nested(list(unwrap([w.text for w in c.words]).words)):
                if n.kind == "text" and n.via in ("eval", "trap"):
                    sub = parse(n.text)
                    if sub.status == OK:
                        out += list(sub.commands) + _subcommands(sub.commands, depth + 1)
                    else:
                        out.append(None)                # the consumer gets a refusal for this text: nothing is hidden
    return out


def check1(res, script):
    out = run_bash(script)
    if out is None:
        return None
    calls, created, rc, err = out
    probs = []
    cmds = res.commands
    names = {}
    view = {}
    for c in cmds:
        if c.words:
            u = list(unwrap([w.text for w in c.words]).words)
            view[id(c)] = u
            if u:
                names.setdefault(u[0], []).append(c)
    subs = _subcommands(cmds)                           # round 8: text a command runs in turn (eval, trap) counts as parsed
    refused_text = None in subs
    subs = [x for x in subs if x is not None]
    for sc in subs:
        if sc.words:
            su = list(unwrap([w.text for w in sc.words]).words)
            view[id(sc)] = su
            if su:
                names.setdefault(su[0], []).append(sc)
    cmds = list(cmds) + subs
    ran = {}
    for call in calls:
        if not re.fullmatch(r"M\d+", call[0]) or any(re.search(r"M\d", a) and a not in script for a in call[1:]):
            continue   # a torn log record from two racing writers (harness artifact, seen on `M1&M2 ...` shapes)
        ran.setdefault(call[0], set()).add(tuple(call[1:]))
    # round 8 invariants: a redirect's fd is a number or {name}; a word's raw text is what the source says at its span
    sb = script.encode("utf-8", "surrogatepass")
    for c in res.commands:
        for r in list(c.redirects) + [r for a in c.ancestors for r in a.redirects]:
            if r.fd is not None and not re.fullmatch(r"[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\}", r.fd):
                probs.append(f"fd format {r.fd!r}")
        for w in list(c.words) + [r.target for r in c.redirects if r.target is not None]:
            if sb[w.span[0]:w.span[1]].decode("utf-8", "replace") != w.raw and not (b"\n" in sb[w.span[0]:w.span[1]] or b";" in w.raw.encode()):
                probs.append(f"span of {w.raw!r}")
    nonlit_name = refused_text or any(c.words and any(not w.literal for w in c.words[:4]) for c in cmds)   # round 8: a wrapper (`time M1$x`) hides the name one word later
    # round 8: a nested() report of "unknown" (subscript builtins, eval-like) is the library saying it can't know
    if any(n.kind == "unknown" for c in cmds if c.words for n in nested(list(unwrap([w.text for w in c.words]).words))):
        nonlit_name = True
    for mk, argvs in ran.items():
        if mk not in names:
            if not nonlit_name:
                probs.append(f"hidden {mk}")
            continue
        cs = [c for c in names[mk] if c.words]
        if len(cs) == 1 and len(names[mk]) == 1 and not nonlit_name:
            u = view[id(cs[0])]
            off = len(cs[0].words) - len(u)
            want, full = [], True
            for j in range(1, len(u)):                  # round 8: compare the literal prefix, not only all-literal commands
                w = cs[0].words[off + j]
                if not w.literal:
                    full = False
                    break
                want.append(w.text)
            for argv in argvs:
                if (argv != tuple(want)) if full else (argv[:len(want)] != tuple(want)):
                    probs.append(f"words {mk}: bash {argv!r} parse {tuple(want)!r}{'' if full else ' (prefix)'}")
    targets = set()
    nonlit = refused_text
    for c in cmds:
        for r in c.redirects:
            if r.target is not None:
                if r.target.literal:
                    targets.add(r.target.text)
                else:
                    nonlit = True
        for a in c.ancestors:
            for r in a.redirects:
                if r.target is not None:
                    if r.target.literal:
                        targets.add(r.target.text)
                    else:
                        nonlit = True
    if not nonlit:
        for f in created:
            if f not in targets:
                probs.append(f"redir file {f!r} created by bash, not a parsed target {sorted(targets)!r}")
    return probs


def worker(args):
    seed, count, mode = args
    rng = random.Random(seed)
    bad, checked = [], 0
    for _ in range(count):
        g = Gen(rng)
        if mode.startswith("x"):                      # round 8: _g8_extra statements mixed in
            g.xp = 0.6
        script = g.seq(0, 1, 2) if mode.startswith("x") else g.seq(0)
        if mode in ("mut", "xmut") or (mode in ("both", "xboth") and rng.random() < 0.5):
            script = mutate(rng, script)
        if mode == "xcont":                           # round 8: backslash-newline variants at token boundaries
            script = _g8_contmut(rng, script)
        if mode == "xbnd" or (mode == "xboth" and rng.random() < 0.5):
            script = _g8_boundary(rng, script)
            if rng.random() < 0.2:
                script = mutate(rng, script)
        if "M1" not in script and "M2" not in script:
            continue
        try:
            p = check(script)
        except Exception as exc:  # noqa: BLE001
            bad.append((script, [f"CRASH {type(exc).__name__}: {exc}"]))
            continue
        if p is None:
            continue
        checked += 1
        if p:
            bad.append((script, p))
    return checked, bad


def worker_list(scripts):
    bad, checked = [], 0
    for script in scripts:
        if os.environ.get("SHOW"):
            res, out = parse(script), run_bash(script)
            names = [[c.kind, [w.text for w in c.words]] for c in res.commands] if res.status == OK else res.reason
            print(repr(script), "| parse:", res.status, names, "| bash calls:", out and out[0], "files:", out and out[1], file=open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "show.out"), "a"), flush=True)
        try:
            p = check(script)
        except Exception as exc:  # noqa: BLE001
            bad.append((script, [f"CRASH {type(exc).__name__}: {exc}"]))
            continue
        if p is None:
            bad.append((script, ["NOT-OK-OR-TIMEOUT (informational)"])) if os.environ.get("SHOWSKIP") else None
            continue
        checked += 1
        if p:
            bad.append((script, p))
    return checked, bad


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    mode = sys.argv[3] if len(sys.argv) > 3 else "gen"
    if os.environ.get("SAFE_RUN") != "1":
        sys.exit("refusing to run: use bench/sweep.sh (it wraps bench/safe-run.sh, which caps processes and time)")
    jobs = min(int(os.environ.get("JOBS", "4")), 6)   # a hard cap: never flood the machine
    per = count // jobs
    with mp.Pool(jobs) as pool:
        if mode == "list":                # hand-written scripts in bench/listfile.py (S = [...]), marker functions only
            ns = {}
            exec(open(Path(__file__).resolve().parent / "listfile.py").read(), ns)
            S = ns["S"]
            results = pool.map(worker_list, [S[j::jobs] for j in range(jobs)])
        else:
            results = pool.map(worker, [(seed * 1000 + j, per, mode) for j in range(jobs)])
    checked = sum(r[0] for r in results)
    bad = [b for r in results for b in r[1]]
    print(f"seed {seed} mode {mode}: {checked} ok scripts checked, {len(bad)} problems")
    kinds = {}
    for s, p in bad:
        for x in p:
            kinds[x.split()[0]] = kinds.get(x.split()[0], 0) + 1
    print(kinds)
    shown = set()
    for s, p in sorted(bad, key=lambda b: len(b[0])):
        key = p[0][:12]
        if key in shown and len(shown) > 6:
            continue
        shown.add(key)
        print("---", p[:3], "\n" + repr(s))
        if len(shown) > 14:
            break
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    os.environ.pop("PYTHONPATH", None)
    main()
