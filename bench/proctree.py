"""Make a run's processes die with the run.

Windows: every run's process is put in a Job Object with KILL_ON_JOB_CLOSE. Closing the job handle
kills everything still in it, including grandchildren the model detached, and the OS closes the
handle itself if this Python process dies (a closed console window), so nothing outlives the round.
POSIX: every run gets its own session/process group, killed as a whole when the run ends.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_ALL_ACCESS = 0x1F0FFF

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                       "OtherOperationCount", "ReadTransferCount",
                                                       "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.OpenProcess.restype = wintypes.HANDLE


def popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def attach(proc: subprocess.Popen):
    """Bind proc (and everything it later starts) to the run. Returns a handle for release()."""
    if sys.platform != "win32":
        return proc.pid
    job = _k32.CreateJobObjectW(None, None)
    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
    h = _k32.OpenProcess(_PROCESS_ALL_ACCESS, False, proc.pid)
    _k32.AssignProcessToJobObject(job, h)
    _k32.CloseHandle(h)
    return job


def release(handle) -> None:
    """Kill whatever the run left behind."""
    if handle is None:
        return
    if sys.platform == "win32":
        _k32.CloseHandle(handle)                 # KILL_ON_JOB_CLOSE ends every process still in the job
    else:
        try:
            os.killpg(handle, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
