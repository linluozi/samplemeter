"""The same workload with one RNG created once and threaded through."""

import random
from urllib.parse import urljoin


def make_rng(seed=4321):
    return random.Random(seed)


def one_doc(rng):
    count = rng.randint(1, 3)
    base = "https://example.com/" + rng.choice(["a", "b"]) + "/"
    items = []
    for i in range(count):
        if rng.random() < 0.5:
            items.append({"link": "../post/%d?token=%d" % (i, rng.randrange(10**6))})
        else:
            items.append({"link": "https://cdn.example.com/%d?token=%d" % (i, rng.randrange(10**6))})
    return {"base": base, "items": items}


def resolve(doc):
    base = doc["base"]
    out = []
    for item in doc["items"]:
        link = item.get("link")
        if link is None:
            out.append(None)
        elif link.startswith("http://") or link.startswith("https://"):
            out.append(link)
        else:
            out.append(urljoin(base, link))
    return out
