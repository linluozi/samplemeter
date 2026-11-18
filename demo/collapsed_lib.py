"""A validator whose 500 distinct documents all take the same branch."""


def validate(doc):
    if not isinstance(doc, dict):
        return False
    if "name" not in doc:
        return False
    return isinstance(doc["name"], str) and len(doc["name"]) > 0
