import json

from app.services.game_state import read_fight_state
from app.services.ram_debug import RamDebugRecorder


class ContractBridge:
    def get_ram_features(self):
        return {
            "mk4_state_payload": {
                "version": "mk4_core_v1",
                "available": True,
                "frame_id": 12,
                "p1_health_word": 0x00010000,
                "p2_health_word": 0x0000C000,
                "p1_health": 160,
                "p2_health": 120,
                "timer": 86,
                "timer_raw": 86,
                "p1_x": -2.0,
                "p2_x": 3.0,
                "p1_airborne": 0.0,
                "p2_airborne": 0.0,
                "p1_y_vel": 0.0,
                "p1_facing": 1,
                "p2_facing": -1,
            }
        }


class DirectBridge:
    def __init__(self):
        self.outputs = {
            "mem /1w 0x800fe0d8": "800FE0D8:  00010000\n(dbg)\n",
            "mem /1w 0x80126f54": "80126F54:  00008000\n(dbg)\n",
            "mem /1b 0x8010511b": "8010511B:  56\n(dbg)\n",
            "mem /1w 0x80105118": "80105118:  00000056\n(dbg)\n",
            "mem /1w 0x800f87f8": "800F87F8:  FFFE0000\n(dbg)\n",
            "mem /1w 0x8006a060": "8006A060:  00030000\n(dbg)\n",
            "mem /1w 0x800fe0f8": "800FE0F8:  00000004\n(dbg)\n",
            "mem /1w 0x80126f78": "80126F78:  00000000\n(dbg)\n",
            "mem /1w 0x800fe90c": "800FE90C:  00000738\n(dbg)\n",
        }

    def debugger_command(self, command, **kwargs):
        return {"output": self.outputs[command]}


class MixedBridge(DirectBridge):
    def get_ram_features(self):
        return {
            "mk4_state_payload": {
                "version": "mk4_core_v1",
                "available": True,
                "frame_id": 0,
                "p1_health_word": 2916,
                "p2_health_word": 65536,
                "p1_health": 5,
                "p2_health": 160,
                "timer": 0,
                "timer_raw": 0,
                "p1_x": 0.0,
                "p2_x": -2.0,
                "p1_airborne": 0.0,
                "p2_airborne": 0.0,
                "p1_y_vel": 0.0,
                "p1_facing": -1,
                "p2_facing": 1,
            }
        }


def test_read_fight_state_contract_debug_info():
    state = read_fight_state(ContractBridge(), frame_id=7)

    assert state.timer == 86
    assert state.debug_info["state_source"] == "contract"
    assert state.debug_info["contract_payload"]["timer_raw"] == 86
    assert "direct_probe" in state.debug_info


def test_read_fight_state_direct_debug_info():
    state = read_fight_state(DirectBridge(), frame_id=9)

    assert state.timer == 0x56
    assert state.p1_health == 160
    assert state.p2_health == 80
    assert state.debug_info["state_source"] == "direct"
    assert state.debug_info["direct_probe"]["timer_raw"]["value"] == 0x56
    assert state.debug_info["direct_probe"]["timer_word_u32"]["value"] == 0x56


def test_read_fight_state_prefers_direct_probe_over_bad_contract():
    state = read_fight_state(MixedBridge(), frame_id=11)

    assert state.debug_info["state_source"] == "direct"
    assert state.p1_health == 160
    assert state.p2_health == 80
    assert state.timer == 0x56
    assert state.debug_info["contract_payload"]["p1_health"] == 5


def test_ram_debug_recorder_writes_jsonl(tmp_path):
    recorder = RamDebugRecorder(
        match_id="match-1",
        instance_id="instance-1",
        log_dir=tmp_path,
    )

    recorder.record_event("round_started", round=1)
    recorder.record({"kind": "sample", "round": 1, "step": 1, "timer": 86})

    lines = recorder.file_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["event"] == "round_started"
    assert second["kind"] == "sample"
    assert second["timer"] == 86
