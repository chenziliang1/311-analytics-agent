#!/usr/bin/env python3

"""
Kill this project's Next.js and FastAPI development servers -- and nothing else.

Usage:
    python kill-dev-servers.py [--dry-run] [--force]

The hard rule: a process is only killed if it can be proven to belong to THIS
checkout. Proof is one of

  1. it carries this checkout's dev marker in its environment -- see the
     `env` blocks on the dev tasks in mise.toml -- which children inherit for
     free, so the whole server tree is tagged by starting it;
  2. its working directory is the project root, or its command line contains
     the project root path (the fallback for a server started by hand); or
  3. it descends from a process that satisfied 1 or 2.

Anything else holding a dev port is reported and left alone, because the same
port number shows up in unrelated software all day long -- and the script exits
non-zero so `mise run dev`, which depends on it, stops rather than starting a
half-wired stack on top of a stranger's port.

Use --force to take those ports anyway: the dev tree holding them is uprooted
whole, wrappers included. A foreign process that is not in our way is never
touched either way. This app is meant to run one instance at a time.
"""

from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from pathlib import Path
import argparse
import os
import re
import signal
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent

# The env var `mise run dev` stamps on its servers.
#
# The name is deliberately generic, shareable by other projects, because the
# *value* is what identifies a checkout: the marker
# matched below is `NAME=<absolute path>`, so two projects using the same
# variable name still never see each other's processes. Naming it after the
# dataset was the earlier arrangement and bought nothing, while guaranteeing the
# name would go stale the first time the dataset was swapped.
DEV_MARKER_VAR = "AGENT_APP_DEV_SERVER"
DEV_MARKER = f"{DEV_MARKER_VAR}={PROJECT_ROOT}"

# Only the ports this repo actually serves on: 3000 for `next dev` and API_PORT
# (8000 unless mise.local.toml moves it) for uvicorn. Next.js is pinned to 3000 in mise.toml rather than left to drift to
# 3001 when 3000 is taken, because next.config.js rewrites /api to
# 127.0.0.1:$API_PORT -- a frontend that quietly moved to 3001 would keep talking
# to whatever backend owns that port, which may belong to another checkout entirely.
# Pinning turns that silent mis-wiring into a startup error. It also keeps this
# list short: an unrelated app on 3001 is none of our business.
DEV_PORTS = (3000, int(os.environ.get("API_PORT", "8000")))

# Patterns that make a process a *candidate*. They are allowed to be loose --
# the project-ownership check below is what decides. See mise.toml.
SEED_PATTERNS = [
    re.compile(r"mise run (next|fastapi)-dev\b"),
    re.compile(r"concurrently.*\bnext-dev\b"),
    re.compile(r"\bnext\b\s+dev\b"),
    re.compile(r"\bnext-server\b"),
    re.compile(r"\buvicorn\b.*\bapi\.index:app"),
]

# Seconds to wait for a graceful SIGTERM before escalating to SIGKILL.
GRACE_PERIOD = 3.0


@dataclass
class Process:
    """One row of `ps`, plus the facts needed to decide whether to kill it."""

    pid: int
    ppid: int
    command: str
    cwd: Optional[Path] = None
    marked: bool = False

    @property
    def matches_seed_pattern(self) -> bool:
        return any(pattern.search(self.command) for pattern in SEED_PATTERNS)

    @property
    def owned_by_project(self) -> bool:
        """True when this process can be proven to belong to this checkout."""
        if self.marked:
            return True
        if self.cwd is not None and self.cwd == PROJECT_ROOT:
            return True
        return str(PROJECT_ROOT) in self.command

    @property
    def evidence(self) -> str:
        if self.marked:
            return "marker"
        if self.cwd is not None and self.cwd == PROJECT_ROOT:
            return "cwd"
        return "path"

    def display(self, max_length: int = 100) -> str:
        if len(self.command) <= max_length:
            return self.command
        return self.command[: max_length - 3] + "..."


