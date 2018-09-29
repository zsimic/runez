"""
Convenience methods for file/process operations
"""

import io
import json
import logging
import os
import shutil
import subprocess  # nosec
import sys
import tempfile
import time

try:
    import StringIO
    StringIO = StringIO.StringIO

except ImportError:
    StringIO = io.StringIO


LOG = logging.getLogger(__name__)
HOME = os.path.expanduser("~")
SYMBOLIC_TMP = "<tmp>"
DRYRUN = False


class State:
    """Helps track state without using globals"""

    anchors = []  # Folder paths that can be used to shorten paths, via short()

    output = True  # print() warning/error messages (can be turned off when/if we have a logger to console for example)
    testing = False  # print all messages instead of logging (useful when running tests)
    logging = False  # Set to True if logging was setup


class CurrentFolder:
    """Context manager for changing the current working directory"""

    def __init__(self, destination, anchor=False):
        self.anchor = anchor
        self.destination = resolved_path(destination)

    def __enter__(self):
        self.current_folder = os.getcwd()
        os.chdir(self.destination)
        if self.anchor:
            add_anchors(self.destination)

    def __exit__(self, *_):
        os.chdir(self.current_folder)
        if self.anchor:
            pop_anchors(self.destination)


class TempFolder:
    """Context manager for obtaining a temp folder"""

    def __init__(self, anchor=True):
        self.anchor = anchor
        self.dryrun = DRYRUN
        self.tmp_folder = None

    def __enter__(self):
        self.tmp_folder = SYMBOLIC_TMP if self.dryrun else tempfile.mkdtemp()
        if self.anchor:
            add_anchors(self.tmp_folder)
        return self.tmp_folder

    def __exit__(self, *_):
        if self.anchor:
            pop_anchors(self.tmp_folder)
        if self.dryrun:
            debug("Would delete %s", self.tmp_folder)
        else:
            delete(self.tmp_folder)


class CaptureOutput:
    """
    Context manager allowing to temporarily grab stdout/stderr output.
    Output is captured and made available only for the duration of the context.

    Sample usage:

    with CaptureOutput() as output:
        # do something that generates output
        # output is available in 'output'
    """

    def __init__(self, stdout=True, stderr=True, anchors=None, dryrun=None):
        """
        :param bool stdout: Capture stdout
        :param bool stderr: Capture stderr
        :param str|list anchors: Optional paths to use as anchors for short()
        :param bool|None dryrun: Override dryrun (when explicitly specified, ie not None)
        """
        self.anchors = anchors
        self.dryrun = dryrun
        self.old_out = sys.stdout
        self.old_err = sys.stderr
        self.old_handlers = logging.root.handlers

        self.out_buffer = StringIO() if stdout else None

        if stderr:
            self.err_buffer = StringIO()
            self.handler = logging.StreamHandler(stream=self.err_buffer)
            self.handler.setLevel(logging.DEBUG)
            self.handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        else:
            self.err_buffer = None
            self.handler = None

    def pop(self):
        """Current contents popped, useful for testing"""
        r = self.__repr__()
        if self.out_buffer:
            self.out_buffer.seek(0)
            self.out_buffer.truncate(0)
        if self.err_buffer:
            self.err_buffer.seek(0)
            self.err_buffer.truncate(0)
        return r

    def __repr__(self):
        result = ""
        if self.out_buffer:
            result += decode(self.out_buffer.getvalue())
        if self.err_buffer:
            result += decode(self.err_buffer.getvalue())
        return result

    def __enter__(self):
        if self.out_buffer:
            sys.stdout = self.out_buffer
        if self.err_buffer:
            sys.stderr = self.err_buffer
        if self.handler:
            logging.root.handlers = [self.handler]

        if self.anchors:
            add_anchors(self.anchors)

        if self.dryrun is not None:
            global DRYRUN
            (DRYRUN, self.dryrun) = (bool(self.dryrun), bool(DRYRUN))

        return self

    def __exit__(self, *args):
        sys.stdout = self.old_out
        sys.stderr = self.old_err
        self.out_buffer = None
        self.err_buffer = None
        logging.root.handlers = self.old_handlers

        if self.anchors:
            pop_anchors(self.anchors)

        if self.dryrun is not None:
            global DRYRUN
            DRYRUN = self.dryrun

    def __contains__(self, item):
        return item is not None and item in str(self)

    def __len__(self):
        return len(str(self))


