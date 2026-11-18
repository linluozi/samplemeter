"""500 distinct inputs, one happy path: diversity without branch coverage."""

import os

import collapsed_lib

N = int(os.environ.get("SM_N", "500"))
docs = [{"name": f"item-{i}", "payload": {"index": i}} for i in range(N)]
accepted = sum(1 for doc in docs if collapsed_lib.validate(doc))

print(f"documents: {len(docs)}, accepted: {accepted}")
