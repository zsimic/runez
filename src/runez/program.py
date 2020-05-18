"""
Convenience methods for executing programs
"""

import os
import shutil
import subprocess  # nosec
import sys
import tempfile

from runez.convert import to_int
from runez.system import _LateImport, abort, decode, flattened, LOG, quoted, short, WINDOWS


DEFAULT_INSTRUCTIONS = {
    "darwin": "run: `brew install {program}`",
    "linux": "run: `apt install {program}`",
}


def check_pid(pid):
    """
    Args:
        pid (int): Pid to examine

    Returns:
        (bool): True if process with pid exists
    """
    if WINDOWS:  # pragma: no cover
        import ctypes

        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x100000
        process = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
        if process:
            kernel32.CloseHandle(process)
            return True

        return False

    try:
        os.kill(pid, 0)
        return True

    except (OSError, TypeError):
        return False


def dev_folder():
    """
    Returns:
        (str | None): Path to development build folder, such as .venv, .tox etc, if any
    """
    return find_parent_folder(sys.prefix, {"venv", ".venv", ".tox", "build"})


def find_parent_folder(path, basenames):
    """
    Args:
        path (str): Path to examine, first parent folder with basename in `basenames` is returned (case insensitive)
        basenames (set): List of acceptable basenames (must be lowercase)

    Returns:
        (str | None): Path to first containing folder of `path` with one of the `basenames`
    """
    if not path or len(path) <= 1:
        return None

    dirpath, basename = os.path.split(path)
    if basename and basename.lower() in basenames:
        return path

    return find_parent_folder(dirpath, basenames)


def is_executable(path):
    """
    Args:
        path (str | None): Path to file

    Returns:
        (bool): True if file exists and is executable
    """
    if WINDOWS:  # pragma: no cover
        return bool(_windows_exe(path))

    return path and os.path.isfile(path) and os.access(path, os.X_OK)


def make_executable(path, fatal=True):
    """
    Args:
        path (str): chmod file with 'path' as executable
        fatal (bool | None): Abort execution on failure if True

    Returns:
        (int): 1 if effectively done, 0 if no-op, -1 on failure
    """
    if is_executable(path):
        return 0

    if _LateImport.is_dryrun():
        LOG.debug("Would make %s executable", short(path))
        return 1

    if not os.path.exists(path):
        return abort("%s does not exist, can't make it executable", short(path), fatal=(fatal, -1))

    try:
        os.chmod(path, 0o755)  # nosec
        return 1

    except Exception as e:
        return abort("Can't chmod %s: %s", short(path), e, fatal=(fatal, -1))


def program_path(path=None):
    """
    Args:
        path (str | None): Optional, path or name to consider (default: `sys.argv[0]`)

    Returns:
        (str): Path of currently running program
    """
    if path is None:
        path = sys.argv[0]

    return which(path) or path


def run(program, *args, **kwargs):
    """
    Run 'program' with 'args'

    Special optional keyword arguments:
    - dryrun (bool): When True (defaults to runez.DRYRUN), do not really run but call logger("Would run: ...") instead

    - fatal (bool | None):
        - when program invocation succeeds:
            - return output if available (ie: if stdout/stderr were not explicitly provided as None)
            - return exit code otherwise
        - when program invocation fails:
            - with fatal=None: Return `None` on failure
            - with fatal=False: Return False on failure
            - with fatal=True: Raise runez.system.AbortException(exit_code) on failure

    - include_error (bool): When True, include stderr contents in returned string

    - logger (callable | None): When provided (defaults to LOG.debug), call logger("Running: ...")

    - path_env (dict | None): Allows to inject PATH-like env vars, see `_added_env_paths()`

    - stdout, stderr: Passed through to `subprocess.Popen`, when both are None causes this function to return exit code instead of output
    """
    args = flattened(args, shellify=True)
    full_path = which(program)

    logger = kwargs.pop("logger", LOG.debug)
    fatal = kwargs.pop("fatal", True)
    dryrun = kwargs.pop("dryrun", _LateImport.is_dryrun())
    include_error = kwargs.pop("include_error", False)

    message = "Would run" if dryrun else "Running"
    message = "%s: %s %s" % (message, short(full_path or program), quoted(args))
    if logger:
        logger(message)

    stdout = kwargs.pop("stdout", subprocess.PIPE)
    stderr = kwargs.pop("stderr", subprocess.PIPE)

    if dryrun:
        if stdout is None and stderr is None:
            return 0
        return message

    if not full_path:
        return abort("%s is not installed", short(program), fatal=fatal)

    with _WrappedArgs([full_path] + args) as args:
        try:
            path_env = kwargs.pop("path_env", None)
            if path_env:
                kwargs["env"] = _added_env_paths(path_env, env=kwargs.get("env"))

            p = subprocess.Popen(args, stdout=stdout, stderr=stderr, **kwargs)  # nosec
            output, err = p.communicate()
            output = decode(output, strip=True)
            err = decode(err, strip=True)

            if stdout is None and stderr is None:
                if p.returncode and fatal:
                    return abort("%s exited with code %s" % (short(program), p.returncode), fatal=fatal, code=p.returncode)
                return p.returncode

            if p.returncode and fatal is not None:
                note = ": %s\n%s" % (err, output) if output or err else ""
                message = "%s exited with code %s%s" % (short(program), p.returncode, note.strip())
                return abort(message, fatal=fatal, code=p.returncode)

            if include_error and err:
                output = "%s\n%s" % (output, err)

            return output and output.strip()

        except Exception as e:
            if isinstance(e, _LateImport.abort_exception()):
                raise

            return abort("%s failed: %s", short(program), e, exc_info=e, fatal=fatal)


