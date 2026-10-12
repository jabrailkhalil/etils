"""Author-only verification of exact source and baseline failure identities."""

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = "d18f006162a8da2b7eb79de2e1e1cac0d07774c5"
HEAD = "7875132c8a7567ad71035704081d1c9a210353fc"
SOURCE = "etils/epy/lazy_imports_utils.py"
TEST = "etils/epy/lazy_imports_scope_test.py"
role = sys.argv[1]
assert role in ("head", "base")


def git(*args):
    return subprocess.check_output(["git", *args])


def run(*args, expected=0):
    result = subprocess.run([sys.executable, *args], check=False)
    assert result.returncode == expected, (args, result.returncode, expected)


assert set(git("diff", "--name-only", BASE, HEAD).decode().splitlines()) == {SOURCE, TEST}
for path in (SOURCE, TEST):
    assert Path(path).read_bytes() == git("show", f"{HEAD}:{path}"), path
original_nodes = {
    node.name: ast.dump(node, include_attributes=False)
    for node in ast.parse(git("show", f"{BASE}:{SOURCE}")).body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name != "lazy_imports"
}
new_nodes = {
    node.name: ast.dump(node, include_attributes=False)
    for node in ast.parse(Path(SOURCE).read_bytes()).body
    if isinstance(node, (ast.ClassDef, ast.FunctionDef))
}
assert all(new_nodes[name] == body for name, body in original_nodes.items())
if role == "base":
    # Ephemeral CI negative control: replace only the one production source file.
    Path(SOURCE).write_bytes(git("show", f"{BASE}:{SOURCE}"))
source_sha = BASE if role == "base" else HEAD
assert Path(SOURCE).read_bytes() == git("show", f"{source_sha}:{SOURCE}")
run("-m", "pytest", "etils/epy", "-q", "--tb=short",
    "--junitxml=author-etils-tests.xml", expected=0 if role == "head" else 1)
root = ET.parse("author-etils-tests.xml").getroot()
cases = list(root.iter("testcase"))
assert len(cases) == 64
assert not any(case.find("error") is not None or case.find("skipped") is not None for case in cases)
failed = sorted(case.attrib["name"] for case in cases if case.find("failure") is not None)
expected_failures = sorted([
    "test_called_function_import_is_eager",
    "test_called_function_relative_import_is_eager",
    "test_called_function_keyword_import_is_eager",
    "test_direct_keyword_import_is_lazy",
    "test_other_thread_import_is_eager[_plain_import]",
    "test_other_thread_import_is_eager[_relative_import]",
    "test_gc_finalizer_import_is_eager[_plain_import]",
    "test_gc_finalizer_import_is_eager[_relative_import]",
    "test_overlapping_thread_scopes[True]",
    "test_existing_import_hook_is_preserved",
])
assert failed == (expected_failures if role == "base" else [])
if sys.platform == "linux":
    run("author_validation_etils_helpers.py", "semaphore", "--expected", role)
if role == "head":
    run("author_validation_etils_helpers.py", "stress", "--repetitions", "100")
    os.environ["ETILS_VALIDATION_OUT"] = "."
    for kind in ("foreign-scope", "early-restore"):
        run("author_validation_etils_helpers.py", "mutate", "--kind", kind)
    run("-m", "pyink", "--target-version", "py311", "--check", SOURCE, TEST)
proof = {
    "role": role, "python": sys.version, "platform": sys.platform,
    "source_commit": source_sha, "test_commit": HEAD,
    "source_sha256": hashlib.sha256(Path(SOURCE).read_bytes()).hexdigest(),
    "test_sha256": hashlib.sha256(Path(TEST).read_bytes()).hexdigest(),
    "case_count": len(cases), "passed": len(cases) - len(failed),
    "expected_failures": failed, "skipped": 0,
    "unchanged_preexisting_class_function_count": len(original_nodes),
    "linux_semaphore_check": "executed" if sys.platform == "linux" else "not applicable",
    "head_stress_and_mutations": "executed" if role == "head" else "not applicable",
}
Path("author-etils-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
print("ETILS_SOURCE_PROOF=" + json.dumps(proof, sort_keys=True))
