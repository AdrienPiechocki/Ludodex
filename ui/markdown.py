"""markdown.py — analyse Markdown en éléments de rendu, pour l'aperçu en direct des notes.

Fonctionne sur le texte brut (celui du buffer) et renvoie, pour une ligne, une
liste d'« éléments » : chacun décrit la portion à styler, les marqueurs de
syntaxe à masquer, et s'il s'agit d'un marqueur de bloc. Le buffer garde toujours
le Markdown d'origine : « l'aperçu » n'est qu'une pose de balises.

Ce module ne dépend pas de GTK : `line_elements` est pur, donc testable sans
affichage.
"""
import re
from collections import namedtuple
from urllib.parse import unquote

_FENCE = re.compile(r"^[ \t]*```")
_HEADING = re.compile(r"^(#{1,6})[ \t]+")
_QUOTE = re.compile(r"^([ \t]*>+[ \t]?)")
_LIST = re.compile(r"^([ \t]*)([-*+]|\d+[.)])([ \t]+)(\[[ xX]\][ \t]+)?")
_TASK_MARK = re.compile(r"^[ \t]*([-*+]|\d+[.)])[ \t]+(\[[ xX]\])")
_LINK_URL = re.compile(r"\]\(([^)]+)\)")

HEADING_SCALE = {1: 1.6, 2: 1.4, 3: 1.2, 4: 1.1, 5: 1.1, 6: 1.0}

# Un élément : `kind` = style à appliquer au contenu (ou None) ;
# `cstart`/`cend` = bornes du contenu ; `markers` = portions de syntaxe à masquer ;
# `block` = vrai pour un marqueur de bloc (titre, citation) qui occupe toute la
# tête de ligne ; la révélation suit la règle générale : marqueur exact sous le
# curseur (voir `markers_revealed`).
Element = namedtuple("Element", "kind cstart cend markers block")
# `task` : (début, fin, coché) de la case à cocher `[ ]`/`[x]`, sinon None.
Line = namedtuple("Line", "elements indent marker checked task", defaults=(None,))

# (type de style du contenu, motif, explorer l'intérieur). Le groupe « c » est le
# contenu visible ; tout le reste de la correspondance (les marqueurs) sera masqué.
_PATTERNS = [
    ("code", re.compile(r"`(?P<c>[^`\n]+)`"), False),
    (None, re.compile(r"!\[(?P<c>[^\]]*)\]\([^)\s]+\)"), False),
    ("link", re.compile(r"\[(?P<c>[^\]]+)\]\([^)\s]+\)"), True),
    ("bi", re.compile(r"\*\*\*(?P<c>.+?)\*\*\*"), True),
    ("bi", re.compile(r"___(?P<c>.+?)___"), True),
    ("bold", re.compile(r"\*\*(?P<c>.+?)\*\*"), True),
    ("bold", re.compile(r"(?<![\w])__(?P<c>.+?)__(?![\w])"), True),
    ("strike", re.compile(r"~~(?P<c>.+?)~~"), True),
    ("italic", re.compile(r"(?<!\*)\*(?P<c>\S(?:.*?\S)?)\*(?!\*)"), True),
    ("italic", re.compile(r"(?<![\w])_(?P<c>\S(?:.*?\S)?)_(?![\w])"), True),
]


def is_fence(line):
    """La ligne ouvre/ferme-t-elle un bloc de code ``` ?"""
    return bool(_FENCE.match(line))


def _match(text, pos):
    """Premier motif reconnu à `pos`, sinon None.

    Renvoie (kind, cstart, cend, markers, recurse, end)."""
    for kind, rx, recurse in _PATTERNS:
        mo = rx.match(text, pos)
        if mo is None:
            continue
        cs, ce = mo.start("c"), mo.end("c")
        markers = [(mo.start(), cs), (ce, mo.end())]
        return kind, cs, ce, markers, recurse, mo.end()
    return None


