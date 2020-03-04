import os

from mock import patch

import runez


def failed_function(*args):
    with patch("runez.system.logging.root") as root:
        root.handlers = None
        runez.abort(*args)


def test_abort(logged):
    assert runez.abort("aborted", fatal=(False, "some-return")) == "some-return"
    assert "aborted" in logged.pop()

    assert runez.abort("aborted", fatal=(False, "some-return"), code=0) == "some-return"
    assert "aborted" in logged
    assert "ERROR" not in logged.pop()

    assert runez.abort("aborted", fatal=(None, "some-return")) == "some-return"
    assert not logged
    assert "stderr: oops" in runez.verify_abort(failed_function, "oops")

    with patch("runez.system.AbortException", side_effect=str):
        assert runez.abort("oops", logger=None) == "1"


def test_auto_import_siblings():
    # Check that none of these invocations raise an exception
    assert not runez.system.is_caller_package(None)
    assert not runez.system.is_caller_package("")
    assert not runez.system.is_caller_package("_pydevd")
    assert not runez.system.is_caller_package("_pytest.foo")
    assert not runez.system.is_caller_package("pluggy.hooks")
    assert not runez.system.is_caller_package("runez.system")

    assert runez.system.is_caller_package("foo")

    assert runez.auto_import_siblings([]) is None
    assert runez.auto_import_siblings([""]) == []

    runez.auto_import_siblings()

    with patch("runez.system.find_caller_frame", return_value=None):
        runez.auto_import_siblings()

    with patch.dict(os.environ, {"TOX_WORK_DIR": "some-value"}, clear=True):
        imported = runez.auto_import_siblings()
        by_name = dict((m.__name__, m) for m in imported)

        assert len(imported) == 20
        assert "conftest" in by_name
        assert "secondary" in by_name
        assert "secondary.test_import" in by_name
        assert "test_base" in by_name
        assert "test_system" not in by_name


def test_current_test():
    assert "test_system.py" in runez.current_test()


def test_failed_version(logged):
    with patch("pkg_resources.get_distribution", side_effect=Exception("testing")):
        assert runez.get_version(runez) == "0.0.0"
    assert "Can't determine version for runez" in logged


def test_formatted_string():
    assert runez.system.formatted_string() == ""

    assert runez.system.formatted_string("test") == "test"
    assert runez.system.formatted_string("test", "bar") == "test"
    assert runez.system.formatted_string("test %s", "bar") == "test bar"
    assert runez.system.formatted_string("test %s %s", "bar") == "test %s %s"

    assert runez.system.formatted_string(None) is None
    assert runez.system.formatted_string(None, "bar") is None

    assert runez.system.formatted_string("test", None) == "test"


def test_platform():
    assert runez.get_platform()


def test_version():
    with runez.CaptureOutput() as logged:
        expected = runez.get_version(runez)
        assert expected
        assert expected != "0.0.0"
        assert expected == runez.get_version(runez.__name__)
        assert expected == runez.get_version("runez")
        assert expected == runez.get_version("runez.base")
        assert not logged

    with runez.CaptureOutput() as logged:
        assert runez.get_version(None) == "0.0.0"
        assert not logged

        assert runez.get_version(["foo"]) == "0.0.0"
        assert "Can't determine version" in logged.pop()
