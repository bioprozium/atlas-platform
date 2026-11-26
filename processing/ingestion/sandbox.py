"""Linux launcher for the trusted, pinned validator. Invoked with isolated Python."""

import ctypes
import errno
import os
import resource
import signal
import sys


def stop_with_parent(parent_pid: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0):  # PR_SET_PDEATHSIG
        raise OSError(
            ctypes.get_errno(), "Could not configure the parent-death signal."
        )
    if os.getppid() != parent_pid:
        raise SystemExit(1)


def disable_network() -> None:
    seccomp = ctypes.CDLL("libseccomp.so.2")
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]

    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Could not initialize seccomp.")
    try:
        deny = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO(EPERM)
        for name in (b"socket", b"socketcall", b"connect", b"io_uring_setup"):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if seccomp.seccomp_rule_add(context, deny, syscall, 0):
                raise RuntimeError("Could not configure the network restrictions.")
        if seccomp.seccomp_load(context):
            raise RuntimeError("Could not load the network restrictions.")
    finally:
        seccomp.seccomp_release(context)


if __name__ == "__main__":
    parent, cpu, address_space, output_size = map(int, sys.argv[1:5])
    stop_with_parent(parent)
    for kind, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_AS, address_space),
        (resource.RLIMIT_FSIZE, output_size),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, 64),
    ):
        resource.setrlimit(kind, (value, value))
    disable_network()
    os.execv(sys.argv[5], sys.argv[5:])
