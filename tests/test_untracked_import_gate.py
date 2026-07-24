"""scripts/check_untracked_imports.py — untracked-import 게이트 검사기 테스트.

시나리오 원형: 2026-07-24 프로덕션 500 (web.py가 untracked regulation.py를 import).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_untracked_imports import find_untracked_imports  # noqa: E402


def _make(repo: Path, rel: str, content: str = "") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_incident_class_relative_import_untracked(tmp_path):
    """`from . import regulation` + src/regulation.py untracked -> 위반."""
    _make(tmp_path, "src/web.py", "from . import regulation\n")
    _make(tmp_path, "src/regulation.py", "X = 1\n")
    known = {"src/web.py"}  # regulation.py는 tracked도 staged도 아님
    violations = find_untracked_imports(tmp_path, ["src/web.py"], known)
    assert len(violations) == 1
    assert "src/regulation.py" in violations[0]


def test_tracked_module_passes(tmp_path):
    _make(tmp_path, "src/web.py", "from .models import AuctionListing\n")
    _make(tmp_path, "src/models.py", "class AuctionListing: ...\n")
    known = {"src/web.py", "src/models.py"}
    assert find_untracked_imports(tmp_path, ["src/web.py"], known) == []


def test_staged_new_module_passes(tmp_path):
    """신규 모듈이라도 같이 staged면 통과 (원자적 커밋)."""
    _make(tmp_path, "src/web.py", "from . import regulation\n")
    _make(tmp_path, "src/regulation.py", "X = 1\n")
    known = {"src/web.py", "src/regulation.py"}  # staged에 포함
    assert find_untracked_imports(tmp_path, ["src/web.py"], known) == []


def test_absolute_and_plain_import_forms(tmp_path):
    """`from src import x`, `import src.y` 절대형도 검출."""
    _make(
        tmp_path,
        "deploy/job.py",
        "from src import helper_a\nimport src.helper_b\n",
    )
    _make(tmp_path, "src/helper_a.py", "")
    _make(tmp_path, "src/helper_b.py", "")
    known = {"deploy/job.py"}
    violations = find_untracked_imports(tmp_path, ["deploy/job.py"], known)
    joined = "\n".join(violations)
    assert "src/helper_a.py" in joined
    assert "src/helper_b.py" in joined
    assert len(violations) == 2


def test_nonexistent_and_thirdparty_ignored(tmp_path):
    """디스크에 없는 모듈(서드파티/오타)은 이 게이트의 소관 아님."""
    _make(tmp_path, "src/web.py", "import flask\nfrom .ghost import x\n")
    known = {"src/web.py"}
    assert find_untracked_imports(tmp_path, ["src/web.py"], known) == []


def test_optional_try_import_ignored(tmp_path):
    """try/except ImportError로 감싼 선택적 import는 제외."""
    _make(
        tmp_path,
        "src/web.py",
        "try:\n    from . import local_only\nexcept ImportError:\n    local_only = None\n",
    )
    _make(tmp_path, "src/local_only.py", "")
    known = {"src/web.py"}
    assert find_untracked_imports(tmp_path, ["src/web.py"], known) == []


def test_deleted_staged_file_skipped(tmp_path):
    """삭제 staged(디스크에 없음)는 파싱 시도 없이 건너뜀."""
    known = {"src/gone.py"}
    assert find_untracked_imports(tmp_path, ["src/gone.py"], known) == []


def test_bom_file_still_checked(tmp_path):
    """UTF-8 BOM 파일도 파싱 실패 없이 검사 (BOM->SyntaxError 무력화 회귀 방지)."""
    p = tmp_path / "src" / "web.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes("from . import regulation\n".encode("utf-8-sig"))
    _make(tmp_path, "src/regulation.py", "X = 1\n")
    known = {"src/web.py"}
    violations = find_untracked_imports(tmp_path, ["src/web.py"], known)
    assert len(violations) == 1


def test_from_pkg_import_submodule_alias(tmp_path):
    """`from src import a, b` 중 하나만 untracked여도 그 건만 위반."""
    _make(tmp_path, "run.py", "from src import store, brand_new\n")
    _make(tmp_path, "src/store.py", "")
    _make(tmp_path, "src/brand_new.py", "")
    known = {"run.py", "src/store.py"}
    violations = find_untracked_imports(tmp_path, ["run.py"], known)
    assert len(violations) == 1
    assert "src/brand_new.py" in violations[0]
