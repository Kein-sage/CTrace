"""Bounded compiler output collection for the local CTrace CLI."""

import os
import queue
import signal
import subprocess
import threading
import time


class CompilerOutputLimitExceeded(Exception):
    pass


def run_compiler(command, *, timeout=5, max_output_bytes=1024 * 1024,
                 stdin=None, env=None, cwd=None, on_output=None):
    if timeout <= 0 or max_output_bytes < 0:
        raise ValueError("Invalid compiler limits")

    deadline = time.monotonic() + timeout
    options = {}
    if stdin is not None:
        options["stdin"] = stdin
    if env is not None:
        options["env"] = env
    if cwd is not None:
        options["cwd"] = cwd
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
        **options,
    )
    events = queue.Queue(maxsize=2)
    stop = threading.Event()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    completed = False

    def publish(item):
        while not stop.is_set():
            try:
                events.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_pipe(name, pipe):
        try:
            while not stop.is_set():
                chunk = pipe.read1(4096)
                if not chunk:
                    break
                publish((name, chunk))
        except OSError as error:
            publish((name, error))
        finally:
            publish((name, None))
            pipe.close()

    readers = [
        threading.Thread(
            target=read_pipe,
            args=(name, pipe),
            daemon=True,
        )
        for name, pipe in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        )
    ]

    try:
        for reader in readers:
            reader.start()

        open_streams = 2
        total = 0
        while open_streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)

            try:
                name, chunk = events.get(timeout=remaining)
            except queue.Empty:
                raise subprocess.TimeoutExpired(command, timeout) from None

            if chunk is None:
                open_streams -= 1
            elif isinstance(chunk, OSError):
                raise chunk
            else:
                total += len(chunk)
                if total > max_output_bytes:
                    raise CompilerOutputLimitExceeded(
                        f"Compiler output exceeded {max_output_bytes} bytes"
                    )
                buffers[name].extend(chunk)
                if on_output is not None:
                    on_output(name, chunk)

        process.wait(timeout=max(0, deadline - time.monotonic()))
        completed = True
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout=buffers["stdout"].decode("utf-8", errors="replace"),
            stderr=buffers["stderr"].decode("utf-8", errors="replace"),
        )
    finally:
        stop.set()
        if not completed:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
        process.wait()
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=1)
