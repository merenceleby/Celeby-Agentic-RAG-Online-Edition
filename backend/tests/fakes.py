"""Testlerde gerçek model/Chroma yerine kullanılan sahte servisler."""
import math
import re
import zlib


def fake_embed(texts, dim=128):
    """Kelime torbası embedding: aynı kelimeleri paylaşan metinler birbirine yakın çıkar."""
    out = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"\w+", t.lower()):
            v[zlib.crc32(w.encode()) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


class FakeLLM:
    """verdicts: validate adımının sırayla döneceği supported değerleri."""

    def __init__(self, verdicts=(True,), question="What does the sample passage describe?"):
        self.verdicts = list(verdicts)
        self.question = question
        self.calls = 0
        self.last_prompt = ""

    async def generate_json(self, prompt, ticket, system=None, temperature=0.0):
        ticket.use()
        self.calls += 1
        if "alternative search queries" in prompt:
            return {"queries": ["a", "b", "c", "d"]}
        if "ONE question" in prompt:
            return {"question": self.question}
        return {"supported": self.verdicts.pop(0), "reason": "no evidence"} if self.verdicts else {"supported": True}

    async def generate(self, prompt, ticket, system=None, temperature=None):
        ticket.use()
        self.calls += 1
        self.last_prompt = prompt
        return " Voldemort is a dark wizard [1]. "

    async def stream(self, prompt, ticket, system=None, temperature=None):
        ticket.use()
        self.calls += 1
        for w in ("mer", "haba"):
            yield w


class FakeStore:
    def __init__(self, n=5, docs=None):
        self.n = n
        self.docs = docs or []
        self.added = []

    def count(self):
        return self.n

    def sources(self):
        return {}

    def all_documents(self):
        return self.docs

    def embed(self, texts):
        return fake_embed(texts)

    def reset(self):
        self.n = 0

    def delete_source(self, name):
        pass

    def add_pdf(self, path, on_progress=None):
        for i in (1, 2):
            if on_progress:
                on_progress(i, 2)
        self.added.append(path.name)
        self.n += 3
        return 3


class FakeBM25:
    def invalidate(self):
        pass


def make_doc(i=1, source="book.pdf", page=651, content="Voldemort keeps Nagini close by him", score=0.9):
    return {"id": f"{source}:{page}:{i}", "content": content, "metadata": {"source": source, "page": page}, "score": score}


class FakeRetrieval:
    def __init__(self, n=5, docs=None):
        self.store = FakeStore(n)
        self.docs = docs if docs is not None else [make_doc()]
        self.last_queries = None

    def hybrid(self, queries, original):
        self.last_queries = queries
        return self.docs

    def vector_only(self, query):
        return self.docs
