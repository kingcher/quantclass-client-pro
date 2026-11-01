"""权限检查,对应 src/shared/lib/permission.ts。

语义:参数之间是 OR,数组内部是 AND。
"""

from typing import Sequence


def _match(permissions: list[str], condition) -> bool:
    if isinstance(condition, str):
        return condition in permissions
    if isinstance(condition, (list, tuple)):
        return all(c in permissions for c in condition)
    if isinstance(condition, dict):
        conds = condition.get("conditions", [])
        if condition.get("method") == "AND":
            return all(c in permissions for c in conds)
        return any(c in permissions for c in conds)
    return False


def check_permission(permissions: Sequence[str] | None, *conditions) -> bool:
    """参数之间 OR:check_permission(perms, "isMember", "isStock")。"""
    perms = list(permissions or [])
    return any(_match(perms, cond) for cond in conditions)


def get_select_kernel(permissions: Sequence[str] | None) -> str:
    """isMember 用 fusion 内核,否则 aqua。"""
    return "fusion" if check_permission(permissions, "isMember") else "aqua"
