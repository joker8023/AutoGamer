"""Tests: sidecar JSON-lines protocol + offline replay."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


class EventReader:
    """Background thread pumping sidecar stdout lines into a queue.

    Do NOT mix select() with a buffered reader: BufferedReader readahead
    makes select() report 'not readable' while lines sit in the buffer.
    """

    def __init__(self, proc):
        self.q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        self._thread.start()

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    try:
                        self.q.put(json.loads(line))
                    except json.JSONDecodeError:
                        self.q.put({"type": "_nonjson", "line": line})
        except Exception:
            pass

    def read(self, timeout=5.0):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


def wait_for(reader, want_type, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        ev = reader.read(timeout=max(0.1, end - time.time()))
        if ev is not None and ev.get("type") == want_type:
            return ev
    return None


def test_sidecar_protocol():
    from hsbot.cards import CardDB  # noqa: F401  (import sanity)
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        log_path = f.name
    proc = subprocess.Popen(
        [sys.executable, "-m", "hsbot.sidecar"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    reader = EventReader(proc)
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send({"type": "ping"})
        assert wait_for(reader, "pong") is not None, "no pong"
        send({"type": "start", "config": {"log_path": log_path, "dry_run": True}})
        started = wait_for(reader, "started")
        assert started is not None, "no started event"
        # Append log lines AFTER start (sidecar seeks to end on start).
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("D 10:15:23.1 GameState.DebugPrintPower() - TAG_CHANGE Entity=GameEntity tag=TURN value=2\n")
            f.write("D 10:15:23.1 GameState.DebugPrintPower() - TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION\n")
            f.write("D 10:15:23.1 GameState.DebugPrintPower() - TAG_CHANGE Entity=GameEntity tag=CURRENT_PLAYER value=1\n")
            f.flush()
        state_ev = None
        for _ in range(30):
            # Lines arrive one at a time; wait for the snapshot where BOTH
            # TURN and CURRENT_PLAYER have landed.
            ev = reader.read(timeout=3.0)
            if ev is None:
                break
            if (
                ev.get("type") == "state"
                and ev["state"]["turn"] == 2
                and ev["state"]["current_player"] == 1
            ):
                state_ev = ev
                break
        assert state_ev is not None, "no state event for turn 2"
        assert state_ev["state"]["is_my_turn"] is True
        send({"type": "pause"})
        assert wait_for(reader, "paused") is not None
        send({"type": "resume"})
        assert wait_for(reader, "resumed") is not None
        send({"type": "stop"})
        assert wait_for(reader, "stopped") is not None
        print("sidecar protocol OK")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        os.unlink(log_path)


def test_replay():
    from hsbot.cards import CardDB
    from hsbot.replay import replay_log
    db = CardDB.from_deck_json(os.path.join(ROOT, "data", "deck.json"))
    snaps = replay_log(os.path.join(ROOT, "samples", "sample_power.log"), db)
    assert len(snaps) > 0
    last = snaps[-1]
    assert last["turn"] == 1
    assert len(last["me"]["board"]) == 1  # the 1-cost minion went to PLAY
    assert len(last["me"]["hand"]) == 1
    # A plan was attached at my turn start.
    assert any("plan" in s for s in snaps), "no plan attached in replay"
    print(f"replay OK: {len(snaps)} snapshots")


if __name__ == "__main__":
    test_sidecar_protocol()
    test_replay()
    print("SIDECAR/REPLAY TESTS PASSED")
