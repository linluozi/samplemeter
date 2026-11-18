"""Parse a real PEP 508 requirement line with packaging."""

from packaging.requirements import Requirement


def parse_requirement(line):
    req = Requirement(line)
    extras = tuple(sorted(req.extras))
    specifiers = tuple(sorted(str(s) for s in req.specifier))
    return (req.name.lower(), extras, specifiers, req.marker is not None)
