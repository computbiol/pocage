from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


IGNORED_DIR_NAMES = {
    ".git",
    "node_modules",
    ".pixi",
    ".local",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    "data",
    "logs",
    ".data",
    ".logs",
}
IGNORED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".tsbuildinfo",
}


@dataclass(slots=True)
class ContextCandidate:
    kind: str
    name: str
    relative_path: str
    uri: str


def _is_subsequence(needle: str, haystack: str) -> bool:
    index = 0
    for char in haystack:
        if index < len(needle) and needle[index] == char:
            index += 1
        if index == len(needle):
            return True
    return index == len(needle)


def _score_candidate(query: str, name: str, relative_path: str) -> tuple[int, int, int] | None:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return None

    normalized_name = name.lower()
    normalized_path = relative_path.lower()

    if normalized_name.startswith(normalized_query):
        return (0, len(relative_path), len(name))
    if normalized_name.find(normalized_query) >= 0:
        return (1, len(relative_path), len(name))
    if normalized_path.startswith(normalized_query):
        return (2, len(relative_path), len(name))
    if normalized_path.find(normalized_query) >= 0:
        return (3, len(relative_path), len(name))
    if _is_subsequence(normalized_query, normalized_name):
        return (4, len(relative_path), len(name))
    if _is_subsequence(normalized_query, normalized_path):
        return (5, len(relative_path), len(name))
    return None


def search_context_candidates(cwd: str, query: str, *, limit: int) -> list[ContextCandidate]:
    root = Path(cwd).expanduser()
    if not root.exists() or not root.is_dir():
        return []

    normalized_query = query.strip()
    if not normalized_query:
        return []

    scored: list[tuple[tuple[int, int, int], ContextCandidate]] = []
    for current_root_raw, dirnames, filenames in os.walk(root, topdown=True):
        current_root = Path(current_root_raw)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in IGNORED_DIR_NAMES
            and not name.endswith(".egg-info")
            and not (current_root / name).is_symlink()
        )

        if current_root != root:
            relative_dir = current_root.relative_to(root).as_posix()
            dir_score = _score_candidate(normalized_query, current_root.name, relative_dir)
            if dir_score is not None:
                scored.append(
                    (
                        dir_score,
                        ContextCandidate(
                            kind="directory",
                            name=current_root.name,
                            relative_path=relative_dir,
                            uri=current_root.resolve().as_uri(),
                        ),
                    )
                )

        for filename in sorted(filenames):
            file_path = current_root / filename
            if file_path.is_symlink() or any(filename.endswith(suffix) for suffix in IGNORED_FILE_SUFFIXES):
                continue
            relative_path = file_path.relative_to(root).as_posix()
            file_score = _score_candidate(normalized_query, filename, relative_path)
            if file_score is None:
                continue
            scored.append(
                (
                    file_score,
                    ContextCandidate(
                        kind="file",
                        name=filename,
                        relative_path=relative_path,
                        uri=file_path.resolve().as_uri(),
                    ),
                )
            )

    scored.sort(key=lambda item: (item[0], item[1].relative_path))
    return [candidate for _, candidate in scored[:limit]]
