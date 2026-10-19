"""Scope-local Okapi BM25; jieba tokenization is shared by query and corpus.

Scores use positive Robertson IDF, k1=1.2, b=0.75. Corpus statistics must
only include authorized current chunks. This exact implementation scans that
corpus per request; it is not a persistent inverted index for large datasets.
"""
import math
from collections import Counter

import jieba


def tokenize(text):
    return [word.casefold() for word in jieba.cut(text)
            if any(char.isalnum() for char in word)]


def bm25_rank(question, documents, limit, k1=1.2, b=0.75):
    if limit <= 0:
        return []
    terms = set(tokenize(question))
    if not terms:
        return []
    corpus = [(identity, Counter(tokenize(body))) for identity, body in documents]
    if not corpus:
        return []
    lengths = [sum(counts.values()) for _, counts in corpus]
    average = sum(lengths) / len(corpus)
    if not average:
        return []
    frequencies = Counter()
    for _, counts in corpus:
        frequencies.update(terms.intersection(counts))
    idf = {term: math.log1p((len(corpus) - count + 0.5) / (count + 0.5))
           for term, count in frequencies.items()}
    hits = []
    for (identity, counts), length in zip(corpus, lengths):
        normalization = k1 * (1 - b + b * length / average)
        score = sum(idf[term] * counts[term] * (k1 + 1) / (counts[term] + normalization)
                    for term in sorted(terms.intersection(counts)))
        if score > 0:
            hits.append((identity, score))
    return sorted(hits, key=lambda hit: (-hit[1], str(hit[0])))[:limit]
