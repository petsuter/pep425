"""Provide support for PEP 425 compatibility tags triples."""

import distutils.util
import os
import os.path
import platform
import sys
import sysconfig


INTERPRETER_SHORT_NAMES = {
    "python": "py",  # Generic.
    "cpython": "cp",
    "pypy": "pp",
    "ironpython": "ip",
    "jython": "jy",
}


_32_BIT_INTERPRETER = sys.maxsize <= 2 ** 32


# A dataclass would be better, but Python 2.7. :(
class Tag:

    """Representation of the interpreter/ABI/platform tag triple as specified by PEP 425."""

    def __init__(self, interpreter, abi, platform):
        """Initialize the instance attributes.

        All values are lowercased.

        """
        self._tags = interpreter.lower(), abi.lower(), platform.lower()

    def __eq__(self, other):
        return self._tags == other._tags

    def __hash__(self):
        return hash(self._tags)

    def __str__(self):
        return "-".join(self._tags)

    def __repr__(self):
        return "<{self} @ {self_id}>".format(self=self, self_id=id(self))

    @property
    def interpreter(self):
        return self._tags[0]

    @property
    def abi(self):
        return self._tags[1]

    @property
    def platform(self):
        return self._tags[2]


def parse_tag(tag):
    """Parse the tag triple.

    The result can be more than one tag triple due to the possibility of
    compressed tag triples.

    """
    tags = set()
    interpreters, abis, platforms = tag.split("-")
    for interpreter in interpreters.split("."):
        for abi in abis.split("."):
            for platform in platforms.split("."):
                tags.add(Tag(interpreter, abi, platform))
    return frozenset(tags)


def parse_wheel_tag(path):
    """Parse the path of a wheel file for its tag triple(s)."""
    name = os.path.splitext(path)[0]
    parts = 3
    index = len(name)
    while parts:
        index = name.rindex("-", 0, index)
        parts -= 1
    return parse_tag(name[index + 1 :])


def _normalize_string(string):
    """Convert 'string' to be compatible as a tag."""
    return string.replace(".", "_").replace("-", "_")


def _cpython_interpreter(py_version):
    # TODO: Is using py_version_nodot for interpreter version critical?
    return "cp{major}{minor}".format(major=py_version[0], minor=py_version[1])


def _cpython_abi(py_version):
    """Calcuate the ABI for this CPython interpreter."""
    soabi = sysconfig.get_config_var("SOABI")
    if soabi:
        _, options, _ = soabi.split("-")
    else:
        found_options = [str(py_version[0]), str(py_version[1])]
        if sysconfig.get_config_var("Py_DEBUG"):
            found_options.append("d")
        if sysconfig.get_config_var("WITH_PYMALLOC"):
            found_options.append("m")
        if sysconfig.get_config_var("Py_UNICODE_SIZE") == 4:
            found_options.append("u")
        options = "".join(found_options)
    return "cp{options}".format(options=options)


def _cpython_tags(py_version, interpreter, abi, platforms):
    for tag in (Tag(interpreter, abi, platform) for platform in platforms):
        yield tag
    for tag in (Tag(interpreter, "abi3", platform) for platform in platforms):
        yield tag
    for tag in (Tag(interpreter, "none", platform) for platform in platforms):
        yield tag
    # PEP 384 was first implemented in Python 3.2.
    for minor_version in range(py_version[1] - 1, 1, -1):
        for platform in platforms:
            yield Tag(
                "cp{major}{minor}".format(major=py_version[0], minor=minor_version),
                "abi3",
                platform,
            )


def _pypy_interpreter():
    return "pp{py_major}{pypy_major}{pypy_minor}".format(
        py_major=sys.version_info[0],
        pypy_major=sys.pypy_version_info.major,
        pypy_minor=sys.pypy_version_info.minor,
    )


def _generic_abi():
    """Get the ABI version for this interpreter."""
    abi = sysconfig.get_config_var("SOABI")
    if abi:
        return _normalize_string(abi)
    else:
        return "none"


def _pypy_tags(py_version, interpreter, abi, platforms):
    for tag in (Tag(interpreter, abi, platform) for platform in platforms):
        yield tag
    for tag in (Tag(interpreter, "none", platform) for platform in platforms):
        yield tag


def _generic_tags(interpreter, py_version, abi, platforms):
    for tag in (Tag(interpreter, abi, platform) for platform in platforms):
        yield tag
    if abi != "none":
        for tag in (Tag(interpreter, "none", platform) for platform in platforms):
            yield tag


def _py_interpreter_range(py_version):
    """Yield Python versions in descending order.

    After the latest version, the major-only version will be yielded, and then
    all following versions up to 'end'.

    """
    yield "py{major}{minor}".format(major=py_version[0], minor=py_version[1])
    yield "py{major}".format(major=py_version[0])
    for minor in range(py_version[1] - 1, -1, -1):
        yield "py{major}{minor}".format(major=py_version[0], minor=minor)


def _independent_tags(interpreter, py_version, platforms):
    """Return the sequence of tags that are consistent across implementations.

    The tags consist of:
    - py*-none-<platform>
    - <interpreter>-none-any
    - py*-none-any
    """
    for version in _py_interpreter_range(py_version):
        for platform in platforms:
            yield Tag(version, "none", platform)
    yield Tag(interpreter, "none", "any")
    for version in _py_interpreter_range(py_version):
        yield Tag(version, "none", "any")


