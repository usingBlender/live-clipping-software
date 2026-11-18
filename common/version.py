__version__ = "0.3.0"


def parse(v: str) -> tuple:
    v = v.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts + [0] * (3 - len(parts)))
