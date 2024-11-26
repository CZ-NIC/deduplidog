from deduplidog import Deduplidog
from deduplidog.deduplidog import Action, Execution, Match, Media, Helper


def drun(action=None, execution=None, match=None, media=None, helper=None, **kw):
    def _(l: list | dict):
        if isinstance(l, list):
            return {k: True for k in l}
        return l
    return Deduplidog(Action(**_(action or [])),
                      Execution(**_(execution or [])),
                      Match(**_(match or [])),
                      Media(**_(media or [])),
                      Helper(**_(helper or [])),
                      **kw).start()
