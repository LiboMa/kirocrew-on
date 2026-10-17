"""What a Worker from before D37 answers for the candidate and check routes: 404, so the Engine skips those gates.
Fakes in older tests call it first, keeping the behaviour they test unchanged on sessions of the third contract."""
from octopus.security import Error

D37_PATHS = frozenset({"/candidate", "/verify/start", "/verify/status"})


def as_old_worker(path):
    if path in D37_PATHS:
        raise Error(404, "not_found", "接口不存在")
