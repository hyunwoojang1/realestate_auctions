"""커밋 게이트: staged .py가 import하는 레포-로컬 모듈의 untracked 누락 검사.

2026-07-24 프로덕션 500 사고 클래스 재발 방지:
  web.py에 `from . import regulation`을 넣어 커밋했지만 src/regulation.py가
  untracked라 커밋에서 누락 -> 클린 체크아웃(Vercel) 부팅 즉사.
  로컬 워킹트리에는 파일이 있어 pytest/격리서버는 전부 통과하므로
  기존 게이트(ruff/pytest)로는 잡을 수 없는 클래스다.

규칙: staged .py가 import하는 모듈이 "디스크에 존재하지만 tracked도 staged도
아닌" 경우 위반. (디스크에 없으면 pytest가 잡고, tracked/staged면 안전.)
try/except ImportError 로 감싼 선택적 import는 제외한다.

사용: .venv 파이썬으로 실행, 위반 시 exit 1 (precommit_gate.ps1 step 4.5).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_OPTIONAL_EXC = {"ImportError", "ModuleNotFoundError", "Exception"}


def _optional_import_lines(tree: ast.AST) -> set[int]:
    """try/except(ImportError 계열)로 감싼 import 문의 라인 번호 집합."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import_error = False
        for handler in node.handlers:
            names: list[str] = []
            t = handler.type
            if t is None:
                catches_import_error = True
            elif isinstance(t, ast.Name):
                names = [t.id]
            elif isinstance(t, ast.Tuple):
                names = [e.id for e in t.elts if isinstance(e, ast.Name)]
            if any(n in _OPTIONAL_EXC for n in names):
                catches_import_error = True
        if not catches_import_error:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    lines.add(sub.lineno)
    return lines


def _candidates_for_parts(parts: list[str]) -> list[str]:
    """모듈 경로 파트 -> 레포 상대 후보 파일 경로(posix)."""
    if not parts:
        return []
    base = "/".join(parts)
    return [f"{base}.py", f"{base}/__init__.py"]


def _module_refs(py_file: str, tree: ast.AST) -> list[tuple[int, str, list[str]]]:
    """(라인, 표기, 후보경로들) 목록. 상대/절대 import 모두 해석."""
    refs: list[tuple[int, str, list[str]]] = []
    file_dir_parts = Path(py_file).parent.as_posix().split("/")
    if file_dir_parts == ["."]:
        file_dir_parts = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                refs.append((node.lineno, alias.name, _candidates_for_parts(parts)))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base_parts = node.module.split(".") if node.module else []
            else:
                # from .mod import X : level=1 -> 파일의 디렉터리 기준
                up = node.level - 1
                if up > len(file_dir_parts):
                    continue
                anchor = file_dir_parts[: len(file_dir_parts) - up]
                base_parts = anchor + (node.module.split(".") if node.module else [])
            label = ("." * node.level) + (node.module or "")
            # from X import a, b : a/b가 서브모듈 파일일 수도, 심볼일 수도 있다.
            cands = list(_candidates_for_parts(base_parts))
            for alias in node.names:
                if alias.name == "*":
                    continue
                cands.extend(_candidates_for_parts(base_parts + [alias.name]))
            refs.append((node.lineno, label, cands))
    return refs


def find_untracked_imports(
    repo_root: Path, staged_py: list[str], known: set[str]
) -> list[str]:
    """위반 메시지 목록. known = tracked ∪ staged (레포 상대 posix 경로)."""
    violations: list[str] = []
    for py_file in staged_py:
        path = repo_root / py_file
        if not path.is_file():
            continue  # 삭제 staged
        try:
            # utf-8-sig: BOM이 남은 파일도 파싱 (BOM이 str에 남으면 SyntaxError로
            # 조용히 건너뛰어 검사가 무력화된다 - 셀프테스트에서 실측)
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except SyntaxError:
            continue  # ruff/pytest가 잡는다
        optional = _optional_import_lines(tree)
        seen: set[str] = set()
        for lineno, label, cands in _module_refs(py_file, tree):
            if lineno in optional:
                continue
            for cand in cands:
                if cand in seen:
                    continue
                seen.add(cand)
                if cand in known:
                    continue
                if (repo_root / cand).is_file():
                    violations.append(
                        f"{py_file}:{lineno} import '{label}' -> {cand} "
                        "(디스크에 있지만 untracked+unstaged: 클린 체크아웃 부팅 실패)"
                    )
    return violations


def _git_lines(repo_root: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [line.strip().strip('"') for line in out.splitlines() if line.strip()]


def main() -> int:
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # 한글 레포 경로: 콘솔 cp949 기본값이면 디코드 실패
            check=True,
        ).stdout.strip()
    )
    staged = _git_lines(repo_root, "diff", "--cached", "--name-only")
    tracked = set(_git_lines(repo_root, "ls-files"))
    known = tracked | set(staged)
    staged_py = [f for f in staged if f.endswith(".py")]
    violations = find_untracked_imports(repo_root, staged_py, known)
    if violations:
        print("[untracked-import] 위반 발견:")
        for v in violations:
            print("  " + v)
        print("  -> 해당 모듈 파일을 함께 git add 하거나 import를 제거하세요.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
