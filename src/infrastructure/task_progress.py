"""Pure, bounded parsing of Task candidate progress metadata."""

from __future__ import annotations

from typing import Any, TypeGuard

from ..domain.task_progress import TaskActivity, TaskProgress


MAX_ACTIVITIES = 50
MAX_DEPTH = 4
MAX_LABEL_CHARS = 256
MAX_ID_CHARS = 256


def _id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_ID_CHARS


def _field(value: list[Any], index: int) -> Any:
    return value[index] if index < len(value) else None


def _label(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, str):
        raise ValueError('Invalid label')
    return value[:MAX_LABEL_CHARS] or None, len(value) > MAX_LABEL_CHARS


def parse_task_progress(candidate: Any, *, chat_id: str, response_id: str,
                        candidate_id: str) -> TaskProgress | None:
    """Extract only labels and counts from the exact candidate; never return output nodes."""
    if not all(_id(v) for v in (chat_id, response_id, candidate_id)):
        raise ValueError('Incomplete response identity')
    if not isinstance(candidate, list) or not candidate or candidate[0] != candidate_id:
        raise ValueError('Candidate identity mismatch')
    activities: list[TaskActivity] = []
    truncated = False
    observed = False
    activity_group = _field(candidate, 37)
    if activity_group is not None:
        if not isinstance(activity_group, list):
            raise ValueError('Invalid activity group')
        rows = _field(activity_group, 1)
        if rows is not None and not isinstance(rows, list):
            raise ValueError('Invalid activity rows')
        observed = True
        for row in rows or []:
            if len(activities) >= MAX_ACTIVITIES:
                truncated = True
                break
            if not isinstance(row, list):
                raise ValueError('Invalid activity row')
            text_object = _field(row, 0)
            if text_object is not None and not isinstance(text_object, list):
                raise ValueError('Invalid activity label')
            label, cut1 = _label(_field(text_object, 0) if text_object else None)
            tool, cut2 = _label(_field(row, 2))
            output = _field(row, 4)
            if output is not None and not isinstance(output, list):
                raise ValueError('Invalid structured output')
            nodes = _field(output, 0) if output else None
            if nodes is not None and not isinstance(nodes, list):
                raise ValueError('Invalid output nodes')
            sources = _field(row, 7)
            if sources is not None and not isinstance(sources, list):
                raise ValueError('Invalid source list')
            activities.append(TaskActivity(label, tool, len(nodes or []), len(sources or [])))
            truncated |= cut1 or cut2

    legacy = _field(candidate, 45)
    if legacy is not None:
        if not isinstance(legacy, list):
            raise ValueError('Invalid legacy progress')
        tree = _field(legacy, 2)
        if tree is not None:
            if not isinstance(tree, list):
                raise ValueError('Invalid legacy tree')
            roots = _field(tree, 0)
            if roots is not None and not isinstance(roots, list):
                raise ValueError('Invalid legacy roots')
            observed = True
            root_nodes = roots or []
            if len(root_nodes) > MAX_ACTIVITIES:
                truncated = True
            stack = [(node, 0) for node in reversed(root_nodes[:MAX_ACTIVITIES])]
            while stack:
                if len(activities) >= MAX_ACTIVITIES:
                    truncated = True
                    break
                node, depth = stack.pop()
                if not isinstance(node, list):
                    raise ValueError('Invalid legacy step')
                label, cut = _label(_field(node, 1))
                children = _field(node, 7)
                if children is not None and not isinstance(children, list):
                    raise ValueError('Invalid legacy children')
                activities.append(TaskActivity(label, None, 0, 0, depth))
                truncated |= cut
                if children and depth >= MAX_DEPTH:
                    truncated = True
                elif children:
                    remaining = MAX_ACTIVITIES - len(activities) - len(stack)
                    if len(children) > remaining:
                        truncated = True
                    stack.extend((child, depth + 1) for child in reversed(children[:max(0, remaining)]))
    if not observed:
        return None
    return TaskProgress(chat_id, response_id, candidate_id, tuple(activities), 'exact_candidate_observed', truncated)