@dataclass
class ProcessTable:
    """Every process owned by the current user, indexed for tree walks."""

    by_pid: Dict[int, Process] = field(default_factory=dict)
    children: Dict[int, List[int]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "ProcessTable":
        table = cls()
        # -u restricts to the current user: never touch another account's
        # processes, and never need sudo to do the killing.
        result = run(["ps", "-u", str(os.getuid()), "-o", "pid=,ppid=,command="])
        for line in result.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            table.by_pid[pid] = Process(pid=pid, ppid=ppid, command=parts[2])
            table.children.setdefault(ppid, []).append(pid)
        return table

    def mark_tagged(self) -> None:
        """Flag every process whose environment carries this checkout's marker.

        `ps -E` prints the environment after the command, and a child inherits
        it, so one env var on the mise task tags the entire server tree. The
        environment is read in a separate pass from the command line on
        purpose: folding it into `command` would make every process ever
        launched from this directory look like ours, because of `PWD`.
        """
        output = run(["ps", "-Eww", "-u", str(os.getuid()), "-o", "pid=,command="])
        for line in output.splitlines():
            if DEV_MARKER not in line:
                continue
            head = line.strip().split(None, 1)
            if not head:
                continue
            try:
                pid = int(head[0])
            except ValueError:
                continue
            if pid in self.by_pid:
                self.by_pid[pid].marked = True

    def ancestors_of(self, pid: int) -> Set[int]:
        """Walk up the parent chain. Used to never kill our own caller."""
        seen: Set[int] = set()
        current = self.by_pid.get(pid)
        while current is not None and current.ppid not in seen:
            seen.add(current.ppid)
            current = self.by_pid.get(current.ppid)
        return seen

    def tree_root_of(self, pid: int, stop_at: Set[int]) -> int:
        """Walk up to the top of the dev-server tree that `pid` belongs to.

        The walk is bounded by `matches_seed_pattern`, and that bound is the
        whole safety story: above a dev server sits the user's shell and their
        terminal emulator, and neither of those is ever a thing to kill. A
        shell matches no seed pattern, so the walk stops one step below it and
        the top wrapper -- `pnpm exec concurrently 'mise run next-dev' ...` --
        is the last process taken.
        """
        current = pid
        while True:
            proc = self.by_pid.get(current)
            if proc is None:
                return current
            parent = self.by_pid.get(proc.ppid)
            if parent is None:
                return current
            if parent.pid in stop_at or not parent.matches_seed_pattern:
                return current
            current = parent.pid

    def descendants_of(self, pid: int) -> Set[int]:
        """Every process below `pid`, so a subtree dies as a unit."""
        found: Set[int] = set()
        stack = list(self.children.get(pid, []))
        while stack:
            child = stack.pop()
            if child in found:
                continue
            found.add(child)
            stack.extend(self.children.get(child, []))
        return found

    def fill_cwds(self, pids: Set[int]) -> None:
        """Ask lsof for the working directory of the given pids, in one call."""
        if not pids:
            return
        joined = ",".join(str(pid) for pid in sorted(pids))
        output = run(["lsof", "-a", "-d", "cwd", "-p", joined, "-Fpn"])
        current: Optional[int] = None
        for line in output.splitlines():
            if line.startswith("p"):
                try:
                    current = int(line[1:])
                except ValueError:
                    current = None
            elif line.startswith("n") and current in self.by_pid:
                self.by_pid[current].cwd = Path(line[1:])


def run(cmd: List[str]) -> str:
    """Run a command and return stdout. A failure is an empty string."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout
    except Exception as e:  # lsof missing, permission denied, ...
        print(f"⚠️  Could not run {cmd[0]}: {e}")
        return ""


def listeners_on(port: int) -> List[int]:
    """PIDs *listening* on a port.

    -sTCP:LISTEN is the whole point: a bare `lsof -i :3000` also returns every
    browser tab connected to the dev server and every unrelated app whose
    remote peer happens to use that port number.
    """
    # -a is mandatory: lsof ORs its selection flags together, so without it
    # `-u me -iTCP:3000` means "my files OR anything on port 3000" and returns
    # nearly every process on the machine.
    output = run(
        [
            "lsof",
            "-nP",
            "-a",
            "-u",
            str(os.getuid()),
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ]
    )
    pids = []
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pids: List[int]) -> List[int]:
    """SIGTERM, wait out the grace period, then SIGKILL whatever is left."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.time() + GRACE_PERIOD
    while time.time() < deadline:
        if not any(is_alive(pid) for pid in pids):
            break
        time.sleep(0.1)

    for pid in pids:
        if is_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    time.sleep(0.2)
    return [pid for pid in pids if is_alive(pid)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be killed, kill nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="kill whatever foreign dev server is holding our ports, tree and all",
    )
    args = parser.parse_args()

    print(f"🔍 Looking for dev servers under {PROJECT_ROOT}\n")

    table = ProcessTable.load()
    protected = table.ancestors_of(os.getpid()) | {os.getpid()}

    # Candidates come from three places: what is listening on our ports, what
    # carries the dev marker, and what looks like one of our `mise run`
    # commands (the concurrently wrapper, for instance, listens on nothing).
    candidates: Set[int] = set()
    port_holders: Set[int] = set()
    for port in DEV_PORTS:
        port_holders.update(pid for pid in listeners_on(port) if pid in table.by_pid)
    candidates.update(port_holders)

    table.mark_tagged()
    candidates.update(
        pid
        for pid, proc in table.by_pid.items()
        if proc.marked or proc.matches_seed_pattern
    )
    candidates -= protected

    table.fill_cwds(candidates)

    ours: Set[int] = set()
    foreign: Set[int] = set()
    for pid in candidates:
        if table.by_pid[pid].owned_by_project:
            ours.add(pid)
        else:
            foreign.add(pid)

    # A verified process brings its whole subtree with it: `next dev` spawns
    # next-server, which spawns the postcss worker, and uvicorn --reload spawns
    # the actual app.
    targets: Set[int] = set()
    for pid in ours:
        targets.add(pid)
        targets.update(table.descendants_of(pid))
    targets -= protected

    if targets:
        print(f"🎯 {len(targets)} process(es) belonging to this project:")
        for pid in sorted(targets):
            proc = table.by_pid[pid]
            print(f"   PID {pid} ({proc.evidence}): {proc.display(88)}")
        print()
    else:
        print("ℹ️  No dev servers from this project are running\n")

    if foreign:
        print("🛡️  Left alone (not this project):")
        for pid in sorted(foreign):
            proc = table.by_pid[pid]
            where = proc.cwd if proc.cwd else "unknown cwd"
            # Only a port holder is in our way; the rest are listed purely so
            # it is visible that they were seen and deliberately spared.
            flag = " ⟵ holds a dev port" if pid in port_holders else ""
            print(f"   PID {pid} [{where}]{flag}: {proc.display(70)}")
        if not args.force and foreign & port_holders:
            print("   Pass --force to take those ports anyway.")
        print()

    # --force means "take the ports I need", not "kill everything that is not
    # mine". A foreign process only qualifies by actually holding one of
    # DEV_PORTS: another checkout's `next dev` sitting on 3001 lands in
    # `foreign` too, and it is no business of ours.
    #
    # A qualifying process is then uprooted whole -- up to the top of its dev
    # tree, then everything below that. Killing only the listener would leave
    # the other checkout's `concurrently` and `mise run` wrappers behind, and
    # a uvicorn reloader would simply respawn the worker we just killed.
    if args.force:
        for pid in foreign & port_holders:
            root = table.tree_root_of(pid, stop_at=protected)
            targets.add(root)
            targets.update(table.descendants_of(root))
        targets -= protected

    if args.dry_run:
        print("🧪 Dry run: nothing was killed")
        return 0

    if targets:
        survivors = terminate(sorted(targets))
        killed = len(targets) - len(survivors)
        print(f"✅ Terminated {killed} process(es)")
        if survivors:
            print(f"❌ Could not kill: {', '.join(str(p) for p in survivors)}")

    print("\n📊 Ports:")
    blocked = False
    for port in DEV_PORTS:
        remaining = listeners_on(port)
        if not remaining:
            print(f"   ✅ {port} free")
            continue
        blocked = True
        for pid in remaining:
            proc = table.by_pid.get(pid)
            name = proc.display(60) if proc else "<unknown>"
            print(f"   ⚠️  {port} still held by PID {pid}: {name}")

    # `mise run dev` depends on this task, so a non-zero exit is what stops it
    # from starting on top of a port it does not own. Without this the servers
    # come up half-broken: uvicorn dies on "address already in use" while Next
    # proxies /api to the stranger already sitting on 8000.
    if blocked:
        print(
            "\n❌ A dev port above is held by a process that is not this"
            " project's.\n"
            "   Not touching it -- you probably have another app running.\n"
            "   Take a look: this app is designed to run one at a time.\n"
            "\n"
            "   Then either stop that app where it lives, or re-run with"
            " --force to\n"
            "   kill whatever is holding these ports and take them over."
        )
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled")
        sys.exit(1)
