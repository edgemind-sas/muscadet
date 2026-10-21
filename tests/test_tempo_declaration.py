"""Portable declarations omit generated timer state names and keep custom names."""

import sys

import cod3s

import muscadet
from muscadet import declare


def _delete(system):
    """Release native state without replacing an active test failure."""
    preserve_error = sys.exc_info()[0] is not None
    try:
        try:
            system.deleteSys()
        finally:
            cod3s.terminate_session()
    except Exception:
        if not preserve_error:
            raise


def test_tempo_state_names_survive_round_trip_without_exporting_generated_names():
    system = muscadet.System(name="PortableTimers")
    try:
        comp = system.add_component(name="C", cls="ObjFlow", partial_init=True)
        for name in ("first", "second", "custom", "mixed"):
            custom = (
                {"state_enable_name": "ready", "state_disable_name": "waiting"}
                if name == "custom"
                else {}
            )
            if name == "mixed":
                custom = {"state_enable_name": "mixed_ready"}
            comp.add_flow(
                dict(
                    cls="FlowOutTempo",
                    name=name,
                    var_prod_default=True,
                    init_enable=name == "first",
                    occ_enable_flow={"cls": "delay", "time": 10},
                    **custom,
                )
            )
        comp.set_flows()
        document = declare.system_spec(system)
        flows = {f["name"]: f for f in document["components"]["C"]["flows"]}
        for name in ("first", "second"):
            assert "state_enable_name" not in flows[name]
            assert "state_disable_name" not in flows[name]
        assert flows["custom"]["state_enable_name"] == "ready"
        assert flows["custom"]["state_disable_name"] == "waiting"
        assert flows["mixed"]["state_enable_name"] == "mixed_ready"
        assert "state_disable_name" not in flows["mixed"]
    finally:
        _delete(system)

    rebuilt = declare.build_system(document)
    try:
        assert declare.system_spec(rebuilt) == document
        outputs = rebuilt.comp["C"].flows_out
        assert outputs["first"].state_enable_name == "first_enabled"
        assert outputs["second"].state_disable_name == "second_disabled"
        assert outputs["custom"].state_enable_name == "ready"
        assert outputs["mixed"].state_disable_name == "mixed_disabled"
        rebuilt.isimu_start()
        assert outputs["first"].var_fed.value() is True
        assert outputs["second"].var_fed.value() is False
        rebuilt.isimu_step_to(9.9)
        assert outputs["second"].var_fed.value() is False
        rebuilt.isimu_step_to(10)
        assert all(f.var_fed.value() for f in rebuilt.comp["C"].flows_out.values())
        rebuilt.isimu_stop()
    finally:
        _delete(rebuilt)
