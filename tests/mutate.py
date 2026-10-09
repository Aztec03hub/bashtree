"""Mutation check: remove each guard from a temporary copy of the code, and
the tests must then fail. A guard whose removal still passes is not protected
by any test.

    python tests/mutate.py      (about a minute; mutants run in parallel)
"""
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
C, H = "core.py", "helpers.py"
# name -> [(file, old text, new text)]
MUTANTS = {
    # parsing
    "syntax errors parsed": [(C, "    if tree.root_node.has_error:\n        return Result(SYNTAX_ERROR", "    if False:\n        return Result(SYNTAX_ERROR")],
    "no heredoc cap": [(C, "    if text.count(\"<<\") > MAX_HEREDOCS:", "    if False:")],
    "no array cap": [(C, "    if text.count(\"=(\") > MAX_ARRAYS:", "    if False:")],
    "escapes in \"...\" kept": [(C, "        return _unescape(raw[1:-1], '$`\"\\\\\\n')", "        return raw[1:-1]")],
    "string lines glued": [(C, "        return _unescape(raw[1:-1], '$`\"\\\\\\n')",
                            "        return \"\".join(_text(c, src) for c in node.children if c.type != '\"')")],
    "escapes in words kept": [(C, '        return _unescape(raw, "")', "        return raw")],
    "'...' quotes kept": [(C, '    if t == "raw_string":\n        return raw[1:-1]', '    if t == "raw_string":\n        return raw')],
    "$'...' undecoded": [(C, "        return _ansi_c(raw[2:-1])", "        return raw[2:-1]")],
    "\\x as a character": [(C, '            out.append(int(s[1:].strip("{}") or "0", 16) & 0xFF)', '            out += chr(int(s[1:].strip("{}") or "0", 16)).encode()')],
    "globs are literal": [(C, 'if n.type == "word" and _UNQUOTED_SPECIAL.search(_raw(n, src)):', "if False:")],
    "expansions literal": [(C, "        if n.type in _EXPANDING:\n            return False", "        if False:\n            return False")],
    "heredoc quoting ignored": [(C, "quoted = any(ch in delim for ch in \"'\\\"\\\\\")", "quoted = False")],
    "nested trusts any path": [(H, "    if prog in _NESTERS and not _trusted(words[0]):", "    if False:")],
    # structure
    "newline not an op": [(C, 'ops.append("\\n" if b"\\n" in src[lo:hi] else None)', "ops.append(None)")],
    "prefix redirects lost": [(C, "            elif c.type in _REDIRECTS:\n                reds += _redirects_of([c], src)", "            elif False:\n                pass")],
    # structure (the top-down walk)
    "function bodies unmarked": [(C, '        return Ancestor("function_body", span, redirects=extra["fn_reds"])', "        return None")],
    "function redirects dropped": [(C, 'return Ancestor("function_body", span, redirects=extra["fn_reds"])', 'return Ancestor("function_body", span)')],
    "list sides swapped": [(C, "ops = (st.ops[0], list_op) if left else (list_op, st.ops[1])", "ops = (list_op, st.ops[1]) if left else (st.ops[0], list_op)")],
    "& only direct": [(C, "bg, neg, c_reds = st.bg or el_bg, False, ()", "bg, neg, c_reds = el_bg, False, ()")],
    "! only on stage 0": [(C, 'pipe_neg = t == "pipeline" and bool(named) and named[0].type == "negated_command"', "pipe_neg = False")],
    "compound redirects dropped": [(C, "redirects=_in_order(reds), background=st.el_bg, negated=st.neg),)", "redirects=(), background=st.el_bg, negated=st.neg),)")],
    "pipe redirect on every stage": [(C, "                    if c.id == last.id:\n                        c_reds = reds", "                    c_reds = reds")],
    "redirect-only lost": [(C, "                out.append(_redirect_only(n, st, st.reds + own))", "                pass")],
    "(( )) redirect lost": [(C, 'arith = t == "compound_statement" and bool(kids) and kids[0].type == "(("', "arith = False")],
    "heredoc $( ) unlabelled": [(C, '"heredoc_substitution" if st.ptype == "heredoc_body" else', '"command_substitution" if st.ptype == "heredoc_body" else')],
    "elif is if": [(C, '            return Ancestor("elif_condition", span)', '            return Ancestor("if_condition", span)')],
    "if parts merged": [(C, 'return Ancestor("if_condition" if before_then else "if_body", span)', 'return Ancestor("if_body", span)')],
    "case pattern is body": [(C, 'Ancestor("case_body" if extra["index"] > extra["paren"] else "case_pattern", span)', 'Ancestor("case_body", span)')],
    "standalone X=1 lost": [(C, '    return n.type == "variable_assignment" and ptype not in (', "    return False and ptype not in (")],
    "numeric name assigns": [(C, "            if words or not _NAME.match(a.name):", "            if words:")],
    "brace not expanding": [(C, '              "arithmetic_expansion", "brace_expression"}', '              "arithmetic_expansion"}')],
    "~ after = literal": [(C, "[*?\\[{]|(?:^|[=:])~", "[*?\\[{]|^~")],
    "no depth cap": [(C, "            if len(c_anc) > MAX_DEPTH:\n                return None", "")],
    "heredoc-nested redirects lost": [(C, "            stack.extend(c for c in r.children if c.type in _REDIRECTS)", "            pass")],
    "list redirect on whole list": [(C, "                    if not left:\n                        c_reds = reds", "                    pass")],
    "redirects out of order": [(C, "    return tuple(sorted(reds, key=lambda r: r.span[0]))", "    return tuple(reds)")],
    "no pipe cap": [(C, "    if text.count(\"|\") > MAX_PIPES:", "    if False:")],
    "no byte cap": [(C, "    if len(src) > MAX_BYTES:", "    if False:")],
    "no command cap": [(C, "            if len(out) > MAX_COMMANDS:\n                return None", "")],
    "lone $ dropped": [(C, '"variable_name", "$")', '"variable_name")')],
    "c-style for body is header": [(C, '            return Ancestor("loop_body", span)       # `for ((;;)) { a; }`', '            return Ancestor("loop_header", span)')],
    # helpers
    "find -exec missed": [(H, "            if a in FIND_EXEC:", "            if False:")],
    'reserved word accepted': [(C, '    if any(c.words and c.words[0].raw in _RESERVED for c in commands):', '    if False:')],
    'only { } reserved': [(C, '_RESERVED = {"{", "}", "then", "do", "done", "fi", "esac", "elif", "else"}', '_RESERVED = {"{", "}"}')],
    '$ "..." split in two': [(C, '        elif kept and w.raw[:1] == \'"\'', '        elif False and kept and w.raw[:1] == \'"\'')],
    'translated string raw': [(C, '    if t == "translated_string":', '    if False:')],
    'concatenated $ kept': [(C, 'if not (c.type == "$" and k + 1 < len(kids) and kids[k + 1].type == "string"))', 'if True)')],
    'NUL kept': [(C, 'return out.split(b"\\0", 1)[0].decode', 'return out.decode')],
    'no bracket cap': [(C, '    if sum(map(text.count, "()[]{}")) > MAX_BRACKETS:', '    if False:')],
    'unparsed text trusted': [(C, '    if why:\n        return Result(SYNTAX_ERROR, reason=why)', '    if False:\n        pass')],
    'backslash in backticks ok': [(C, '            if b"\\\\" in src[n.start_byte:n.end_byte]:', '            if False:')],
    'unlisted letter guessed': [(H, '                if ch not in spec.flags:\n                    return opts, operands + args[i - 1:], False', '                if False:\n                    pass')],
    'attached value read as flags': [(H, '                    val = body[k + 1:]                   # the rest of the bundle is its value', '                    val = ""')],
    'no --prefix': [(H, 'else [n for n in spec.longs if n.startswith(name)]', 'else []')],
    'su options not permuted': [(H, 'getopt(args, SU, permute=True)', 'getopt(args, SU)')],
    'queries run': [(H, '            return stop("query")', '            pass')],
    'edits run': [(H, '            return stop("edit")', '            pass')],
    'sudo -hHOST is help': [(H, 'or (name == "sudo" and ("-h", None) in opts)', 'or (name == "sudo" and "-h" in names)')],
    'env - is the program': [(H, '            if rest[:1] == ["-"]:', '            if False:')],
    'env takes only NAME=': [(H, '            while j < len(rest) and "=" in rest[j]:', '            while j < len(rest) and _ASSIGN.match(rest[j]):')],
    'timeout duration is the program': [(H, '            j = 1                                        # the duration', '            j = 0')],
    './sudo trusted': [(H, 'return name if name in WRAPPERS and _trusted(word) else None', 'return name if name in WRAPPERS else None')],
    'NAME= skipped for every wrapper': [(H, '        elif name == "sudo":\n', '        elif True:\n')],
    'shell -o value is the text': [(H, "                k += 1                               # `-oc pipefail text`: its value is the next argument, and -c still counts", "                pass")],
    'shell -c ignored': [(H, '                saw_c = True', '                saw_c = False')],
    'fish/csh/script -c ignored': [(H, 'texts = [v for o, v in opts if o in ("-c", "--command")]', 'texts = []')],
    'screen -X runs words': [(H, 'flags & {"-X", "-c"}', 'flags & {"-c"}')],
    'tmux -c ignored': [(H, 'out = [Nested("tmux", "text", text=v) for o, v in opts if o == "-c"]', 'out = []')],
    'tmux ; not split': [(H, '        if a == ";":', '        if False:')],
    'tmux prefix ignored': [(H, '    hits = hits or [n for n in TMUX_CMDS if n.startswith(sub)]', '    hits = hits')],
    'send-keys joined with spaces': [(H, '    text = "".join(typed)', '    text = " ".join(typed)')],
    'send-keys key names typed': [(H, '        elif _KEY_NAME.fullmatch(key) and len(key) > 1:', '        elif False:')],
    'send-keys -l keys mapped': [(H, '        if "-l" in flags:\n            typed.append(key)', '        if False:\n            typed.append(key)')],
    'xargs options in words': [(H, 'words=tuple(rest or ["echo"]) if ok else ())', 'words=tuple(args))')],
    'screen reattach runs (3)': [(H, '    if flags & {"-r", "-x", "-Q"}:\n        return []', '    if False:\n        return []')],
    'screen -R runs words': [(H, '    if "-R" in flags:', '    if False:')],
    'fd 0 is a word': [(C, '(_FD_VAR.match(w.raw) or (_FD_NUM.match(w.raw) and int(w.raw) <= 2147483647))', '_FD_VAR.match(w.raw)')],
    'fd variable is a word': [(C, '(_FD_VAR.match(w.raw) or (_FD_NUM.match(w.raw) and int(w.raw) <= 2147483647))', '(_FD_NUM.match(w.raw) and int(w.raw) <= 2147483647)')],
    'unicode digits are an fd': [(C, '_FD_NUM = re.compile(r"[0-9]+\\Z")', '_FD_NUM = re.compile(r"\\d+\\Z")')],
    'escaped marks count': [(C, '_QUOTED_MARK = re.compile(rb"(?<!\\\\)(?:\\\\\\\\)*(?:`|\\$\\()")', '_QUOTED_MARK = re.compile(rb"`|\\$\\(")')],
    '<( counts in quotes': [(C, 'mark = _CODE_MARK if ctx == "code" else _QUOTED_MARK', 'mark = _CODE_MARK')],
    'quoted heredoc checked': [(C, 'elif ct == "comment" or (ct == "heredoc_body" and _quoted_heredoc(c, src)):', 'elif ct == "comment":')],
    'single quotes always data': [(C, 'cc = "dqexp" if ctx == "dqexp" else "data"', 'cc = "data"')],
    'single quotes never data': [(C, 'cc = "dqexp" if ctx == "dqexp" else "data"', 'cc = ctx')],
    'dq expansion not tracked': [(C, 'cc = "dqexp" if ctx in ("dq", "dqexp") else ctx', 'cc = ctx')],
    '$( ) inherits quoting': [(C, 'cc = "code"                          # a new shell context, even inside "..."', 'cc = ctx')],
    'uncovered text unchecked': [(C, '                if mark.search(gap):', '                if False:')],
    'heredoc words accepted': [(C, 'if t == "heredoc_redirect" and any(c.type in _WORDS for c in kids):', 'if False:')],
    'NUL accepted': [(C, '    if b"\\x00" in src:', '    if False:')],
    'control chars accepted': [(C, '    for mark in _CONTROL:', '    for mark in ():')],
    '$ continuation in quotes ok': [(C, 'joins = (before == b"$"', 'joins = (False')],
    '2\\<nl>> is two tokens': [(C, '(after in (b"<", b">") and (before.isdigit() or before == b"&"))', 'False')],
    'mid-word continuation ok': [(C, 'in_word and root.descendant_for_byte_range(p, p + 2).type not in _OPAQUE', 'False')],
    'continuation in quotes flagged': [(C, 'in_word and root.descendant_for_byte_range(p, p + 2).type not in _OPAQUE', 'in_word')],
    'escaped backslash is a continuation': [(C, '        if (p - j) % 2:', '        if True:')],
    '\\cX kept': [(C, '        elif s[0] == "c" and len(s) == 2:', '        elif False:')],
    'env -S not split': [(H, '                words = [words[0]] + pieces + rest\n                lits = [True] * (1 + len(pieces)) + lits[len(lits) - len(rest):]\n                continue', '                pass')],
    'env -S $ trusted': [(H, 'if any(ch in split[0] for ch in "$\\\\#"):', 'if False:')],
    'env options after -S': [(H, 'until={"-S", "--split-string"} if name == "env" else ()', 'until=()')],
    'env -S \\v not split': [(H, '" \\t\\r\\n\\v\\f"', '" \\t\\r\\n"')],
    'empty value is None': [(H, 'val if ch in spec.values else val or None', 'val or None')],
    '+c is -c': [(H, 'if ch == "c" and a[0] == "-":', 'if ch == "c":')],
    'tmux unknown command is nothing': [(H, '        # load commands that run later.\n        return [Nested("tmux", "unknown", words=tuple(cmd))]', '        # load commands that run later.\n        return []')],
    'tmux formats trusted': [(H, '    if any("#(" in a for a in cmd):', '    if False:')],
    'tmux nothing is quiet': [(H, '    if TMUX_QUIET.match(sub):\n        return []', '    if False:\n        return []')],
    'Tab typed': [(H, 'r"any|tab|mouse', 'r"any|mouse')],
    'history expansion typed': [(H, '    if "!" in text[:end]:', '    if False:')],
    '/usr/local/bin trusted': [(H, 'SYSTEM_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin"}', 'SYSTEM_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin", "/usr/local/bin"}')],
    'child imports another copy': [(C, '    path = [str(root)] + [p for p in sys.path if p]', '    path = [p for p in sys.path if p]')],
    'sudo -H unsized': [(H, '"AbBEeHiKklNnPSsVv"', '"AbBEeiKklNnPSsVv"')],
    # round 5
    "redirect words not merged": [(C, "        words = sorted(words + [w for r in reds for w in r.words], key=lambda w: w.span[0])", "        pass")],
    "redirect words not read": [(C, '    if node.type == "file_redirect" and dest is not None:', "    if False:")],
    "redirect words on a compound": [(C, 'if body.type not in ("command", "unset_command"):', "if False:")],
    "heredoc end: text after": [(C, 'if lead or src[n.end_byte:n.end_byte + 1] not in (b"", b"\\n"):', "if lead:")],
    "heredoc end: text before": [(C, 'if lead or src[n.end_byte:n.end_byte + 1] not in (b"", b"\\n"):', 'if src[n.end_byte:n.end_byte + 1] not in (b"", b"\\n"):')],
    "heredoc end: tabs on <<E": [(C, 'if any(c.type == "<<-" for c in n.parent.children):', "if True:")],
    "case terminator anywhere": [(C, 'if t in (";;", ";&", ";;&") and not n.is_named and n.parent.type != "case_item":', "if False:")],
    "esac as argument": [(C, 'if t == "case_statement" and len(kids) > 2', 'if False and len(kids) > 2')],
    "unicode names": [(C, '_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*', '_NAME = re.compile(r"[A-Za-z_]\\w*')],
    "unicode wrapper names": [(H, '_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*', '_ASSIGN = re.compile(r"^[A-Za-z_]\\w*')],
    "quoted heredoc as code": [(C, 'if ct == "heredoc_body" and any(g.type in ("command_substitution", "process_substitution")', 'if False and any(g.type in ("command_substitution", "process_substitution")')],
    "heredoc fd lost": [(C, 'fd = next((c for c in node.children if c.type == "file_descriptor"), None)', "fd = None")],
    "paren continuation": [(C, ' or (before == b"(" and after == b"(")', "")],
    "system dir not normalised": [(H, "d = posixpath.normpath(posixpath.dirname(word))", "d = posixpath.dirname(word)")],
    "dotdot trusted": [(H, 'return ".." not in word.split("/") and d in SYSTEM_DIRS', "return d in SYSTEM_DIRS")],
    "untrusted wrapper silent": [(H, 'stopped = "untrusted-path" if words and os.path.basename(words[0]) in WRAPPERS else None', "stopped = None")],
    "eval path trusted": [(H, '"su", "runuser", "eval", "screen"', '"su", "runuser", "screen"')],
    "sudo -s expands $": [(H, 'if names & {"-s", "-i", "--shell", "--login"} and any("$" in a for a in rest[j:]):', "if False:")],
    "runuser -u ignored": [(H, 'if any(o in ("-u", "--user") for o, _ in opts) and rest:', "if False:")],
    "screen -ls anywhere": [(H, 'head = list(itertools.takewhile(lambda a: a.startswith("-"), args))', "head = args")],
    "copy-pipe quiet": [(H, 'if "-X" in flags and keys[:1] and re.match(', "if False and re.match(")],
    "fish runs one -c": [(H, 'if ok and prog == "fish":', "if False:")],
    "paste-buffer quiet": [(H, 'r"clock|server-info', 'r"paste|clock|server-info')],
    "tmux -f ignored": [(H, 'if any(o == "-f" for o, _ in opts):', "if False:")],
    "screen -c ignored": [(H, 'flags & {"-X", "-c"}', 'flags & {"-X"}')],
    "bare Enter quiet": [(H, "if end != -1 and not text[:end].strip():", "if False:")],
    "script files dropped": [(H, 'return [Nested(prog, "text", text=texts[-1], words=files)]', 'return [Nested(prog, "text", text=texts[-1])]')],
    "process substitution not seen": [(C, 'if n.type in ("command_substitution", "process_substitution"):\n            return True', 'if n.type == "command_substitution":\n            return True')],
    "control char in comment refused": [(C, "root.descendant_for_byte_range(p, p + len(mark)).type not in _OPAQUE", "True")],
    "octal not masked": [(C, "out.append(int(s, 8) & 0xFF)", "out.append(int(s, 8) & 0x7F)")],
    "\\U past unicode": [(C, "and int(s[1:], 16) <= 0x10FFFF:", ":")],
    "/sbin untrusted": [(H, 'SYSTEM_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin"}', 'SYSTEM_DIRS = {"/bin", "/usr/bin", "/usr/sbin"}')],
    "flag takes a value": [(H, "            if eq and arity == 0:", "            if False:")],
    "missing long value ok": [(H, "            if arity == 1 and not eq:\n                if i == len(args):", "            if arity == 1 and not eq:\n                if False:")],
    "missing short value ok": [(H, "                        if i == len(args):\n                            return opts, operands + args[i - 1:], False\n                        val = args[i]", "                        if False:\n                            return opts, operands + args[i - 1:], False\n                        val = args[i]")],
    "getopt -- ignored": [(H, '        if a == "--":\n            return opts, operands + args[i:], True', '        if False:\n            return opts, operands + args[i:], True')],
    "until ignored for longs": [(H, '            if "--" + hits[0] in until:', "            if False:")],
    "tmux escaped semicolon": [(H, 'elif a.endswith("\\\\;"):', "elif False:")],
    "run-shell -C runs text": [(H, 'or (hits[0] == "run-shell" and "-C" in flags)', "")],
    "send-keys -H typed": [(H, 'if flags & {"-H", "-K", "-F"}:', "if False:")],
    "command -V runs": [(H, '"command": {"-v", "-V"}', '"command": {"-v"}')],
    "sudo -K runs": [(H, '"sudo": {"-l", "-v", "-K", "-V",', '"sudo": {"-l", "-v", "-V",')],
    "shell -- ignored": [(H, '        if a in ("--", "-"):\n            k += 1\n            break', '        if a == "-":\n            k += 1\n            break')],
    "su bad option guessed": [(H, '        if not ok:\n            return [Nested(prog, "unknown", words=tuple(args))]', '        if False:\n            pass')],
    "screen bad option guessed": [(H, 'if not ok or flags & {"-X", "-c"}:', 'if flags & {"-X", "-c"}:')],
    "killed child is an error": [(C, "    if done.returncode < 0:", "    if False:")],
    "memory error is an error": [(C, '    if done.returncode == 1 and b"MemoryError" in done.stderr[-2000:]:', "    if False:")],
    "child memory not capped": [(C, 'if sys.platform == "linux" else ""', 'if False else ""')],
    "split redirect word not joined": [(C, "if runs and runs[-1][-1].end_byte == c.start_byte:", "if False:")],
    "brace glued to a word": [(C, 'return "a brace glued to the word next to it"', "pass")],
    "continuation opens a line": [(C, 'if p and before == b"\\n" and root.descendant_for_byte_range(p, p + 2).type not in _OPAQUE:', "if False:")],
    "child not isolated": [(C, '[sys.executable, "-I", "-c", code]', '[sys.executable, "-c", code]')],
    # round 6
    "backtick in backticks": [(C, 'if b"`" in src[n.start_byte + 1:n.end_byte - 1]:', "if False:")],
    "continuation in a heredoc body": [(C, "if any(a <= p < b for a, b in bodies):", "if False:")],
    ">&- argument is a target": [(C, 'if op.endswith("&-"):', "if False:")],
    "== dropped": [(C, 'if t in ("==", "=~") and n.parent.type in ("command", "declaration_command", "unset_command"):', "if False:")],
    "command pieces on two lines": [(C, 'for a, b in zip(kids, kids[1:]):         # a command\'s pieces share one line (or a continuation)\n                if re.search(rb"(?<!\\\\)\\n", src[a.end_byte:b.start_byte]):', 'for a, b in zip(kids, kids[1:]):\n                if False:')],
    "last stage not followed": [(C, 'while body.type in ("pipeline", "list", "negated_command"):', "while False:")],
    "declaration redirect words": [(C, 'if body.type not in ("command", "unset_command"):', 'if body.type not in ("command", "declaration_command", "unset_command"):')],
    "su arguments ignored": [(H, 'return [Nested(prog, "unknown", words=tuple(extra))]   # no command', "return []   # no command")],
    "send-keys -M typed": [(H, 'if flags & {"-X", "-M"}:', 'if flags & {"-X"}:')],
    "screen -wipe starts": [(H, '("-ls", "-list", "-wipe")', '("-ls", "-list")')],
    "swallowed newline kept": [(C, "        at = None if tree.root_node.has_error else _swallowed_newline(tree.root_node, src)", "        at = None")],
    "swallowed newline in redirect words": [(C, 'elif n.type == "file_redirect":\n            pairs', 'elif False:\n            pairs')],
    "swallowed newline after comment": [(C, 'if a.type != "comment" and b.type != "comment" and', "if")],
    # round 7
    "dash before redirect lost": [(C, "        if _DASH.fullmatch(gap):\n            at = lo + gap.index(b\"-\")", "        if False:\n            at = lo + gap.index(b\"-\")")],
    "assignment value cut short": [(C, "if assigns and not words and assigns[-1].span[1] == c.start_byte:", "if False:")],
    "touching words split": [(C, "        elif kept and kept[-1].span[1] == w.span[0]:\n            # Touching", "        elif False:\n            # Touching")],
    "&> takes an fd": [(C, 'if r.fd is None and not r.op.startswith("&>")}', "if r.fd is None}")],
    "redirect in $( ) lost": [(C, 'if t in _REDIRECTS and n.parent is not None and n.parent.type == "command_substitution":', "if False:")],
    "assignment-only is simple": [(C, 'if kind == "simple" and not words and assigns:', "if False:")],
    "escaped $ keeps backslash": [(C, 'if t in ("word", "$"):', 'if t == "word":')],
    "arithmetic in heredoc is a command": [(C, 'if st.ptype == "heredoc_body" and extra["src"][n.start_byte:n.start_byte + 3] == b"$((":', "if False:")],
    "ANSI-C end not checked": [(C, "                if i != len(raw) - 1:", "                if False:")],
    "assignment glue allowed": [(C, 'return "an assignment glued to what follows"', 'pass')],
    "word glued to redirect": [(C, 'return "a word glued to a redirect"', "pass")],
    "$ string across space": [(C, 'return "a $ joined to a string across white space"', "pass")],
    "concatenation across space": [(C, "if t == \"concatenation\" and not inexp and any(", "if False and any(")],
    "word with white space": [(C, 'return "a word with white space in it"', "pass")],
    "unaccounted text": [(C, 'return "text the grammar did not account for"', "pass")],
    ">& target twice": [(C, 'return "a >& target, which bash expands twice"', "pass")],
    ">& dash target": [(C, 'return "a redirect target bash reads as a `-`"', "pass")],
    ">&- words not counted": [(C, 'len(runs) > (0 if op.endswith("&-") else 1)', "len(runs) > 1")],
    "escaped delimiter before continuation": [(C, "(esc or before not in b\" \\t\\n;&|(<>\")", "(before not in b\" \\t\\n;&|(<>\")")],
    "newline opening a word": [(C, 'if n.type in ("word", "concatenation", "command_name") and src[n.start_byte:n.start_byte + 1] == b"\\n" \\', 'if False and src[n.start_byte:n.start_byte + 1] == b"\\n" \\')],
    "time keeps VAR=": [(H, 'elif name in ("time", "!", "coproc"):', 'elif False:')],
    "sudo -i alone": [(H, 'if names & {"-s", "-i", "--shell", "--login"} and not rest[j:]:', "if False:")],
    "trap text unseen": [(H, 'if prog == "trap":', 'if False:')],
    "trap listing runs": [(H, 'ops[0] not in ("-", "-l", "-p")', "True")],
    "subscript substitutions unseen": [(H, "if prog in _SUBSCRIPTED and any(", "if False and any(")],
    # round 8, slice A
    "quoted substitution in arithmetic": [(C, 'return "a quoted substitution that arithmetic or a subscript would run"', "pass")],
    "substitution in assignment subscript": [(C, 'return "a substitution in an assignment subscript"', "pass")],
    "substitution in array subscript": [(C, 'return "a substitution in an array subscript"', "pass")],
    "expansion after a quote is literal": [(C, 'return Word(_text(node, src), raw, _literal(node, src) and not _expands(raw),', 'return Word(_text(node, src), raw, _literal(node, src),')],
    "joined word literal ignores expansion": [(C, 'all(w.literal for w in words) and not _expands(src[span[0]:span[1]].decode("utf-8", "replace")), span)', 'all(w.literal for w in words), span)')],
    "touching words literal ignores expansion": [(C, 'p.literal and w.literal and not _expands(p.raw + w.raw), (p.span[0]', 'p.literal and w.literal, (p.span[0]')],
    "odd descriptor taken": [(C, 'return "a file descriptor bash would not take as one"', "pass")],
    "fd variable non-ASCII": [(C, r'_FD_VAR = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}\Z")', r'_FD_VAR = re.compile(r"\{[A-Za-z_]\w*\}\Z")')],
    "dollar glued to a redirect": [(C, 'return "a $ glued to a redirect"', "pass")],
    "r8 hex brace escape": [(C, r"(x\{[0-9A-Fa-f]*\}?|x[0-9A-Fa-f]{1,2}", r"(x[0-9A-Fa-f]{1,2}")],
    "r8 surrogateescape": [(C, 'decode("utf-8", "surrogateescape")', 'decode("utf-8", "replace")')],
    "r8 assignment across space": [(C, 'return "an assignment whose value is across white space"', "pass")],
    "r8 heredoc delimiter operator": [(C, 'return "a heredoc delimiter holding a shell operator"', "pass")],
    "r8 quote opener": [(C, 'return "a quoted string the grammar started before its opening quote"', "pass")],
    "r8 heredoc command line": [(C, 'return "text between the parts of a heredoc redirect"', "pass")],
    "r8 control backslash": [(C, '        elif s == "c\\\\\\\\":','        elif False:')],
    # round 8, slice C
    "launchers unseen": [(H, "    if prog in LAUNCHERS or", "    if False or")],
    "su without command is nothing": [(H, '        return [Nested(prog, "unknown", words=tuple(extra))]   #', '        return [Nested(prog, "unknown", words=tuple(extra))] if extra else []   #')],
    "parse in process": [(C, "    if ALWAYS_CHILD or len(src) > IN_PROCESS_BYTES or not text.isascii():", "    if False:")],
    "non-ascii parsed in process": [(C, " or not text.isascii():", ":")],
    # round 9i
    "r9i nested does not unwrap": [(H, "    if unwrap_first and words:\n", "    if False:\n")],
    "r9i unwrap stop is nothing": [(H, '        if u.stopped:\n            return [Nested(', '        if u.stopped:\n            return [] or [Nested(')],
    "r9i source unseen": [(H, ' or prog in (".", "source"):', ':')],
    "r9i git glued -c accepted": [(H, "        elif a.startswith(\"-\"):\n            return GitArgs(", "        elif a.startswith(\"-c\") and False:\n            pass\n        elif a.startswith(\"-\"):\n            return GitArgs(")],
    "r9i git -C value unread": [(H, "            opts.append((a, args[i + 1]))\n            i += 1", "            opts.append((a, args[i + 1]))")],
    "r9i git unknown not reported": [(H, "tuple(args[i:]), False, a)", "tuple(args[i:]), False, None)")],
    "r9i nonliteral prefix unseen": [(H, "        if bad(i):\n            return nonlit(i)", "        if False:\n            return nonlit(i)")],
    "r9i nonliteral env unseen": [(H, "                if bad2(rest, j):\n                    return Unwrapped(tuple(rest[j:]), tuple(peeled), \"nonliteral-assignment\", tuple(options))\n                j += 1                                   # env", "                j += 1                                   # env")],
    "r9i git eq value dropped": [(H, "opts.append((name, val))", "opts.append((name, None))")],
    # r9h: heredoc fallback and bodies
    "r9h fallback off": [(C, '    if tree.root_node.has_error and b"<<" in src:', "    if False:")],
    "r9h unquoted substitution body read": [(C, "        if _SUBST_MARK.search(raw):\n            return None", "        if False:\n            return None")],
    "r9h loose terminator": [(C, 'if (line.lstrip(b"\\t") if dash else line) == delim:', "if line.strip() == delim:")],
    "r9h unused opener ignored": [(C, "src.used != set(src.heredocs)", "False")],
    "r9h tabs kept in <<- body": [(C, "    if dash:\n        raw = re.sub(", "    if False:\n        raw = re.sub(")],
    "r9h here-string body dropped": [(C, "span, (), tgt.text)", "span, (), None)")],
    "r9h <<- dash left in place": [(C, "out[o:o + 2 + dash] =", "out[o:o + 2] =")],
    "r9h quoted delimiter unnoticed": [(C, "quoted = raw != delim", "quoted = False")],
}
TEST = "import sys; sys.path.insert(0, 'tests'); import test_bashtree as t; [f() for n, f in sorted(vars(t).items()) if n.startswith('test_')]"