def _mac_arch(arch, is_32bit=_32_BIT_INTERPRETER):
    """Calculate the CPU architecture for the interpreter on macOS."""
    if is_32bit:
        if arch.startswith("ppc"):
            return "ppc"
        else:
            return "i386"
    else:
        return arch


def _mac_binary_formats(version, cpu_arch):
    """Calculate the supported binary formats for the specified macOS version and architecture."""
    formats = [cpu_arch]
    if cpu_arch == "x86_64":
        if version >= (10, 4):
            formats.extend(["intel", "fat64", "fat32"])
        else:
            return []
    elif cpu_arch == "i386":
        if version >= (10, 4):
            formats.extend(["intel", "fat32", "fat"])
        else:
            return []
    elif cpu_arch == "ppc64":
        # TODO: Need to care about 32-bit PPC for ppc64 through 10.2?
        if version > (10, 5) or version < (10, 4):
            return []
        else:
            formats.append("fat64")
    elif cpu_arch == "ppc":
        if version <= (10, 6):
            formats.extend(["fat32", "fat"])
        else:
            return []

    formats.append("universal")
    return formats


def _mac_platforms(version=None, arch=None):
    """Calculate the platform tags for macOS."""
    version_str, _, cpu_arch = platform.mac_ver()
    if version is None:
        version = tuple(map(int, version_str.split(".")[:2]))
    if arch is None:
        arch = _mac_arch(cpu_arch)
    platforms = []
    for minor_version in range(version[1], -1, -1):
        compat_version = version[0], minor_version
        binary_formats = _mac_binary_formats(compat_version, cpu_arch)
        for binary_format in binary_formats:
            platforms.append(
                "macosx_{major}_{minor}_{binary_format}".format(
                    major=compat_version[0],
                    minor=compat_version[1],
                    binary_format=binary_format,
                )
            )
    return platforms


def _windows_platforms():
    # XXX Is this function even necessary?
    raise NotImplementedError


# From PEP 513.
def _is_manylinux1_compatible(platform):
    if platform not in ["linux_x86_64", "linux_i686"]:
        return False

    # Check for presence of _manylinux module.
    try:
        import _manylinux

        return bool(_manylinux.manylinux1_compatible)
    except (ImportError, AttributeError):
        # Fall through to heuristic check below.
        pass

    # Check glibc version. CentOS 5 uses glibc 2.5.
    return have_compatible_glibc(2, 5)


# From PEP 513.
def _have_compatible_glibc(major, minimum_minor):
    import ctypes

    process_namespace = ctypes.CDLL(None)
    try:
        gnu_get_libc_version = process_namespace.gnu_get_libc_version
    except AttributeError:
        # Symbol doesn't exist -> therefore, we are not linked to
        # glibc.
        return False

    # Call gnu_get_libc_version, which returns a string like "2.5".
    gnu_get_libc_version.restype = ctypes.c_char_p
    version_str = gnu_get_libc_version()
    # py2 / py3 compatibility:
    if not isinstance(version_str, str):
        version_str = version_str.decode("ascii")

    # Parse string and check against requested version.
    version = [int(piece) for piece in version_str.split(".")]
    assert len(version) == 2
    if major != version[0]:
        return False
    if minimum_minor > version[1]:
        return False
    return True


def _linux_platforms(is_32bit=_32_BIT_INTERPRETER):
    """Return the supported platforms on Linux."""
    linux = _normalize_string(distutils.util.get_platform())
    if linux == "linux_x86_64" and is_32bit:
        linux = "linux_i686"
    platforms = [linux]
    if _is_manylinux1_compatible(linux):
        platforms.append(linux.replace("linux", "manylinux1"))
    return platforms


def _generic_platforms():
    platform = _normalize_string(distutils.util.get_platform())
    return [platform]


def _interpreter_name():
    """Return the name of the running interpreter."""
    name = platform.python_implementation().lower()
    return INTERPRETER_SHORT_NAMES.get(name) or name


def _generic_interpreter(name, py_version):
    version = sysconfig.get_config_var("py_version_nodot")
    if not version:
        version = "".join(py_version[:2])
    return "{name}{version}".format(name=name, version=version)


def sys_tags():
    """Return the sequence of tag triples for the running interpreter.

    The order of the sequence corresponds to priority order for the interpreter,
    from most to least important.

    """
    py_version = sys.version_info[:2]
    interpreter_name = _interpreter_name()
    if platform.system() == "Darwin":
        platforms = _mac_platforms()
    elif platform.system == "Linux":
        platforms = _linux_platforms()
    else:
        platforms = _generic_platforms()

    if interpreter_name == "cp":
        interpreter = _cpython_interpreter(py_version)
        abi = _cpython_abi(py_version)
        for tag in _cpython_tags(py_version, interpreter, abi, platforms):
            yield tag
    elif interpreter_name == "pp":
        interpreter = _pypy_interpreter()
        abi = _generic_abi()
        for tag in _pypy_tags(py_version, interpreter, abi, platforms):
            yield tag
    else:
        interpreter = _generic_interpreter(interpreter_name, py_version)
        abi = _generic_abi()
        for tag in _generic_tags(interpreter, py_version, abi, platforms):
            yield tag
    for tag in _independent_tags(interpreter, py_version, platforms):
        yield tag


# XXX Test _linux_platforms()
# XXX Test on Windows; should just work.


# XXX https://pypi.org/project/mysql-connector-python/#files
# XXX https://pypi.org/project/pip/#files
# XXX https://pypi.org/project/numpy/#files