def decode(value):
    """Python 2/3 friendly decoding of output"""
    if isinstance(value, bytes) and not isinstance(value, str):
        return value.decode("utf-8")
    return value


def get_version(mod, default="0.0.0", fatal=True, quiet=False):
    """
    :param module|str mod: Module, or module name to find version for (pass either calling module, or its .__name__)
    :param str default: Value to return if version determination fails
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log errors if True
    :return str: Determined version
    """
    name = mod
    if hasattr(mod, "__name__"):
        name = mod.__name__

    try:
        import pkg_resources
        return pkg_resources.get_distribution(name).version

    except Exception as e:
        return abort("Can't determine version for %s: %s", name, e, exc_info=e, fatal=fatal, quiet=quiet, return_value=default)


def resolved_path(path, base=None):
    """
    :param str path: Path to resolve
    :param str|None base: Base path to use to resolve relative paths (default: current working dir)
    :return str: Absolute path
    """
    if not path or path.startswith(SYMBOLIC_TMP):
        return path
    path = os.path.expanduser(path)
    if base and not os.path.isabs(path):
        return os.path.join(resolved_path(base), path)
    return os.path.abspath(path)


def set_anchors(anchors):
    """
    :param str|list anchors: Optional paths to use as anchors for short()
    """
    State.anchors = sorted(flattened(anchors, unique=True), reverse=True)


def add_anchors(anchors):
    """
    :param str|list anchors: Optional paths to use as anchors for short()
    """
    set_anchors(State.anchors + [anchors])


def pop_anchors(anchors):
    """
    :param str|list anchors: Optional paths to use as anchors for short()
    """
    for anchor in flattened(anchors):
        if anchor in State.anchors:
            State.anchors.remove(anchor)


def short(path):
    """
    Example:
        short("examined /Users/joe/foo") -> "examined ~/foo"

    :param path: Path to represent in its short form
    :return str: Short form, using '~' if applicable
    """
    if not path:
        return path

    path = str(path)
    if State.anchors:
        for p in State.anchors:
            if p:
                path = path.replace(p + "/", "")

    path = path.replace(HOME, "~")
    return path


def parent_folder(path, base=None):
    """
    :param str path: Path to file or folder
    :param str|None base: Base folder to use for relative paths (default: current working dir)
    :return str: Absolute path of parent folder of 'path'
    """
    return path and os.path.dirname(resolved_path(path, base=base))


def flatten(result, value, separator=None, unique=True):
    """
    :param list result: Flattened values
    :param value: Possibly nested arguments (sequence of lists, nested lists)
    :param str|None separator: Split values with 'separator' if specified
    :param bool unique: If True, return unique values only
    """
    if not value:
        # Convenience: allow to filter out --foo None easily
        if value is None and not unique and result and result[-1].startswith("-"):
            result.pop(-1)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            flatten(result, item, separator=separator, unique=unique)
        return
    if separator is not None and hasattr(value, "split") and separator in value:
        flatten(result, value.split(separator), separator=separator, unique=unique)
        return
    if not unique or value not in result:
        result.append(value)


def flattened(value, separator=None, unique=True):
    """
    :param value: Possibly nested arguments (sequence of lists, nested lists)
    :param str|None separator: Split values with 'separator' if specified
    :param bool unique: If True, return unique values only
    :return list: 'value' flattened out (leaves from all involved lists/tuples)
    """
    result = []
    flatten(result, value, separator=separator, unique=unique)
    return result


def quoted(text):
    """
    :param str text: Text to optionally quote
    :return str: Quoted if 'text' contains spaces
    """
    if text and " " in text:
        sep = "'" if '"' in text else '"'
        return "%s%s%s" % (sep, text, sep)
    return text


def represented_args(args, separator=" "):
    """
    :param list|tuple args: Arguments to represent
    :param str separator: Separator to use
    :return str: Quoted as needed textual representation
    """
    result = []
    if args:
        for text in args:
            result.append(quoted(short(text)))
    return separator.join(result)


def to_int(text, default=None):
    """
    :param text: Value to convert
    :param int|None default: Default to use if 'text' can't be parsed
    :return int:
    """
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def debug(message, *args, **kwargs):
    """Same as logging.debug(), but more convenient when testing"""
    if State.logging:
        LOG.debug(message, *args, **kwargs)
    if State.testing:
        print(message % args)


