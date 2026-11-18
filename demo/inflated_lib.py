"""A generator that resets its seed on every call -- the shape of the 11-01 bug."""

import random
from urllib.parse import urljoin


def one_doc():
    random.seed(4321)  # <- inside the function, so every call returns the same doc
    count = random.randint(1, 3)
    base = "https://example.com/" + random.choice(["a", "b"]) + "/"
    items = []
    for i in range(count):
        if random.random() < 0.5:
            items.append({"link": "../post/%d" % i})
        else:
            items.append({"link": "https://cdn.example.com/%d" % i})
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
