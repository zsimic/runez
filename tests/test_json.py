from mock import patch

import runez


def test_json(temp_base):
    assert runez.read_json(None) is None
    assert runez.save_json(None, None) == 0

    data = {"a": "b"}

    with runez.CaptureOutput(dryrun=True) as logged:
        assert runez.save_json(data, "sample.json") == 1
        assert "Would save" in logged.pop()

    with runez.CaptureOutput() as logged:
        assert runez.read_json("sample.json", fatal=False) is None
        assert "No file" in logged.pop()

        assert runez.read_json("sample.json", default={}, fatal=False) == {}
        assert not logged

        with patch("runez.open", side_effect=Exception):
            assert runez.save_json(data, "sample.json", fatal=False) == -1
            assert "Couldn't save" in logged.pop()

        assert runez.save_json(data, "sample.json", logger=runez.debug) == 1
        assert "Saved " in logged.pop()

        with patch("io.open", side_effect=Exception):
            assert runez.read_json("sample.json", fatal=False) is None
            assert "Couldn't read" in logged.pop()

        assert runez.read_json("sample.json", logger=runez.debug) == data
        assert "Read " in logged.pop()

        assert runez.read_json("sample.json", default=[], fatal=False) == []
        assert "Wrong type" in logged.pop()

    with runez.CaptureOutput() as logged:
        # Try with an object that isn't directly serializable, but has a to_dict() function
        obj = runez.State()
        obj.to_dict = lambda *_: data

        assert runez.save_json(obj, "sample2.json", logger=runez.debug) == 1
        assert "Saved " in logged.pop()

        assert runez.read_json("sample2.json", logger=runez.debug) == data
        assert "Read " in logged.pop()


def test_types():
    assert runez.type_name(None) == "None"
    assert runez.type_name("foo") == "str"
    assert runez.type_name({}) == "dict"
    assert runez.type_name([]) == "list"
    assert runez.type_name(1) == "int"

    assert runez.same_type(None, None)
    assert not runez.same_type(None, "")
    assert runez.same_type("foo", "bar")
    assert runez.same_type("foo", u"bar")
    assert runez.same_type(["foo"], [u"bar"])
    assert runez.same_type(1, 2)


def test_serialization():
    with runez.CaptureOutput() as logged:
        j = runez.JsonSerializable()
        assert str(j) == "no source"
        j.save()  # no-op
        j.set_from_dict({}, source="test")
        j.some_list = []
        j.some_string = ""

        j.set_from_dict({"foo": "bar", "some-list": "some_value", "some-string": "some_value"}, source="test")
        assert "foo is not an attribute" in logged
        assert "Wrong type 'str' for JsonSerializable.some_list in test, expecting 'list'" in logged.pop()

        assert str(j) == "test"
        assert not j.some_list
        assert not hasattr(j, "foo")
        assert j.some_string == "some_value"
        assert j.to_dict() == {"some-list": [], "some-string": "some_value"}

        j.reset()
        assert not j.some_string

        j = runez.JsonSerializable.from_json("")
        assert str(j) == "no source"

        j = runez.JsonSerializable.from_json("/dev/null/foo", fatal=False)
        assert str(j) == "/dev/null/foo"
        j.save(fatal=False)
        assert "ERROR: Couldn't save" in logged.pop()