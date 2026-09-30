"""Small, dependency-free TF-IDF similarity used by the rules engine."""
from __future__ import annotations

import math
import re
from collections import Counter

_STOP = set("""
a an and are as at be been but by can do does for from has have how i if in into is it its
me my no not of on or our per so such than that the their them then there these this to
under up we what when where which who will with you your any anything someone tell
client asks ask question again guideline documented do we us own
""".split())

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    for suffix in ("ings", "ing", "ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


class TfIdf:
    def __init__(self, documents: list[str]):
        self.docs = [tokens(d) for d in documents]
        df: Counter[str] = Counter()
        for doc in self.docs:
            df.update(set(doc))
        n = max(len(self.docs), 1)
        self.idf = {term: math.log((1 + n) / (1 + count)) + 1 for term, count in df.items()}
        self.vectors = [self.vectorize_tokens(d) for d in self.docs]

    def vectorize_tokens(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        vec = {t: c * self.idf.get(t, math.log(len(self.docs) + 1) + 1) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def vectorize(self, text: str) -> dict[str, float]:
        return self.vectorize_tokens(tokens(text))


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())
