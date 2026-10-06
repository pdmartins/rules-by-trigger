"""The `status` table, as pure code: rows in, lines out.

Fixed column widths, fixed separators, no colour and nothing that depends on
the clock, so two runs over the same data print the same bytes. Cutting a cell
to its width and choosing the labels are the only decisions made here; which
rules are rows, and in what order, is decided by the caller."""

from .common import HOOK

SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
STATE_ACTIVE = "active"
STATE_DISABLED = "disabled"

# Rule, Scope, State, This repo, Total — and which of them hold numbers.
COLUMN_WIDTHS = (40, 18, 12, 10, 7)
NUMERIC_COLUMNS = (3, 4)
COLUMN_SEPARATOR = "  "
RULER_CHAR = "-"
ELLIPSIS = "…"
SEGMENT_SEPARATOR = "/"
SUBDIR_OPEN = " ("
SUBDIR_CLOSE = ")"

LANGUAGE_EN = HOOK.DEFAULT_LANGUAGE
LANGUAGE_PT = HOOK.BRAZILIAN_PORTUGUESE

# ---- user-visible text, one row of labels per language -----------------------
LABEL_RULE, LABEL_SCOPE, LABEL_STATE = "rule", "scope", "state"
LABEL_THIS_REPO, LABEL_TOTAL = "this_repo", "total"
LABEL_NO_RULES, LABEL_COVERING = "no_rules", "covering"
# The header cells, in column order.
HEADER_KEYS = (LABEL_RULE, LABEL_SCOPE, LABEL_STATE, LABEL_THIS_REPO, LABEL_TOTAL)
LABELS = {
    LANGUAGE_EN: {LABEL_RULE: "Rule", LABEL_SCOPE: "Scope", LABEL_STATE: "State",
                  LABEL_THIS_REPO: "This repo", LABEL_TOTAL: "Total",
                  STATE_ACTIVE: "active", STATE_DISABLED: "disabled",
                  SCOPE_PROJECT: "project", SCOPE_GLOBAL: "global",
                  LABEL_NO_RULES: "No rules.",
                  LABEL_COVERING: "Covering {path}:"},
    LANGUAGE_PT: {LABEL_RULE: "Regra", LABEL_SCOPE: "Escopo", LABEL_STATE: "Estado",
                  LABEL_THIS_REPO: "Neste repo", LABEL_TOTAL: "Total",
                  STATE_ACTIVE: "ativa", STATE_DISABLED: "desabilitada",
                  SCOPE_PROJECT: "projeto", SCOPE_GLOBAL: "global",
                  LABEL_NO_RULES: "Nenhuma regra.",
                  LABEL_COVERING: "Cobrem {path}:"},
}
COVERING_INDENT = "  "
COVERING_LINE = "{indent}{name}  {scope}"
# -----------------------------------------------------------------------------


def labels_for(language):
    """The labels of a configured language: Portuguese for `pt-BR` (spelled
    like the hook spells it), English for anything else."""
    if HOOK.canonical_language(language) == LANGUAGE_PT:
        return LABELS[LANGUAGE_PT]
    return LABELS[LANGUAGE_EN]


def fit(text, width):
    """`text` cut to `width`, the ellipsis counting inside it."""
    text = str(text)
    if len(text) <= width:
        return text
    return text[:width - 1] + ELLIPSIS


def scope_text(kind, subdir, labels):
    """The scope as a reader sees it, uncut: `project`, `global` or
    `project (<subdir>)`."""
    if kind == SCOPE_GLOBAL:
        return labels[SCOPE_GLOBAL]
    if not subdir:
        return labels[SCOPE_PROJECT]
    return f"{labels[SCOPE_PROJECT]}{SUBDIR_OPEN}{subdir}{SUBDIR_CLOSE}"


def scope_cell(kind, subdir, labels, width):
    """The Scope cell. A nested project's folder that does not fit is cut from
    the START — the end is what tells projects apart — keeping the longest run
    of trailing folders that fits behind `…/`; when even the last folder alone
    does not fit, its tail behind a bare `…`."""
    full = scope_text(kind, subdir, labels)
    if len(full) <= width:
        return full
    head = f"{labels[SCOPE_PROJECT]}{SUBDIR_OPEN}"
    room = width - len(head) - len(SUBDIR_CLOSE)
    segments = subdir.split(SEGMENT_SEPARATOR)
    for first in range(1, len(segments)):
        shown = ELLIPSIS + SEGMENT_SEPARATOR + SEGMENT_SEPARATOR.join(segments[first:])
        if len(shown) <= room:
            return f"{head}{shown}{SUBDIR_CLOSE}"
    return f"{head}{ELLIPSIS}{segments[-1][-(room - 1):]}{SUBDIR_CLOSE}"


def line_of(cells):
    """One table line from five cells: each cut to its width, the numbers
    aligned to the right, columns joined by the separator."""
    parts = []
    for index, (cell, width) in enumerate(zip(cells, COLUMN_WIDTHS)):
        cut = fit(cell, width)
        parts.append(cut.rjust(width) if index in NUMERIC_COLUMNS else cut.ljust(width))
    return COLUMN_SEPARATOR.join(parts)


def render_table(rows, labels):
    """The table's lines for `rows` already in display order. Each row holds
    `name`, `kind`, `subdir`, `state`, `this_repo` and `total`. No rows, one
    line saying so."""
    if not rows:
        return [labels[LABEL_NO_RULES]]
    lines = [line_of([labels[key] for key in HEADER_KEYS]),
             COLUMN_SEPARATOR.join(RULER_CHAR * width for width in COLUMN_WIDTHS)]
    for row in rows:
        lines.append(line_of([row["name"],
                              scope_cell(row["kind"], row["subdir"], labels,
                                         COLUMN_WIDTHS[1]),
                              labels[row["state"]], row["this_repo"], row["total"]]))
    return lines


def render_covering(rows, labels, path):
    """The block that follows the table with `--path`: the header, then one
    rule per line — name and scope, uncut."""
    lines = [labels[LABEL_COVERING].format(path=path)]
    for row in rows:
        lines.append(COVERING_LINE.format(
            indent=COVERING_INDENT, name=row["name"],
            scope=scope_text(row["kind"], row["subdir"], labels)))
    return lines