def info(message, *args, **kwargs):
    """
    Often, an info() message should be logged, but also shown to user (in the even where logging is not done to console)

    Example:
        info("...") -> Will log if we're logging, but also print() if State.output is currently set
        info("...", output=False) -> Will only log, never print
        info("...", output=True) -> Will log if we're logging, and print
    """
    output = kwargs.pop("output", State.output)
    if State.logging:
        LOG.info(message, *args, **kwargs)
    if output or State.testing:
        print(message % args)


def warning(message, *args, **kwargs):
    """Same as logging.warning(), but more convenient when testing, similar to info()"""
    if State.logging:
        LOG.warning(message, *args, **kwargs)
    if State.output or State.testing:
        print("WARNING: %s" % (message % args))


def error(message, *args, **kwargs):
    """Same as logging.error(), but more convenient when testing, similar to info()"""
    if State.logging:
        LOG.error(message, *args, **kwargs)
    if State.output or State.testing:
        print("ERROR: %s" % (message % args))


def abort(*args, **kwargs):
    """
    Usage:
        return abort("...") -> will sys.exit() by default
        return abort("...", quiet=True) -> will not log/print the message
        return abort("...", fatal=False) -> will return '-1' by default
        return abort("...", fatal=False, return_value=None) -> will return None

    :param args: Args passed through for error reporting
    :param kwargs: Args passed through for error reporting
    :return: kwargs["return_value"] (default: -1) to signify failure to non-fatal callers
    """
    code = kwargs.pop("code", 1)
    logger = kwargs.pop("logger", warning)
    fatal = kwargs.pop("fatal", True)
    quiet = kwargs.pop("quiet", False)
    return_value = kwargs.pop("return_value", -1)
    if not quiet and args:
        if code == 0:
            logger(*args, **kwargs)
        else:
            error(*args, **kwargs)
    if fatal:
        sys.exit(code)
    return return_value


def ensure_folder(path, folder=False, fatal=True, quiet=False):
    """
    :param str path: Path to file or folder
    :param bool folder: If True, 'path' refers to a folder (file otherwise)
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log debug if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    if not path:
        return 0

    if folder:
        folder = resolved_path(path)
    else:
        folder = parent_folder(path)
    if os.path.isdir(folder):
        return 0

    if DRYRUN:
        debug("Would create %s", short(folder))
        return 1

    try:
        os.makedirs(folder)
        if not quiet:
            debug("Created folder %s", short(folder))
        return 1

    except Exception as e:
        return abort("Can't create folder %s: %s", short(folder), e, fatal=fatal)


def first_line(path):
    """
    :param str path: Path to file
    :return str|None: First line of file, if any
    """
    try:
        with io.open(path, "rt", errors="ignore") as fh:
            return fh.readline().strip()
    except (IOError, TypeError):
        return None


def get_lines(path, max_size=8192, fatal=True, quiet=False):
    """
    :param str path: Path of text file to return lines from
    :param int max_size: Return contents only for files smaller than 'max_size' bytes
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log errors if True
    :return list|None: Lines from file contents
    """
    if not path or not os.path.isfile(path) or os.path.getsize(path) > max_size:
        # Intended for small text files, pretend no contents for binaries
        return None

    try:
        with io.open(path, "rt", errors="ignore") as fh:
            return fh.readlines()

    except Exception as e:
        return abort("Can't read %s: %s", short(path), e, fatal=fatal, quiet=quiet, return_value=None)


def file_younger(path, age):
    """
    :param str path: Path to file
    :param int|float age: How many seconds to consider the file too old
    :return bool: True if file exists and is younger than 'age' seconds
    """
    try:
        return time.time() - os.path.getmtime(path) < age

    except (OSError, TypeError):
        return False


def check_pid(pid):
    """Check For the existence of a unix pid"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def touch(path, fatal=True, quiet=True):
    """
    :param str path: Path to file to touch
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True (dryrun being always logged)
    """
    return write_contents(path, "", fatal=fatal, quiet=quiet)


