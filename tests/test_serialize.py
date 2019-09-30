import datetime
import logging

import pytest
from mock import patch

import runez
from runez.schema import Dict, Integer, List, String
from runez.serialize import add_meta, ClassMetaDescription, get_descriptor, same_type, type_name, ValidationException, with_behavior


@add_meta(ClassMetaDescription)
class MetaSlotted(object):
    __slots__ = "name"


class SlottedExample(runez.Serializable, with_behavior(strict=True, extras=Exception)):
    __slots__ = ["name"]


def test_slotted(logged):
    assert isinstance(MetaSlotted._meta, ClassMetaDescription)
    assert len(MetaSlotted._meta.attributes) == 1
    assert not MetaSlotted._meta.properties

    assert isinstance(SlottedExample._meta, ClassMetaDescription)
    assert len(SlottedExample._meta.attributes) == 1
    assert not SlottedExample._meta.properties

    se = SlottedExample.from_dict({"name": "foo"})
    assert se.name == "foo"
    assert se.to_dict() == {"name": "foo"}

    with pytest.raises(Exception) as e:
        SlottedExample.from_dict({"foo": "bar"})
    assert str(e.value) == "Extra content given for SlottedExample: foo"


def test_get_descriptor():
    assert str(get_descriptor("a")) == "string (default: a)"
    assert str(get_descriptor(u"a")) == "string (default: a)"
    assert str(get_descriptor(5)) == "integer (default: 5)"

    assert str(get_descriptor(str)) == "string"
    assert str(get_descriptor(int)) == "integer"
    assert str(get_descriptor(dict)) == "dict[*, *]"
    assert str(get_descriptor(list)) == "list[*]"
    assert str(get_descriptor(set)) == "list[*]"
    assert str(get_descriptor(tuple)) == "list[*]"

    assert str(get_descriptor(List)) == "list[*]"
    assert str(get_descriptor(List(Integer))) == "list[integer]"
    assert str(get_descriptor(Dict(String, List(Integer)))) == "dict[string, list[integer]]"

    assert get_descriptor(object()) is None


def test_json(temp_folder):
    assert runez.read_json(None, fatal=False) is None

    assert runez.represented_json(None) == "null\n"
    assert runez.represented_json([]) == "[]\n"
    assert runez.represented_json({}) == "{}\n"
    assert runez.represented_json("foo") == '"foo"\n'

    assert runez.save_json(None, None, fatal=False) == 0

    data = {"a": "b"}

    assert not runez.DRYRUN
    with runez.CaptureOutput(dryrun=True) as logged:
        assert runez.save_json(data, "sample.json") == 1
        assert "Would save" in logged.pop()
    assert not runez.DRYRUN

    with runez.CaptureOutput() as logged:
        assert runez.read_json("sample.json", fatal=False) is None
        assert "No file" in logged.pop()

        assert runez.read_json("sample.json", default={}, fatal=False) == {}
        assert not logged

        with patch("runez.serialize.open", side_effect=Exception):
            assert runez.save_json(data, "sample.json", fatal=False) == -1
            assert "Couldn't save" in logged.pop()

        assert runez.save_json(data, "sample.json", logger=logging.debug) == 1
        assert "Saved " in logged.pop()

        with patch("io.open", side_effect=Exception):
            assert runez.read_json("sample.json", fatal=False) is None
            assert "Couldn't read" in logged.pop()

        assert runez.read_json("sample.json", logger=logging.debug) == data
        assert "Read " in logged.pop()

        assert runez.read_json("sample.json", default=[], fatal=False) == []
        assert "Wrong type" in logged.pop()


class SomeSerializable(runez.Serializable, with_behavior(strict=True)):
    name = "my name"
    some_int = 7
    some_value = List(Integer)
    another = None

    @property
    def int_prod(self):
        return self.some_int

    def set_some_int(self, value):
        self.some_int = value


class SomeRecord(object):
    name = "my record"
    some_int = 5


def test_meta(logged):
    custom = ClassMetaDescription(SomeRecord)
    assert len(custom.attributes) == 2
    assert len(custom.properties) == 0

    with pytest.raises(ValidationException) as e:
        SomeSerializable.from_dict({"some_int": "foo"})
    assert e.value.message == "Can't deserialize SomeSerializable.some_int: expecting int, got 'foo'"

    data = {"name": "some name", "some_int": 15}
    obj = SomeSerializable.from_dict(data)
    obj2 = SomeSerializable()
    assert obj != obj2

    obj2.name = "some name"
    obj2.some_int = 15
    assert obj == obj2

    assert len(SomeSerializable._meta.attributes) == 4
    assert len(SomeSerializable._meta.properties) == 1
    assert obj._meta is SomeSerializable._meta

    assert not logged


def test_sanitize():
    assert runez.json_sanitized(None) is None
    assert runez.json_sanitized({1, 2}) == [1, 2]

    now = datetime.datetime.now()
    assert runez.json_sanitized(now) == str(now)
    assert runez.json_sanitized(now, dt=None) is now
    assert runez.json_sanitized([now]) == [str(now)]
    assert runez.json_sanitized([now], dt=None) == [now]

    obj = object()
    assert runez.json_sanitized(obj) == str(obj)
    assert runez.json_sanitized(obj, stringify=None) is obj


def test_serialization(logged):
    obj = runez.Serializable()
    assert not obj._meta.attributes
    assert not obj._meta.properties

    obj.set_from_dict({}, source="testing")  # no-op
    assert not logged

    obj.set_from_dict({"foo": 0}, source="testing")  # no-op
    assert not logged

    obj = SomeSerializable.from_dict({}, source="testing")
    assert obj.to_dict() == {"name": "my name", "some_int": 7}

    # Unknown fields
    obj2 = SomeSerializable.from_dict({"foo": 1, "bar": 2})
    assert not hasattr(obj2, "foo")  # non-declared keys are ignored
    assert obj2.some_int == 7  # Fields not in data still get their default value
    assert obj == obj2
    assert "Extra content given for SomeSerializable: bar, foo" in logged.pop()

    obj2 = SomeSerializable.from_json("", default={})
    assert obj == obj2
    assert not logged

    obj.some_int = 5
    obj.reset()
    assert obj.name == "my name"
    assert obj.some_int == 7
    assert obj.some_value is None
    assert obj == obj2

    path = "/dev/null/not-there"
    obj3 = SomeSerializable.from_json(path, fatal=False)
    assert "No file /dev/null/not-there" in logged.pop()

    assert obj == obj3


def test_to_dict(temp_folder):
    with runez.CaptureOutput() as logged:
        # Try with an object that isn't directly serializable, but has a to_dict() function
        data = {"a": "b"}
        obj = SomeRecord()
        obj.to_dict = lambda *_: data

        assert runez.save_json(obj, "sample2.json", logger=logging.debug) == 1
        assert "Saved " in logged.pop()

        assert runez.read_json("sample2.json", logger=logging.debug) == data
        assert "Read " in logged.pop()


def test_types():
    assert type_name(None) == "None"
    assert type_name("some-string") == "str"
    assert type_name(u"some-string") == "str"
    assert type_name({}) == "dict"
    assert type_name(dict) == "dict"
    assert type_name([]) == "list"
    assert type_name(1) == "int"

    assert same_type(None, None)
    assert not same_type(None, "")
    assert not same_type("", None)
    assert same_type("some-string", "some-other-string")
    assert same_type("some-string", u"some-unicode")
    assert same_type(["some-string"], [u"some-unicode"])
    assert same_type(1, 2)