def _scan(text, base=0):
    """Parcourt `text` et renvoie la liste des éléments (décalages absolus)."""
    els = []
    i, n = 0, len(text)
    while i < n:
        hit = _match(text, i)
        if hit is None:
            i += 1
            continue
        kind, cs, ce, markers, recurse, end = hit
        els.append(Element(kind, base + cs, base + ce,
                           [(base + a, base + b) for a, b in markers if b > a], False))
        if recurse and ce > cs:  # mise en forme imbriquée (le code, lui, est protégé)
            els += _scan(text[cs:ce], base + cs)
        i = end
    return els


def line_elements(raw, in_code=False):
    """Décompose une ligne brute en éléments de rendu.

    Renvoie un `Line` :
      - elements : liste d'`Element` (styles, marqueurs, bloc) ;
      - indent   : niveau d'indentation (listes), 0 sinon ;
      - marker   : bornes de la puce/du numéro à colorer, ou None ;
      - checked  : bornes du texte d'une tâche cochée (à barrer), ou None.
    """
    if in_code:
        els = [Element("code", 0, len(raw), [], False)] if raw else []
        return Line(els, 0, None, None)
    m = _HEADING.match(raw)
    if m:
        els = [Element(f"h{len(m.group(1))}", m.end(), len(raw), [(0, m.end())], True)]
        return Line(els + _scan(raw[m.end():], m.end()), 0, None, None)
    m = _QUOTE.match(raw)
    if m:
        els = [Element("quote", m.end(), len(raw), [(0, m.end())], True)]
        return Line(els + _scan(raw[m.end():], m.end()), 0, None, None)
    m = _LIST.match(raw)
    if m:
        indent = min(4, 1 + len(m.group(1).replace("\t", "  ")) // 2)
        marker = (m.start(2), m.end(2))
        checked = task = None
        if m.group(4):                      # `[ ] ` / `[x] ` : on ne garde que les 3 caractères
            checked_state = m.group(4).lstrip().lower().startswith("[x")
            task = (m.start(4), m.start(4) + 3, checked_state)
            if checked_state:
                checked = (m.end(4), len(raw))
        return Line(_scan(raw, 0), indent, marker, checked, task)
    return Line(_scan(raw, 0), 0, None, None)


def task_at(raw, col):
    """Bornes de la case à cocher (`[ ]`/`[x]`) si `col` est dessus, sinon None."""
    m = _TASK_MARK.match(raw)
    if m and m.start(2) <= col <= m.end(2):
        return (m.start(2), m.end(2))
    return None


def link_at(raw, col, in_code=False):
    """URL du lien dont le texte contient `col`, sinon None (jamais dans le code)."""
    if in_code:
        return None
    for el in _scan(raw, 0):
        if el.kind == "link" and el.cstart <= col <= el.cend:
            edges = [el.cstart, el.cend] + [x for a, b in el.markers for x in (a, b)]
            m = _LINK_URL.search(raw[min(edges):max(edges)])
            if m:
                return m.group(1)
    return None


def heading_text(raw):
    """Texte d'un titre (`## Toto` -> « Toto »), sinon None."""
    m = _HEADING.match(raw)
    return raw[m.end():].strip() if m else None


def heading_level(raw):
    """Niveau du titre (1-6), sinon 0 (pas un titre)."""
    m = _HEADING.match(raw)
    return len(m.group(1)) if m else 0


def markers_revealed(markers, cursor_col):
    """Le curseur est-il posé EXACTEMENT sur l'un des marqueurs ?

    Bornes comprises des deux côtés : la position juste avant le premier
    caractère et celle juste après le dernier comptent (on les tapait il y a un
    instant), pas celle d'après — tapant « # Titre », les dièses se masquent
    dès qu'on entre dans le titre. Hors marqueur (colonne négative comprise,
    curseur sur une autre ligne), la syntaxe reste cachée."""
    if cursor_col < 0:
        return False
    return any(a <= cursor_col <= b for a, b in markers)


def heading_slug(text):
    """Identifiant d'ancre façon GitHub : minuscules, espaces en tirets, ponctuation ôtée."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    return s.strip("-")


def anchor_matches(fragment, heading):
    """Le fragment d'ancre `#…` désigne-t-il ce titre ? (slug GitHub ou texte brut)."""
    want = unquote(fragment).lstrip("#").strip()
    if not want:
        return False
    if heading_slug(want) == heading_slug(heading):
        return True
    return want.casefold() == heading.strip().casefold()