def terminal_width(default=None):
    """Get the width (number of columns) of the terminal window.

    Args:
        default: Default to use if terminal width could not be determined

    Returns:
        (int): Determined terminal width, if possible
    """
    for func in (_tw_shutil, _tw_env):
        columns = func()
        if columns is not None:
            return columns

    return to_int(default)


def which(program, ignore_own_venv=False):
    """
    Args:
        program (str | None): Program name to find via env var PATH
        ignore_own_venv (bool): If True, do not resolve to executables in current venv

    Returns:
        (str | None): Full path to program, if one exists and is executable
    """
    if not program:
        return None

    if os.path.isabs(program):
        if WINDOWS:  # pragma: no cover
            return _windows_exe(program)

        return program if is_executable(program) else None

    for p in os.environ.get("PATH", "").split(os.path.pathsep):
        fp = os.path.join(p, program)
        if WINDOWS:  # pragma: no cover
            fp = _windows_exe(fp)

        if fp and (not ignore_own_venv or not fp.startswith(sys.prefix)) and is_executable(fp):
            return fp

    program = os.path.join(os.getcwd(), program)
    if is_executable(program):
        return program

    return None


def require_installed(program, instructions=None, fatal=True, platform=sys.platform):
    """
    Args:
        program (str): Program to check
        instructions (str | dict): Short instructions letting user know how to get `program` installed, example: `run: brew install foo`
                                   Extra convenience, specify:
                                   - None if `program` can simply be install via `brew install <program>`
                                   - A word (without spaces) to refer to "usual" package (brew on OSX, apt on Linux etc)
                                   - A dict with instructions per `sys.platform`
        fatal (bool): If True, raise `runez.system.AbortException` when `program` is not installed
        platform (str | None): Override sys.platform, if provided

    Returns:
        (bool): True if installed, False otherwise (and fatal=False)
    """
    if which(program) is None:
        if not instructions:
            instructions = DEFAULT_INSTRUCTIONS

        if isinstance(instructions, dict):
            instructions = _install_instructions(instructions, platform)

        message = "{program} is not installed"
        if instructions:
            if "\n" in instructions:
                message += ":\n- %s" % instructions

            else:
                message += ", %s" % instructions

        message = message.format(program=program)
        return abort(message, fatal=(fatal, False))

    return True


def _added_env_paths(env_vars, env=None):
    """
    Args:
        env_vars (dict): Env var customizations to apply
        env (dict | None): Original env vars (default: os.environ)

    Returns:
        (dict): Resulting merged env vars
    """
    if not env:
        env = os.environ

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


def _install_instructions(instructions_dict, platform):
    text = instructions_dict.get(platform)
    if not text:
        text = "\n- ".join("on %s: %s" % (k, v) for k, v in instructions_dict.items())

    return text


def _windows_exe(path):  # pragma: no cover
    if path:
        for extension in (".exe", ".bat"):
            fpath = path
            if not fpath.lower().endswith(extension):
                fpath += extension

            if os.path.isfile(fpath):
                return fpath


class _WrappedArgs(object):
    """Context manager to temporarily work around https://youtrack.jetbrains.com/issue/PY-40692"""

    def __init__(self, args):
        self.args = args
        self.tmp_folder = None

    def __enter__(self):
        args = self.args
        if not WINDOWS and "PYCHARM_HOSTED" in os.environ and len(args) > 1 and "python" in args[0] and args[1][:2] in ("-m", "-X"):
            self.tmp_folder = os.path.realpath(tempfile.mkdtemp())
            wrapper = os.path.join(self.tmp_folder, "pydev-wrapper.sh")
            with open(wrapper, "wt") as fh:
                fh.write('exec "$@"\n')

            args = ["/bin/sh", wrapper] + args

        return args

    def __exit__(self, *_):
        if self.tmp_folder:
            shutil.rmtree(self.tmp_folder)


def _tw_shutil():
    try:
        return shutil.get_terminal_size().columns

    except Exception:
        return None


def _tw_env():
    return to_int(os.environ.get("COLUMNS"))
