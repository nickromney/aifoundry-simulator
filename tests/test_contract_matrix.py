"""Contract coverage gate.

Keeps contracts/contract_matrix.yml and the pytest ``contract`` markers in
lockstep: every matrix entry must be owned by at least one real test, and
every marker used in a test must exist in the matrix. The same discipline as
the sibling APIM simulator's contract gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "contracts" / "contract_matrix.yml"


def _load_matrix() -> list[dict]:
    document = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    return document["contracts"]


def _collect_marker_ids_and_tests() -> tuple[set[str], dict[str, set[str]]]:
    marker_ids: set[str] = set()
    tests_by_file: dict[str, set[str]] = {}
    for test_file in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
        function_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            function_names.add(node.name)
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "contract"
                ):
                    for argument in decorator.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            marker_ids.add(argument.value)
        tests_by_file[f"tests/{test_file.name}"] = function_names
    return marker_ids, tests_by_file


def test_matrix_and_markers_stay_in_lockstep():
    contracts = _load_matrix()
    marker_ids, tests_by_file = _collect_marker_ids_and_tests()
    matrix_ids = {contract["id"] for contract in contracts}

    unowned = matrix_ids - marker_ids
    assert not unowned, f"contract matrix entries without an owning test marker: {sorted(unowned)}"

    unknown = marker_ids - matrix_ids
    assert not unknown, f"test markers missing from the contract matrix: {sorted(unknown)}"


def test_owner_tests_reference_real_functions():
    contracts = _load_matrix()
    _, tests_by_file = _collect_marker_ids_and_tests()
    for contract in contracts:
        for owner in contract.get("owner_tests", []):
            file_part, _, function_part = owner.partition("::")
            assert file_part in tests_by_file, f"{contract['id']}: unknown test file {file_part}"
            assert function_part in tests_by_file[file_part], (
                f"{contract['id']}: {file_part} has no test named {function_part}"
            )


def test_matrix_entries_are_well_formed():
    for contract in _load_matrix():
        assert contract["status"] in ("supported", "adapted"), contract["id"]
        assert contract["surface"], contract["id"]
        assert contract["module"], contract["id"]
        assert contract["owner_tests"], contract["id"]
        module_path = REPO_ROOT / contract["module"]
        assert module_path.exists(), f"{contract['id']}: module {contract['module']} does not exist"
