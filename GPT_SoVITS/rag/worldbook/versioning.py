"""世界书包使用的受限 SemVer 解析与比较。"""

from __future__ import annotations

import re
from dataclasses import dataclass


_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_COMPARATOR_PATTERN = re.compile(r"^(==|>=|<=|>|<)(.+)$")


@dataclass(frozen=True, slots=True)
class SemVer:
    """保存足以执行世界书依赖比较的 SemVer。"""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    def __lt__(self, other: "SemVer") -> bool:
        """按 SemVer 优先级判断当前版本是否较低。"""

        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        return _prerelease_is_lower(self.prerelease, other.prerelease)

    def __le__(self, other: "SemVer") -> bool:
        """按 SemVer 优先级判断当前版本是否不高于目标。"""

        return self == other or self < other

    def __gt__(self, other: "SemVer") -> bool:
        """按 SemVer 优先级判断当前版本是否较高。"""

        return not self <= other

    def __ge__(self, other: "SemVer") -> bool:
        """按 SemVer 优先级判断当前版本是否不低于目标。"""

        return not self < other


def parse_semver(value: str) -> SemVer:
    """解析完整 SemVer，忽略不影响优先级的构建元数据。"""

    match = _SEMVER_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"不是有效的 SemVer: {value!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    for identifier in prerelease:
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise ValueError(f"SemVer 预发布数字标识不得包含前导零: {value!r}")
    return SemVer(
        major=int(match.group(1)),
        minor=int(match.group(2)),
        patch=int(match.group(3)),
        prerelease=prerelease,
    )


def validate_version_spec(value: str) -> str:
    """校验仅由逗号连接显式比较器组成的版本约束。"""

    normalized = value.strip()
    if not normalized:
        raise ValueError("version_spec 不能为空")
    for raw_comparator in normalized.split(","):
        comparator = raw_comparator.strip()
        match = _COMPARATOR_PATTERN.fullmatch(comparator)
        if match is None:
            raise ValueError(f"不支持的 SemVer 比较器: {comparator!r}")
        parse_semver(match.group(2).strip())
    return ",".join(item.strip() for item in normalized.split(","))


def version_satisfies(version: str, version_spec: str) -> bool:
    """判断版本是否满足全部显式比较器。"""

    candidate = parse_semver(version)
    normalized_spec = validate_version_spec(version_spec)
    for comparator in normalized_spec.split(","):
        match = _COMPARATOR_PATTERN.fullmatch(comparator)
        if match is None:
            raise ValueError(f"不支持的 SemVer 比较器: {comparator!r}")
        operator = match.group(1)
        boundary = parse_semver(match.group(2).strip())
        if operator == "==" and candidate != boundary:
            return False
        if operator == ">=" and candidate < boundary:
            return False
        if operator == "<=" and candidate > boundary:
            return False
        if operator == ">" and candidate <= boundary:
            return False
        if operator == "<" and candidate >= boundary:
            return False
    return True


def _prerelease_is_lower(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """比较两个相同核心版本的预发布标识。"""

    if not left:
        return False
    if not right:
        return True
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return int(left_item) < int(right_item)
        if left_numeric != right_numeric:
            return left_numeric
        return left_item < right_item
    return len(left) < len(right)
