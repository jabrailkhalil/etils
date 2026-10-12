"""Author-only stress, mutation and native Linux semaphore checks."""

import argparse
import builtins
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from etils import epy
from etils.epy import lazy_imports_utils as module


def stress(repetitions):
    from etils.epy import lazy_imports_scope_test as tests

    original = builtins.__import__
    for _ in range(repetitions):
        for order in (False, True):
            tests.test_overlapping_thread_scopes(order)
            assert builtins.__import__ is original
            assert not module._IMPORT_SCOPES
    print(json.dumps({"stress_ordered_scope_pairs_passed": repetitions * 2}))


def mutate(kind):
    import pytest

    if kind == "foreign-scope":
        def broken_import(name, globals_=None, locals_=None, fromlist=(), level=0):
            return module._IMPORT_SCOPES[-1].import_fn(
                name, globals_, locals_, fromlist, level
            )
        module._scoped_import = broken_import
    elif kind == "early-restore":
        class BrokenScopes(list):
            def remove(self, item):
                super().remove(item)
                builtins.__import__ = module._original_import
        module._IMPORT_SCOPES = BrokenScopes()
    else:
        raise AssertionError(kind)
    xml_path = Path(os.environ.get("ETILS_VALIDATION_OUT", "../gde-autonomous-2026-10-12")) / f"etils-mutant-{kind}.xml"
    result = pytest.main([
        "etils/epy/lazy_imports_scope_test.py", "-q", "--tb=short",
        f"--junitxml={xml_path}",
    ])
    assert result == 1, (kind, result)
    cases = list(ET.parse(xml_path).getroot().iter("testcase"))
    assert len(cases) == 21
    assert not any(case.find("error") is not None or case.find("skipped") is not None for case in cases)
    failures = {case.attrib["name"] for case in cases if case.find("failure") is not None}
    if kind == "early-restore":
        assert failures == {
            "test_overlapping_thread_scopes[False]",
            "test_overlapping_thread_scopes[True]",
            "test_nested_scope_callbacks",
        }, failures
    else:
        assert {
            "test_called_function_import_is_eager",
            "test_other_thread_import_is_eager[_plain_import]",
            "test_gc_finalizer_import_is_eager[_plain_import]",
            "test_overlapping_thread_scopes[True]",
        } <= failures, failures
    print(json.dumps({"mutation_rejected": kind, "failure_names": sorted(failures)}))


def semaphore(expected):
    assert sys.platform == "linux", "Native Linux named semaphore check only"
    import _multiprocessing
    import multiprocessing
    from multiprocessing import resource_tracker

    class Node:
        pass

    original = builtins.__import__
    gc_enabled = gc.isenabled()
    gc.disable()
    names = []
    try:
        for _ in range(4):
            holder = Node()
            holder.lock = multiprocessing.get_context("spawn").Lock()
            holder.cycle = holder
            names.append(holder.lock._semlock.name)
            del holder
        with epy.lazy_imports():
            gc.collect()
        remaining = [
            name for name in names
            if os.path.exists("/dev/shm/sem." + name.lstrip("/"))
        ]
    finally:
        builtins.__import__ = original
        # Negative-control cleanup must not leave leaked OS resources behind.
        for name in names:
            if os.path.exists("/dev/shm/sem." + name.lstrip("/")):
                _multiprocessing.sem_unlink(name)
                resource_tracker.unregister(name, "semaphore")
        if gc_enabled:
            gc.enable()
    assert len(remaining) == (4 if expected == "base" else 0), remaining
    assert all(
        not os.path.exists("/dev/shm/sem." + name.lstrip("/")) for name in names
    )
    print(json.dumps({
        "linux_semaphores_created": 4,
        "remaining_after_finalizer": len(remaining),
        "remaining_after_probe_cleanup": 0,
        "expected_source": expected,
    }))


def compare_xml(base_path, head_path):
    def cases(path):
        root = ET.parse(path).getroot()
        return {
            (case.attrib["classname"], case.attrib["name"]): (
                "failed" if case.find("failure") is not None else
                "error" if case.find("error") is not None else
                "skipped" if case.find("skipped") is not None else "passed"
            )
            for case in root.iter("testcase")
        }

    base, head = cases(base_path), cases(head_path)
    assert base.keys() == head.keys()
    assert set(head.values()) == {"passed"}, head
    failures = [name for (_, name), status in base.items() if status == "failed"]
    expected = {
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
    }
    assert set(failures) == expected, failures
    assert set(base.values()) == {"passed", "failed"}, base
    print(json.dumps({
        "same_case_set": True, "head_passed": len(head),
        "base_expected_failures": sorted(failures),
        "base_passed": len(base) - len(failures), "skipped": 0,
    }))


parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("stress", "mutate", "semaphore", "compare"))
parser.add_argument("--repetitions", type=int, default=100)
parser.add_argument("--kind", choices=("foreign-scope", "early-restore"))
parser.add_argument("--expected", choices=("base", "head"))
parser.add_argument("--base-xml", type=Path)
parser.add_argument("--head-xml", type=Path)
args = parser.parse_args()
if args.mode == "stress":
    stress(args.repetitions)
elif args.mode == "mutate":
    mutate(args.kind)
elif args.mode == "semaphore":
    semaphore(args.expected)
else:
    compare_xml(args.base_xml, args.head_xml)