def run(item):
    name, edits = item
    with tempfile.TemporaryDirectory(prefix="bashtree-mut-") as tmp:
        d = Path(tmp)
        shutil.copytree(SRC / "bashtree", d / "bashtree")
        shutil.copytree(SRC / "tests", d / "tests")
        for fname, a, b in edits:
            f = d / "bashtree" / fname
            text = f.read_text()
            if a not in text:
                return f"{name:32} -> STALE (mutation no longer applies)"
            f.write_text(text.replace(a, b))
        try:
            res = subprocess.run([sys.executable, "-c", TEST], cwd=d, capture_output=True,
                                 text=True, timeout=240)           # every parse runs in a child now: the suite takes 40 s alone
            if res.returncode and not any(e in res.stderr for e in ("AssertionError", "ValueError", "IndexError", "RuntimeError")):
                return f"{name:32} -> ERROR, not an assertion: {res.stderr.strip().splitlines()[-1][:80]}"
            return f"{name:32} -> {'killed' if res.returncode else 'SURVIVED'}"
        except subprocess.TimeoutExpired:
            return f"{name:32} -> killed (timeout)"


if __name__ == "__main__":
    # A repeated name silently replaced the earlier mutant once (66 of them).
    import ast
    names = [k.value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n, ast.Dict)
             for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    dup = {k for k in names if names.count(k) > 1}
    assert not dup, f"duplicate mutant names: {sorted(dup)}"
    same = [n for n, e in MUTANTS.items() if sum(repr(e) == repr(f) for f in MUTANTS.values()) > 1]
    assert not same, f"mutants repeating another's edits: {same}"
    # Control: the unmodified copy must pass, or "killed" means nothing.
    control = run(("control (no mutation)", []))
    print(control.replace("SURVIVED", "passes, as it must"))
    if "SURVIVED" not in control:
        sys.exit("the tests fail without any mutation; fix that first")
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(run, MUTANTS.items()))
    print("\n".join(results))
    sys.exit(0 if all(" killed" in r for r in results) else 1)

