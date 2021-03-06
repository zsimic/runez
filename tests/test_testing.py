import os
import re
import sys

import pytest

import runez
import runez.conftest


def sample_main():
    args = sys.argv[1:]
    if args:
        args = runez.flattened(args, shellify=True)
        if args[0] == "TypeError":
            # Raise a TypeError
            len(42)

        exit_code = runez.to_int(args[0])
        if exit_code is not None:
            # When first arg is a number, call sys.exit() with that number
            if len(args) > 1:
                print(" ".join(args[1:]))
            sys.exit(exit_code)

        if args[0] == "Exception":
            # Raise a generic exception
            raise Exception("crashed: %s" % args[1:])

        if args[0] == "exit":
            # exit without explicit code
            sys.exit(" ".join(args[1:]))

        if args[0] == "quiet":
            # Don't output anything
            sys.exit(0)

    # Simulate some output
    return "%s %s" % (os.path.basename(sys.argv[0]), " ".join(args))


def test_crash(cli):
    with pytest.raises(AssertionError):
        # Nothing ran yet, no output
        cli.match("foo")

    with pytest.raises(AssertionError):
        # No main provided
        cli.main = cli.default_main = None
        cli.run("hello no main")

    cli.run(["Exception", "hello with main"], main=sample_main)
    assert cli.failed
    assert cli.match("crashed...hello")
    assert cli.match("Exited with stacktrace:")

    cli.run("TypeError")
    assert cli.failed
    assert cli.match("TypeError: ... has no len")

    cli.run("exit", "some message")
    assert cli.failed
    assert cli.match("some message", "!stacktrace")

    cli.expect_failure("Exception hello", "crashed...hello", "Exited with stacktrace:", "!this message shouldn't appear")

    cli.run(["successful hello"])
    assert cli.succeeded
    assert cli.match("successful hello")

    cli.run(["Exception", "hello again"])
    assert cli.failed
    assert not cli.match("hello with main")
    assert not cli.match("successful hello")
    assert cli.match("hello again")

    cli.run(1, "hello")
    assert cli.failed
    assert "hello" in cli.logged.stdout
    assert "Exited with stacktrace" not in cli.logged

    cli.run(2)
    assert cli.failed
    assert cli.exit_code == 2
    assert not cli.logged

    with pytest.raises(AssertionError):
        # No captures specified
        assert cli.match("crashed...hello", stdout=False, stderr=False)

    with pytest.raises(AssertionError):
        # Expect success failed
        cli.expect_success("Exception", "hello")

    with pytest.raises(AssertionError):
        # Unexpected message seen in output
        cli.expect_failure(["Exception", "hello"], "!crashed...hello")

    with pytest.raises(AssertionError):
        # Expected message not seen in output
        cli.expect_failure(["Exception", "hello"], "this message shouldn't appear")


def test_edge_cases(monkeypatch):
    # verify_abort should complain about called function not having raised anything
    with pytest.raises(AssertionError):
        assert runez.conftest.verify_abort(sample_main)

    # Edge case for wrapper= arg in patch_raise()
    runez.conftest.patch_raise(monkeypatch, runez.log, "tests_path", wrapper=staticmethod)
    with pytest.raises(Exception):
        runez.log.project_path()


def test_success(cli, monkeypatch):
    cli.main = sample_main

    # Verify that project folder works properly
    tests = os.path.dirname(__file__)
    project_folder = os.path.abspath(os.path.join(tests, ".."))

    assert cli.project_folder == project_folder
    assert runez.log.project_path() == project_folder
    assert cli.tests_folder == tests
    assert cli.tests_path("foo.txt") == os.path.join(tests, "foo.txt")
    assert cli.project_path() == project_folder
    assert cli.project_path("foo") == os.path.join(project_folder, "foo")

    cli.run("quiet")
    assert cli.succeeded
    assert not cli.match(".*", regex=True)

    cli.run("--dryrun hello", exe="bar/foo")
    assert cli.succeeded
    assert cli.logged.stdout.contents() == "foo --dryrun hello"
    assert not cli.logged.stderr

    cli.run("--dryrun hello")
    assert cli.succeeded
    assert cli.logged.stdout.contents() == "pytest --dryrun hello"
    assert not cli.logged.stderr
    assert cli.match("el+", regex=True)
    assert not cli.match("EL+", regex=True)
    assert cli.match("EL+", regex=re.IGNORECASE)

    cli.expect_success("hello", "hello", "el+", regex=True)

    cli.run("{marker} world", marker="hello")
    m = cli.match("hello world")
    assert m
    assert str(m) == "hello world"
    m = cli.match("el+", regex=True)
    assert m
    assert m.match == "ell"

    assert cli.match("h...")
    assert cli.match("h...", regex=True)
    assert not cli.match("h...", regex=False)
    assert cli.match(re.compile("hel+o"))

    assert not cli.match("Hello")
    assert cli.match("Hello", regex=re.IGNORECASE)

    cli.run([""])
    assert not cli.match("hello")