def write_contents(path, contents, fatal=True, quiet=True):
    """
    :param str path: Path to file
    :param str contents: Contents to write
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log debug if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    if not path:
        return 0

    if DRYRUN:
        action = "write %s bytes to" % len(contents) if contents else "touch"
        debug("Would %s %s", action, short(path))
        return 1

    ensure_folder(path, fatal=fatal, quiet=quiet)
    if not quiet and contents:
        debug("Writing %s bytes to %s", len(contents), short(path))

    try:
        with open(path, "wt") as fh:
            if contents:
                fh.write(decode(contents))
            else:
                os.utime(path, None)
        return 1

    except Exception as e:
        return abort("Can't write to %s: %s", short(path), e, fatal=fatal)


def read_json(path, default=None, fatal=False, quiet=True):
    """
    :param str path: Path to file to deserialize
    :param dict|list default: Default if file is not present, or if it's not json
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log debug if True
    :return dict|list: Deserialized data from file
    """
    path = resolved_path(path)
    if not path or not os.path.exists(path):
        if default is None:
            return abort("No file %s", short(path), fatal=fatal, return_value=None)
        return default

    try:
        with io.open(path, "rt") as fh:
            data = json.load(fh)
            if default is not None and type(data) != type(default):
                return abort(
                    "Wrong type %s for %s, expecting %s", type(data), short(path), type(default), fatal=fatal, return_value=default
                )
            if not quiet:
                debug("Read %s", short(path))
            return data

    except Exception as e:
        return abort("Couldn't read %s: %s", short(path), e, fatal=fatal, return_value=default)


def save_json(data, path, fatal=False, quiet=True, sort_keys=True, indent=2):
    """
    :param dict|list|None data: Data to serialize and save
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log debug if True
    :param bool sort_keys: Save json with sorted keys
    :param int indent: Indentation to use
    :param str path: Path to file where to save
    """
    if data is None or not path:
        return 0

    try:
        path = resolved_path(path)
        ensure_folder(path, fatal=fatal, quiet=quiet)
        if DRYRUN:
            debug("Would save %s", short(path))
            return 1

        if hasattr(data, "to_dict"):
            data = data.to_dict()

        with open(path, "wt") as fh:
            json.dump(data, fh, sort_keys=sort_keys, indent=indent)
            fh.write("\n")

        if not quiet:
            debug("Saved %s", short(path))

        return 1

    except Exception as e:
        return abort("Couldn't save %s: %s", short(path), e, fatal=fatal)


def copy(source, destination, adapter=None, fatal=True, quiet=False):
    """
    Copy source -> destination

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param callable adapter: Optional function to call on 'source' before copy
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    return _file_op(source, destination, _copy, adapter, fatal, quiet)


def move(source, destination, adapter=None, fatal=True, quiet=False):
    """
    Move source -> destination

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param callable adapter: Optional function to call on 'source' before copy
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    return _file_op(source, destination, _move, adapter, fatal, quiet)


def symlink(source, destination, adapter=None, must_exist=True, fatal=True, quiet=False):
    """
    Symlink source -> destination

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param callable adapter: Optional function to call on 'source' before copy
    :param bool must_exist: If True, verify that source does indeed exist
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    return _file_op(source, destination, _symlink, adapter,  fatal, quiet, must_exist=must_exist)


def _copy(source, destination):
    """Effective copy"""
    if os.path.isdir(source):
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy(source, destination)

    shutil.copystat(source, destination)  # Make sure last modification time is preserved


def _move(source, destination):
    """Effective move"""
    shutil.move(source, destination)


def _symlink(source, destination):
    """Effective symlink"""
    os.symlink(source, destination)


def _file_op(source, destination, func, adapter, fatal, quiet, must_exist=True):
    """
    Call func(source, destination)

    :param str source: Source file or folder
    :param str destination: Destination file or folder
    :param callable func: Implementation function
    :param callable adapter: Optional function to call on 'source' before copy
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True
    :param bool must_exist: If True, verify that source does indeed exist
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    if not source or not destination or source == destination:
        return 0

    action = func.__name__[1:]
    psource = parent_folder(source)
    pdest = resolved_path(destination)
    if psource != pdest and psource.startswith(pdest):
        return abort("Can't %s %s -> %s: source contained in destination", action, short(source), short(destination), fatal=fatal)

    if DRYRUN:
        debug("Would %s %s -> %s", action, short(source), short(destination))
        return 1

    if must_exist and not os.path.exists(source):
        return abort("%s does not exist, can't %s to %s", short(source), action.title(), short(destination), fatal=fatal)

    try:
        # Delete destination, but ensure that its parent folder exists
        delete(destination, fatal=fatal, quiet=True)
        ensure_folder(destination, fatal=fatal, quiet=quiet)

        if not quiet:
            note = adapter(source, destination, fatal=fatal, quiet=quiet) if adapter else ""
            debug("%s %s -> %s%s", action.title(), short(source), short(destination), note)

        func(source, destination)
        return 1

    except Exception as e:
        return abort("Can't %s %s -> %s: %s", action, short(source), short(destination), e, fatal=fatal)


def delete(path, fatal=True, quiet=False):
    """
    :param str|None path: Path to file or folder to delete
    :param bool fatal: Abort execution on failure if True
    :param bool quiet: Don't log if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    islink = path and os.path.islink(path)
    if not islink and (not path or not os.path.exists(path)):
        return 0

    if DRYRUN:
        debug("Would delete %s", short(path))
        return 1

    if not quiet:
        debug("Deleting %s", short(path))
    try:
        if islink or os.path.isfile(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)
        return 1

    except Exception as e:
        return abort("Can't delete %s: %s", short(path), e, fatal=fatal)


