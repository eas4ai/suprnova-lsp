"""Bound fixture dependency resolution with a Windows Job owned before execution starts."""

import ctypes
from ctypes import wintypes
import json
import subprocess
import time


class WindowsJob:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        functions = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in functions.items():
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result

        class BasicLimits(ctypes.Structure):
            _fields_ = [("processTime", ctypes.c_int64), ("jobTime", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimumWorkingSet", ctypes.c_size_t),
                        ("maximumWorkingSet", ctypes.c_size_t), ("activeLimit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("readOps", "writeOps", "otherOps", "readBytes", "writeBytes", "otherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("processMemory", ctypes.c_size_t), ("jobMemory", ctypes.c_size_t),
                        ("peakProcessMemory", ctypes.c_size_t), ("peakJobMemory", ctypes.c_size_t)]

        self.handle = self.kernel.CreateJobObjectW(None, None)
        self.check(self.handle, "create fixture job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
        try:
            self.check(self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                       ctypes.sizeof(limits)), "configure fixture job")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(result, context):
        if not result:
            raise OSError(f"{context}: {ctypes.WinError(ctypes.get_last_error())}")

    def spawn(self, argv, cwd, environment, stdout, stderr):
        # No command may create a descendant before assignment to our Job.
        child = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=stdout, stderr=stderr,
                                 creationflags=0x00000004)  # CREATE_SUSPENDED is not exported by subprocess.
        try:
            self.check(self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)),
                       "assign fixture command to job")
            self.resume(child.pid)
        except BaseException:
            child.kill()
            child.wait(timeout=5)
            raise
        return child

    def resume(self, pid):
        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("thread", wintypes.DWORD), ("process", wintypes.DWORD),
                        ("priority", wintypes.LONG), ("delta", wintypes.LONG),
                        ("flags", wintypes.DWORD)]

        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD.
        if snapshot == ctypes.c_void_p(-1).value:
            self.check(False, "enumerate suspended fixture thread")
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.process == pid:
                    thread = self.kernel.OpenThread(2, False, entry.thread)  # THREAD_SUSPEND_RESUME.
                    self.check(thread, "open suspended fixture thread")
                    try:
                        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            self.check(False, "resume fixture thread")
                    finally:
                        self.kernel.CloseHandle(thread)
                    return
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("suspended fixture primary thread was not found")
        finally:
            self.kernel.CloseHandle(snapshot)

    def active_processes(self):
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ("userTime", "kernelTime", "periodUserTime", "periodKernelTime")] + [
                        (name, wintypes.DWORD) for name in
                        ("pageFaults", "totalProcesses", "activeProcesses", "terminatedProcesses")]

        accounting = Accounting()
        self.check(self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                   ctypes.sizeof(accounting), None), "observe fixture job cleanup")
        return accounting.activeProcesses

    def drain(self, child):
        self.check(self.kernel.TerminateJobObject(self.handle, 1), "terminate fixture job")
        if child is not None:
            child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while self.active_processes() != 0:
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture job descendants did not drain before cleanup deadline")
            time.sleep(0.02)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_supervised(spec, cwd, environment, output_directory, timeout_ms):
    output_directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = {"command": spec.command, "args": spec.args, "code": None, "timedOut": False,
              "cleanup": {"verifiedEmpty": False, "ownership": "windows-job"}}
    job = WindowsJob()
    child = None
    try:
        with (output_directory / "stdout.log").open("wb") as stdout, \
                (output_directory / "stderr.log").open("wb") as stderr:
            try:
                child = job.spawn([spec.command, *spec.args], str(cwd), environment, stdout, stderr)
                (output_directory / "process.json").write_text(json.dumps({
                    "pid": child.pid, "ownership": "windows-job", "assignedBeforeResume": True}))
                result["code"] = child.wait(timeout=timeout_ms / 1000)
            except subprocess.TimeoutExpired:
                result["timedOut"] = True
            except OSError as error:
                result["spawnError"] = str(error)
            finally:
                try:
                    job.drain(child)
                    result["cleanup"]["verifiedEmpty"] = True
                except (OSError, subprocess.TimeoutExpired, TimeoutError) as error:
                    result["cleanup"]["error"] = str(error)
    finally:
        # Closing the handle also terminates owned descendants if observation failed.
        job.close()
    result["elapsedMs"] = int((time.monotonic() - started) * 1000)
    (output_directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
