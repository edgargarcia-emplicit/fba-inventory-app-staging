"""
diffing.py — Word-level diff highlighting and similarity scoring.

Ports the exact comparison + highlighting logic from the WordPress plugin
(FLM_CrossRef::compare / FLM_QA::compare, and the buildDiffHtml() JS):

  - Exact match (case/whitespace-insensitive) => pass
  - Target field empty => missing
  - Otherwise => fail, with a word-level diff:
      - words in the source but not the target => red strikethrough
      - words in the target but not the source => yellow highlight
    plus a similarity percentage (Python's difflib as the equivalent of
    PHP's similar_text()).
"""

import difflib
import html
import re


def strings_match(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())
    return norm(a) == norm(b)


def similarity(a: str, b: str) -> int:
    """Similarity percentage, 0-100 — matches PHP's similar_text() closely enough
    for this purpose (both are longest-common-substring-based measures)."""
    if not a and not b:
        return 100
    return round(difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)


def compare_field(source: str, target: str) -> dict:
    """
    Compare one field's expected value (source) against the found value (target).
    Returns {'result': 'pass'|'fail'|'missing', 'similarity': int}.
    Caller skips fields where source is empty (nothing to check).
    """
    source = html.unescape((source or "").strip())
    target = html.unescape((target or "").strip())

    if not target:
        return {"result": "missing", "similarity": 0}
    if strings_match(source, target):
        return {"result": "pass", "similarity": 100}
    return {"result": "fail", "similarity": similarity(source, target)}


def _tokenize(s: str) -> set:
    words = re.split(r"\s+", s)
    return {re.sub(r"[^a-z0-9]", "", w.lower()) for w in words if re.sub(r"[^a-z0-9]", "", w.lower())}


def build_diff_html(source: str, target: str) -> dict:
    """
    Build word-level diff HTML for a failed comparison.
    Returns {'source_html': ..., 'target_html': ...} — source words missing
    from target get a red strikethrough mark; target words not in source get
    a yellow highlight mark. Matches the WordPress buildDiffHtml() exactly.
    """
    source = source or ""
    target = target or ""
    src_tokens = _tokenize(source)
    tgt_tokens = _tokenize(target)

    def esc(s):
        return html.escape(s, quote=True)

    src_html_parts = []
    for word in source.split():
        clean = re.sub(r"[^a-z0-9]", "", word.lower())
        if not clean or clean in tgt_tokens:
            src_html_parts.append(esc(word))
        else:
            src_html_parts.append(
                f'<mark style="background:#fee2e2;border-radius:2px;padding:0 2px;'
                f'text-decoration:line-through;color:#991b1b;">{esc(word)}</mark>'
            )

    tgt_html_parts = []
    for word in target.split():
        clean = re.sub(r"[^a-z0-9]", "", word.lower())
        if not clean or clean in src_tokens:
            tgt_html_parts.append(esc(word))
        else:
            tgt_html_parts.append(
                f'<mark style="background:#fef3c7;border-radius:2px;padding:0 2px;">{esc(word)}</mark>'
            )

    return {"source_html": " ".join(src_html_parts), "target_html": " ".join(tgt_html_parts)}


def is_parent_sku(sku: str) -> bool:
    """Matches the WP plugin's parent-SKU detection: ends in -P, -P1, _Parent, etc."""
    if not sku:
        return False
    return bool(re.search(r"[-_\s]P(\d+)?$", sku, re.I)) or bool(re.search(r"[-_\s]?Parent$", sku, re.I))


def skus_are_all_parents(skus: list) -> bool:
    if not skus:
        return False
    return all(is_parent_sku(s) for s in skus)