def make_executable(path, fatal=True):
    """
    :param str path: chmod file with 'path' as executable
    :param bool fatal: Abort execution on failure if True
    :return int: 1 if effectively done, 0 if no-op, -1 on failure
    """
    if is_executable(path):
        return 0

    if DRYRUN:
        debug("Would make %s executable", short(path))
        return 1

    if not os.path.exists(path):
        return abort("%s does not exist, can't make it executable", short(path), fatal=fatal)

    try:
        os.chmod(path, 0o755)  # nosec
        return 1

    except Exception as e:
        return abort("Can't chmod %s: %s", short(path), e, fatal=fatal)


def is_executable(path):
    """
    :param str path: Path to file
    :return bool: True if file exists and is executable
    """
    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def which(program, ignore_own_venv=False):
    """
    :param str program: Program name to find via env var PATH
    :param bool ignore_own_venv: If True, do not resolve to executables in current venv
    :return str|None: Full path to program, if one exists and is executable
    """
    if not program:
        return None
    if os.path.isabs(program):
        return program if is_executable(program) else None
    for p in os.environ.get("PATH", "").split(":"):
        fp = os.path.join(p, program)
        if (not ignore_own_venv or not fp.startswith(sys.prefix)) and is_executable(fp):
            return fp
    return None


def run_program(program, *args, **kwargs):
    """Run 'program' with 'args'"""
    args = flattened(args, unique=False)
    full_path = which(program)

    fatal = kwargs.pop("fatal", True)
    dryrun = kwargs.pop("dryrun", DRYRUN)
    include_error = kwargs.pop("include_error", False)
    quiet = kwargs.pop("quiet", False)

    message = "Would run" if dryrun else "Running"
    message = "%s: %s %s" % (message, short(full_path or program), represented_args(args))
    if not quiet:
        logger = kwargs.pop("logger", debug)
        logger(message)

    if dryrun:
        return message

    if not full_path:
        return abort("%s is not installed", short(program), fatal=fatal, quiet=quiet, return_value=None)

    stdout = kwargs.pop("stdout", subprocess.PIPE)
    stderr = kwargs.pop("stderr", subprocess.PIPE)
    args = [full_path] + args
    try:
        path_env = kwargs.pop("path_env", None)
        if path_env:
            kwargs["env"] = added_env_paths(path_env, env=kwargs.get("env"))
        p = subprocess.Popen(args, stdout=stdout, stderr=stderr, **kwargs)  # nosec
        output, err = p.communicate()
        output = decode(output)
        err = decode(err)
        if output is not None:
            output = output.strip()
        if err is not None:
            err = err.strip()

        if p.returncode and fatal is not None:
            note = ": %s\n%s" % (err, output) if output or err else ""
            message = "%s exited with code %s%s" % (short(program), p.returncode, note.strip())
            return abort(message, fatal=fatal, quiet=quiet, return_value=None)

        if include_error and err:
            output = "%s\n%s" % (output, err)
        return output and output.strip()

    except Exception as e:
        return abort("%s failed: %s", short(program), e, exc_info=e, fatal=fatal, quiet=quiet, return_value=None)


def added_env_paths(env_vars, env=None):
    """
    :param dict env_vars: Env vars to customize
    :param dict env: Original env vars
    """
    if not env_vars:
        return None
    if not env:
        env = dict(os.environ)
    result = dict(env)
    for env_var, paths in env_vars.items():
        separator = paths[0]
        paths = paths[1:]
        current = env.get(env_var, "")
        current = [x for x in current.split(separator) if x]
        added = 0
        for path in paths.split(separator):
            if path not in current:
                added += 1
                current.append(path)
        if added:
            result[env_var] = separator.join(current)
    return result


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
