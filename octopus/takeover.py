"""D74 (owner 2026-10-25, 方案一「接手」): a Session that runs on someone else's own boundary, taken over to one's own.
What moves between the two Workers: the Session's branch with its whole history (D58's full bundle) and the files of its
attempts the next tasks read (handoffs, outcomes) — one tar the new boundary adopts as the same workspace."""
import io
import re
import tarfile

ATTEMPT = re.compile(r"attempt-[a-f0-9]{32}")
# What a later task, a review or a diagnosis reads of an earlier attempt; the old Gateway's ids (dispatch.json) stay behind
FILES = ("handoff.md", "outcome.json", "decision.json", "instructions.md", "context.json")
LIMIT = 20 * 1024 * 1024   # the attempt files of one Session


def pack(bundle, files):
    """The tar a takeover sends to the new boundary: ``session.bundle`` and ``attempts/<attempt>/<file>``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in (("session.bundle", bundle), *((f"attempts/{k}", v.encode()) for k, v in sorted(files.items()))):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def unpack(data):
    """The bundle and the attempt files of a takeover's tar, or ValueError: regular files only, ``session.bundle`` once and
    ``attempts/<attempt>/<one of FILES>`` — never a path elsewhere, a link or anything the old boundary should keep."""
    bundle, files = None, {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                if not member.isreg():
                    raise ValueError("接手的内容只能是普通文件")
                if member.name == "session.bundle" and bundle is None:
                    bundle = archive.extractfile(member).read()
                    continue
                parts = member.name.split("/")
                if len(parts) != 3 or parts[0] != "attempts" or not ATTEMPT.fullmatch(parts[1]) or parts[2] not in FILES \
                        or member.name[len("attempts/"):] in files:
                    raise ValueError("接手的内容含有不认识的文件：" + member.name[:120])
                files[member.name[len("attempts/"):]] = archive.extractfile(member).read()
    except tarfile.TarError as exc:
        raise ValueError("接手的内容不是有效的归档") from exc
    if bundle is None:
        raise ValueError("接手的内容缺少会话分支")
    return bundle, files
