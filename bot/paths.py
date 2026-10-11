"""Where runtime files live. CZB_HOME overrides; default D:/czb on the original laptop, else ./runtime."""
import os

HOME = os.environ.get("CZB_HOME") or ("D:/czb" if os.path.isdir("D:/czb") else os.path.abspath("runtime"))


def p(*parts):
    return os.path.join(HOME, *parts).replace("\\", "/")
