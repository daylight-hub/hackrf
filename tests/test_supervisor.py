import sys
import time

from rfstudio.config import RESTART_ALWAYS, RESTART_NEVER, RESTART_ON_FAILURE, SupervisorSettings
from rfstudio.supervisor import LaunchSpec, State, Supervisor


def make(code: str, **policy):
    lines: list[str] = []
    states: list[State] = []
    events: list[str] = []
    pol = SupervisorSettings(backoff_initial_s=0.1, backoff_max_s=0.2, stop_timeout_s=2, **policy)
    sup = Supervisor(lambda: LaunchSpec([sys.executable, "-u", "-c", code]), pol,
                     on_output=lambda line, s: lines.append(f"{s}:{line}"),
                     on_state=states.append,
                     on_event=lambda lvl, m: events.append(f"{lvl}:{m}"))
    return sup, lines, states, events


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_restarts_on_crash_and_gives_up():
    sup, lines, states, events = make("import sys; print('hi'); sys.exit(3)",
                                      restart_policy=RESTART_ALWAYS, max_restarts=2, restart_window_s=60)
    sup.start()
    assert wait_for(lambda: sup.state == State.FAILED)
    assert sum(1 for line in lines if line == "stdout:hi") == 3
    assert sup.restart_count == 2
    assert any("Gave up" in e for e in events)


def test_on_failure_stops_after_clean_exit():
    sup, lines, states, _ = make("print('done')", restart_policy=RESTART_ON_FAILURE)
    sup.start()
    assert wait_for(lambda: not sup.is_active)
    assert sup.state == State.STOPPED
    assert lines == ["stdout:done"]


def test_never_policy_reports_failure():
    sup, _, _, _ = make("raise SystemExit(1)", restart_policy=RESTART_NEVER)
    sup.start()
    assert wait_for(lambda: not sup.is_active)
    assert sup.state == State.FAILED and sup.last_exit_code == 1


def test_stop_long_running_and_stderr():
    sup, lines, _, _ = make("import sys,time\nprint('err', file=sys.stderr)\nwhile True: time.sleep(0.1)")
    sup.start()
    assert wait_for(lambda: sup.state == State.RUNNING and lines)
    assert sup.pid is not None
    sup.stop(wait=True)
    assert sup.state == State.STOPPED and sup.pid is None
    assert "stderr:err" in lines


def test_manual_restart():
    sup, lines, _, _ = make("import time\nprint('up')\nwhile True: time.sleep(0.1)")
    sup.start()
    assert wait_for(lambda: lines.count("stdout:up") == 1)
    sup.restart()
    assert wait_for(lambda: lines.count("stdout:up") == 2)
    assert sup.restart_count == 0
    sup.stop()


def test_stall_detection():
    sup, lines, _, events = make("import time\nprint('x')\ntime.sleep(30)", stall_timeout_s=1, max_restarts=1)
    sup.start()
    assert wait_for(lambda: sup.state == State.FAILED, timeout=15)
    assert any("hung" in e for e in events)


def test_factory_error_is_retried():
    calls = []

    def factory():
        calls.append(1)
        raise FileNotFoundError("nope")

    sup = Supervisor(factory, SupervisorSettings(backoff_initial_s=0.05, max_restarts=2))
    sup.start()
    assert wait_for(lambda: sup.state == State.FAILED)
    assert len(calls) == 3


def test_stop_then_start_quickly():
    sup, lines, _, _ = make("import time\nprint('up')\nwhile True: time.sleep(0.1)")
    sup.start()
    assert wait_for(lambda: "stdout:up" in lines)
    sup.stop(wait=False)
    sup.start()
    assert wait_for(lambda: lines.count("stdout:up") == 2)
    assert sup.restart_count == 0
    sup.stop()
