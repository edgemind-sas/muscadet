"""Render the reStructuredText left in muscadet's docstrings as Markdown.

The docstrings follow numpydoc, whose body text is reStructuredText, while
mkdocstrings renders that text as Markdown. Most of it reads the same either
way (``code``, *emphasis*, bullet lists); four constructs do not, and this
griffe extension rewrites them once the whole package is loaded, so the
sources keep the convention every Python tool reads:

* simple tables (``====  ====`` borders) become pipe tables;
* literal blocks (a paragraph ending in ``::``) and ``.. code-block::``
  directives become fenced code blocks;
* cross-reference roles (``:func:`x```, ``:class:`~a.B```...) become links
  when the target is a documented muscadet object, inline code otherwise.

Only the rendered site sees the result: nothing here writes to a source file.
"""

from __future__ import annotations

import ast
import re
import textwrap

import griffe

ROLE = re.compile(
    r":(?:py:)?(?:meth|class|func|attr|mod|data|exc|obj|const|ref):`([^`]+)`"
)
BORDER = re.compile(r"^(\s*)(=+)((?:\s+=+)+)\s*$")
DIRECTIVE = re.compile(r"^(\s*)\.\. code-block::\s*(\S*)\s*$")


class RstToMarkdown(griffe.Extension):
    """Rewrite every docstring of the loaded package, see the module doc."""

    def on_package(self, *, pkg: griffe.Module, **kwargs) -> None:
        for obj in _walk(pkg):
            if obj.docstring is not None:
                obj.docstring.value = convert(obj.docstring.value, obj, pkg)


def _walk(obj: griffe.Object):
    yield obj
    for member in obj.members.values():
        if not member.is_alias:
            yield from _walk(member)


def convert(text: str, obj: griffe.Object, pkg: griffe.Module) -> str:
    lines = text.splitlines()
    lines = _convert_tables(lines)
    lines = _convert_literal_blocks(lines)
    return ROLE.sub(lambda m: _role(m.group(1), obj, pkg), "\n".join(lines))


# --- tables ---------------------------------------------------------------


def _columns(border: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"=+", border)]


def _cells(line: str, cols: list[tuple[int, int]]) -> list[str]:
    cells = []
    for i, (start, _) in enumerate(cols):
        end = cols[i + 1][0] if i + 1 < len(cols) else len(line)
        cells.append(line[start:end].strip())
    return cells


def _pipe_row(indent: str, cells: list[str]) -> str:
    return indent + "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _convert_tables(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = BORDER.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent, cols = m.group(1), _columns(lines[i])
        # A simple table is two or three borders; collect up to the last one,
        # i.e. the border followed by a blank line or by the end of the text.
        j, borders, rows = i + 1, [i], []
        while j < len(lines):
            if BORDER.match(lines[j]):
                borders.append(j)
                if j + 1 >= len(lines) or not lines[j + 1].strip():
                    break
            elif lines[j].strip():
                rows.append((j, lines[j]))
            j += 1
        if len(borders) < 2:
            out.append(lines[i])
            i += 1
            continue
        header: list[str] | None = None
        body: list[list[str]] = []
        for lineno, line in rows:
            cells = _cells(line, cols)
            is_header = len(borders) == 3 and lineno < borders[1]
            if is_header:
                header = cells if header is None else _join(header, cells)
            elif not cells[0] and body:
                body[-1] = _join(body[-1], cells)  # continuation line
            else:
                body.append(cells)
        if out and out[-1].strip():
            out.append("")
        out.append(_pipe_row(indent, header or [""] * len(cols)))
        out.append(indent + "|" + "---|" * len(cols))
        out.extend(_pipe_row(indent, row) for row in body)
        i = borders[-1] + 1
    return out


def _join(row: list[str], more: list[str]) -> list[str]:
    return [" ".join(x for x in (a, b) if x) for a, b in zip(row, more)]


# --- literal blocks -------------------------------------------------------


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _language(block: str) -> str:
    try:
        ast.parse(block)
    except SyntaxError:
        return "text"
    return "python"


def _convert_literal_blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        directive = DIRECTIVE.match(line)
        literal = line.rstrip().endswith("::") and not directive
        if not (directive or literal):
            out.append(line)
            i += 1
            continue
        base = _indent_of(line)
        # The block is the run of lines indented deeper than the introducer,
        # blank lines included, starting after the blank line that opens it.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        start = j
        while j < len(lines) and (
            not lines[j].strip() or _indent_of(lines[j]) > base
        ):
            j += 1
        while j > start and not lines[j - 1].strip():
            j -= 1
        if start == j or _indent_of(lines[start]) <= base:
            out.append(line)
            i += 1
            continue
        block = textwrap.dedent("\n".join(lines[start:j]))
        pad = " " * base
        if directive:
            lang = directive.group(2) or _language(block)
        else:
            lang = _language(block)
            text = line.rstrip()[:-2].rstrip()
            # "Paragraph::" keeps one colon; a bare "::" disappears.
            if text.strip():
                out.append(text + ":")
            out.append("")
        out.append(f"{pad}```{lang}")
        out.extend(pad + b if b else "" for b in block.splitlines())
        out.append(f"{pad}```")
        i = j
    return out


# --- roles ----------------------------------------------------------------


def _role(target: str, obj: griffe.Object, pkg: griffe.Module) -> str:
    title_given = re.match(r"^(.*?)\s*<([^>]+)>$", target)
    if title_given:
        title, target = title_given.group(1), title_given.group(2)
    else:
        short = target.startswith("~")
        target = target.lstrip("~").lstrip(".")
        title = target.rsplit(".", 1)[-1] if short else target
    path = _resolve(target.lstrip("~").lstrip("."), obj, pkg)
    code = f"`{title}`"
    if path is None:
        return code
    # Optional: a private or filtered target renders as plain code rather
    # than failing the strict build.
    return f'<autoref identifier="{path}" optional>{code}</autoref>'


def _resolve(target: str, obj: griffe.Object, pkg: griffe.Module) -> str | None:
    head, _, rest = target.partition(".")
    candidates = []
    scope: griffe.Object | None = obj
    while scope is not None:
        try:
            candidates.append(scope.resolve(head))
        except Exception:  # noqa: BLE001 - griffe raises several kinds here
            pass
        scope = scope.parent
    candidates.append(head)
    for base in candidates:
        path = _canonical(f"{base}.{rest}" if rest else base, pkg)
        if path is not None:
            return path
    return None


def _canonical(path: str, pkg: griffe.Module) -> str | None:
    """The documented path of a muscadet object, through any re-export."""
    if path == pkg.path:
        return path
    if not path.startswith(pkg.path + "."):
        return None
    try:
        found = pkg[path[len(pkg.path) + 1 :]]
        if found.is_alias:
            found = found.final_target
    except Exception:  # noqa: BLE001 - unknown name or external alias
        return None
    return found.path if found.path.startswith(pkg.path + ".") else None
