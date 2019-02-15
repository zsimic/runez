"""
Convenience context managers
"""

import os
import shutil
import sys
import tempfile

try:
    import StringIO
    StringIO = StringIO.StringIO

except ImportError:
    from io import StringIO

import runez.log
from runez.base import flattened, listify, State
from runez.path import resolved_path, SYMBOLIC_TMP


class Anchored:
    """
    An "anchor" is a known path that we don't wish to show in full when printing/logging
    This allows to conveniently shorten paths, and show more readable relative paths
    """

    def __init__(self, folder):
        self.folder = resolved_path(folder)

    def __enter__(self):
        Anchored.add(self.folder)

    def __exit__(self, *_):
        Anchored.pop(self.folder)

    @classmethod
    def set(cls, anchors):
        """
        :param str|list anchors: Optional paths to use as anchors for short()
        """
        State.anchors = sorted(flattened(anchors, unique=True), reverse=True)

    @classmethod
    def add(cls, anchors):
        """
        :param str|list anchors: Optional paths to use as anchors for short()
        """
        cls.set(State.anchors + [anchors])

    @classmethod
    def pop(cls, anchors):
        """
        :param str|list anchors: Optional paths to use as anchors for short()
        """
        for anchor in flattened(anchors):
            if anchor in State.anchors:
                State.anchors.remove(anchor)


class CapturedStream:
    """Capture output to a stream by hijacking temporarily its write() function"""

    _shared = None

    def __init__(self, target):
        self.target = target
        if target is None:
            self.buffer = CapturedStream._shared._buffer
        else:
            self.buffer = StringIO()

    def __repr__(self):
        return "%s: %s" % (self.name, self.contents())

    def __contains__(self, item):
        return item is not None and item in self.contents()

    def __len__(self):
        return len(self.contents())

    @property
    def name(self):
        if self.target is None:
            return "log"
        if self.target is sys.stdout:
            return "stdout"
        if self.target is sys.stderr:
            return "stderr"
        return str(self.target)

    def contents(self):
        return self.buffer.getvalue()

    def capture(self):
        if self.target:
            self.original = self.target.write
            self.target.write = self.buffer.write
        else:
            self._shared._is_capturing = True

    def restore(self):
        """Restore hijacked write() function"""
        if self.target:
            self.target.write = self.original
        else:
            self._shared._is_capturing = False
        self.clear()

    def clear(self):
        """Clear captured content"""
        self.buffer.seek(0)
        self.buffer.truncate(0)


class CaptureOutput:
    """
    Context manager allowing to temporarily grab stdout/stderr output.
    Output is captured and made available only for the duration of the context.

    Sample usage:

    with CaptureOutput() as logged:
        # do something that generates output
        # output has been captured in 'logged'
    """

    def __init__(self, level=None, streams=None, anchors=None, dryrun=None):
        """
        :param int|None level: Change logging level, if specified
        :param tuple|list|None streams: Streams to capture (default: stderr and stdout)
        :param str|list anchors: Optional paths to use as anchors for short()
        :param bool|None dryrun: Override dryrun (when explicitly specified, ie not None)
        """
        self.level = level
        self.old_level = None
        if streams is None:
            if CapturedStream._shared:
                streams = (sys.stdout, sys.stderr, None)
            else:
                streams = (sys.stdout, sys.stderr)
        self.streams = listify(streams)
        self.anchors = anchors
        self.dryrun = dryrun
        self.captured = None

    def __repr__(self):
        return "".join(str(c) for c in self.captured) if self.captured else ""

    def contents(self):
        return "".join(c.contents() for c in self.captured) if self.captured else ""

    def __eq__(self, other):
        if isinstance(other, CaptureOutput):
            return self.captured == other.captured
        return str(self).strip() == str(other).strip()

    def __enter__(self):
        self.old_level = runez.log.OriginalLogging.set_level(self.level)
        self.captured = []
        if self.streams:
            for stream in self.streams:
                c = CapturedStream(stream)
                c.capture()
                self.captured.append(c)
        if self.anchors:
            Anchored.add(self.anchors)
        if self.dryrun is not None:
            (State.dryrun, self.dryrun) = (bool(self.dryrun), bool(State.dryrun))
        return self

    def __exit__(self, *args):
        runez.log.OriginalLogging.set_level(self.old_level)
        for c in self.captured:
            c.restore()
        self.captured = None
        if self.anchors:
            Anchored.pop(self.anchors)
        if self.dryrun is not None:
            State.dryrun = self.dryrun

    def __contains__(self, item):
        for c in self.captured:
            if item in c:
                return True
        return False

    def __len__(self):
        return sum(len(c) for c in self.captured)

    def pop(self):
        """Current content popped, useful for testing"""
        r = self.contents()
        self.clear()
        return r

    def clear(self):
        """Clear captured content"""
        for c in self.captured:
            c.clear()


class CurrentFolder:
    """
    Context manager for changing the current working directory
    """

    def __init__(self, destination, anchor=False):
        self.anchor = anchor
        self.destination = resolved_path(destination)

    def __enter__(self):
        self.current_folder = os.getcwd()
        os.chdir(self.destination)
        if self.anchor:
            Anchored.add(self.destination)

    def __exit__(self, *_):
        os.chdir(self.current_folder)
        if self.anchor:
            Anchored.pop(self.destination)


class TempFolder:
    """
    Context manager for obtaining a temp folder
    """

    def __init__(self, anchor=True, dryrun=None, follow=True):
        """
        :param anchor: If True, short-ify paths relative to used temp folder
        :param dryrun: Override dryrun (if provided)
        :param follow: If True, change working dir to temp folder (and restore)
        """
        self.anchor = anchor
        self.dryrun = dryrun if dryrun is not None else State.dryrun
        self.old_cwd = os.getcwd() if follow else None
        self.tmp_folder = None

    def __enter__(self):
        if self.dryrun:
            self.tmp_folder = SYMBOLIC_TMP
        else:
            # Use realpath() to properly resolve for example symlinks on OSX temp paths
            self.tmp_folder = os.path.realpath(tempfile.mkdtemp())
            if self.old_cwd:
                os.chdir(self.tmp_folder)
        if self.anchor:
            Anchored.add(self.tmp_folder)
        return self.tmp_folder

    def __exit__(self, *_):
        if self.anchor:
            Anchored.pop(self.tmp_folder)
        if not self.dryrun:
            if self.old_cwd:
                os.chdir(self.old_cwd)
            if self.tmp_folder:
                shutil.rmtree(self.tmp_folder)


def verify_abort(func, *args, **kwargs):
    """
    Convenient wrapper around functions that should exit or raise an exception

    Example:
        assert "Can't create folder" in verify_abort(ensure_folder, "/dev/null/foo")

    :param callable func: Function to execute
    :param args: Args to pass to 'func'
    :param Exception expected_exception: Type of exception that should be raised
    :param kwargs: Named args to pass to 'func'
    :return str: Chatter from call to 'func', if it did indeed raise
    """
    expected_exception = kwargs.pop("expected_exception", SystemExit)
    with CaptureOutput() as logged:
        try:
            func(*args, **kwargs)
            return None
        except expected_exception:
            return str(logged)
