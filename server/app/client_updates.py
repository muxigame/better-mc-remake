"""Client support policy. No network calls; shared by API and release tooling."""
from __future__ import annotations

import re
from typing import Any

_VERSION = re.compile(
    r"^[vV]?(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?(?:\.(0|[1-9][0-9]*))?"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def version_key(value: str) -> tuple:
    """SemVer ordering (build metadata ignored); accepts legacy 1.0 versions."""
    match = _VERSION.fullmatch(value)
    if not match:
        raise ValueError(f"无效的客户端版本号：{value}")
    major, minor, patch, pre = match.groups()
    identifiers = []
    for part in pre.split(".") if pre else []:
        if part.isdigit():
            if len(part) > 1 and part.startswith("0"):
                raise ValueError(f"预发布版本号不能包含前导零：{value}")
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part))
    return (int(major), int(minor or 0), int(patch or 0), 1 if pre is None else 0, tuple(identifiers))


def validate_policy(policy: dict, latest: str) -> dict[str, Any]:
    """Refuse policies that would force users toward an unsupported target."""
    target = version_key(latest)
    minimum = policy.get("minSupportedVersion")
    blocked = policy.get("blockedVersions", [])
    reason = policy.get("reason", policy.get("updateReason", ""))
    if minimum is not None and (not isinstance(minimum, str) or version_key(minimum) > target):
        raise ValueError("最低受支持版本不能高于当前发布版本")
    if not isinstance(blocked, list) or any(not isinstance(v, str) for v in blocked):
        raise ValueError("blockedVersions 必须是版本号数组")
    if not isinstance(reason, str) or len(reason) > 500:
        raise ValueError("更新原因最多为 500 字符")
    seen = set()
    versions = []
    for version in blocked:
        key = version_key(version)
        if key >= target:
            raise ValueError("只能停用低于当前发布版本的旧版本")
        if key not in seen:
            seen.add(key)
            versions.append(version)
    return {"minSupportedVersion": minimum, "blockedVersions": versions, "updateReason": reason}


def update_required(release: dict, current: str) -> bool:
    current_key = version_key(current)
    if version_key(str(release["version"])) <= current_key:
        return False  # No forced downgrade, same-version reinstall or update loop.
    minimum = release.get("minSupportedVersion")
    return (
        release.get("mandatory") is True
        or bool(minimum and current_key < version_key(str(minimum)))
        or any(current_key == version_key(v) for v in release.get("blockedVersions", []))
    )
