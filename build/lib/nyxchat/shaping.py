"""Persian/Arabic shaping + minimal BiDi reordering for terminals
that don't do it natively. Pure python, no dependencies."""
from __future__ import annotations

# ── presentation forms ────────────────────────────────────────────────
_SHAPES = {
    '\u0621': ('\uFE80', None, None, None),
    '\u0622': ('\uFE81', '\uFE82', None, None),
    '\u0623': ('\uFE83', '\uFE84', None, None),
    '\u0624': ('\uFE85', '\uFE86', None, None),
    '\u0625': ('\uFE87', '\uFE88', None, None),
    '\u0626': ('\uFE89', '\uFE8A', '\uFE8B', '\uFE8C'),
    '\u0627': ('\uFE8D', '\uFE8E', None, None),
    '\u0628': ('\uFE8F', '\uFE90', '\uFE91', '\uFE92'),
    '\u0629': ('\uFE93', '\uFE94', None, None),
    '\u062A': ('\uFE95', '\uFE96', '\uFE97', '\uFE98'),
    '\u062B': ('\uFE99', '\uFE9A', '\uFE9B', '\uFE9C'),
    '\u062C': ('\uFE9D', '\uFE9E', '\uFE9F', '\uFEA0'),
    '\u062D': ('\uFEA1', '\uFEA2', '\uFEA3', '\uFEA4'),
    '\u062E': ('\uFEA5', '\uFEA6', '\uFEA7', '\uFEA8'),
    '\u062F': ('\uFEA9', '\uFEAA', None, None),
    '\u0630': ('\uFEAB', '\uFEAC', None, None),
    '\u0631': ('\uFEAD', '\uFEAE', None, None),
    '\u0632': ('\uFEAF', '\uFEB0', None, None),
    '\u0633': ('\uFEB1', '\uFEB2', '\uFEB3', '\uFEB4'),
    '\u0634': ('\uFEB5', '\uFEB6', '\uFEB7', '\uFEB8'),
    '\u0635': ('\uFEB9', '\uFEBA', '\uFEBB', '\uFEBC'),
    '\u0636': ('\uFEBD', '\uFEBE', '\uFEBF', '\uFEC0'),
    '\u0637': ('\uFEC1', '\uFEC2', '\uFEC3', '\uFEC4'),
    '\u0638': ('\uFEC5', '\uFEC6', '\uFEC7', '\uFEC8'),
    '\u0639': ('\uFEC9', '\uFECA', '\uFECB', '\uFECC'),
    '\u063A': ('\uFECD', '\uFECE', '\uFECF', '\uFED0'),
    '\u0641': ('\uFED1', '\uFED2', '\uFED3', '\uFED4'),
    '\u0642': ('\uFED5', '\uFED6', '\uFED7', '\uFED8'),
    '\u0643': ('\uFED9', '\uFEDA', '\uFEDB', '\uFEDC'),
    '\u0644': ('\uFEDD', '\uFEDE', '\uFEDF', '\uFEE0'),
    '\u0645': ('\uFEE1', '\uFEE2', '\uFEE3', '\uFEE4'),
    '\u0646': ('\uFEE5', '\uFEE6', '\uFEE7', '\uFEE8'),
    '\u0647': ('\uFEE9', '\uFEEA', '\uFEEB', '\uFEEC'),
    '\u0648': ('\uFEED', '\uFEEE', None, None),
    '\u0649': ('\uFEEF', '\uFEF0', None, None),
    '\u064A': ('\uFEF1', '\uFEF2', '\uFEF3', '\uFEF4'),
    # Persian extras
    '\u067E': ('\uFB56', '\uFB57', '\uFB58', '\uFB59'),   # پ
    '\u0686': ('\uFB7A', '\uFB7B', '\uFB7C', '\uFB7D'),   # چ
    '\u0698': ('\uFB8A', '\uFB8B', None, None),           # ژ
    '\u06A9': ('\uFB8E', '\uFB8F', '\uFB90', '\uFB91'),   # ک
    '\u06AF': ('\uFB92', '\uFB93', '\uFB94', '\uFB95'),   # گ
    '\u06CC': ('\uFBFC', '\uFBFD', '\uFBFE', '\uFBFF'),   # ی
}

_LAM_ALEF = {
    ('\u0644', '\u0627'): '\uFEFB',
    ('\u0644', '\u0622'): '\uFEF5',
    ('\u0644', '\u0623'): '\uFEF7',
    ('\u0644', '\u0625'): '\uFEF9',
}

_DIACRITICS = set(
    '\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0670\u0640'
)

_RTL_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)


def is_rtl(ch: str) -> bool:
    cp = ord(ch)
    return any(a <= cp <= b for a, b in _RTL_RANGES)


def _joins_prev(text: str, i: int) -> bool:
    if i <= 0:
        return False
    sp = _SHAPES.get(text[i - 1])
    return bool(sp and sp[2])


def _joins_next(text: str, i: int) -> bool:
    n = len(text)
    if i + 1 >= n:
        return False
    nx = text[i + 1]
    if nx in _SHAPES:
        # letters that can join to the right
        return _SHAPES[nx][2] is not None or nx in ('\u0627', '\u0622', '\u0623', '\u0625')
    if nx == '\u0644' and i + 2 < n and text[i + 2] in ('\u0627', '\u0622', '\u0623', '\u0625'):
        return True
    return False


def shape(text: str) -> str:
    """Convert logical Arabic/Persian to presentation forms."""
    text = ''.join(c for c in text if c not in _DIACRITICS)
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        # lam-alef ligature
        if i + 1 < n and (ch, text[i + 1]) in _LAM_ALEF:
            lig = _LAM_ALEF[(ch, text[i + 1])]
            out.append(chr(ord(lig) + 1) if _joins_prev(text, i) else lig)
            i += 2
            continue
        sp = _SHAPES.get(ch)
        if not sp:
            out.append(ch)
            i += 1
            continue
        iso, fin, ini, med = sp
        pj = _joins_prev(text, i)
        nj = _joins_next(text, i)
        if pj and nj and med:
            out.append(med)
        elif pj and fin:
            out.append(fin)
        elif nj and ini:
            out.append(ini)
        else:
            out.append(iso)
        i += 1
    return ''.join(out)


def visual(text: str) -> str:
    """Shape + reorder RTL runs for terminals that don't do BiDi.
    Non-RTL (Latin, digits, punctuation) segments are kept in order."""
    if not text or not any(is_rtl(c) for c in text):
        return text
    text = shape(text)
    tokens = []
    i, n = 0, len(text)
    while i < n:
        rtl = is_rtl(text[i])
        j = i
        while j < n and is_rtl(text[j]) == rtl:
            j += 1
        tokens.append((rtl, text[i:j]))
        i = j
    # reverse whole sequence, reverse RTL runs internally
    return ''.join(s[::-1] if rtl else s for rtl, s in reversed(tokens))
