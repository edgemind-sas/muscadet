"""What a platform interface may carry, and what it refuses (COD3S Platform #150).

``prod_conds: [["ctrl"]]`` -- one letter too many -- on a continuous output used
to be accepted without a word: the condition arrived empty and the valve stayed
open for the whole run. Capacities, rules, controllers and profile parameters
were already read through an allowlist; interfaces were not, which cost nothing
while a production condition could not sit on a continuous port and became a
valve that never closes once it could.

The decision this file locks, for a key an interface carries and no reader of
its cell (family x direction) reads:

- a value that says nothing (``None``, ``[]``, ``{}``) passes, whatever the key:
  the platform omits a default from what it serialises, so a newer platform
  sending an unset field must stay importable;
- a NEAR MISS of a key the cell reads is refused, naming the key it was
  probably meant to be;
- any other key is refused too, saying what to do: upgrade muscadet if the
  export comes from a more recent platform, drop the key otherwise. A warning
  would be written in the log of a worker no modeller reads, i.e. would be
  silent where it matters;
- a key KNOWN to another cell is refused naming the cells that read it.

Everything here is refused at parse time, so nothing needs an engine: this
module builds no system and carries no ``test_delete``.
"""

import copy
import json
from pathlib import Path

import pytest

from muscadet.importers.cod3s_platform import (
    CONTINUOUS_FAMILY,
    DISCRETE_FAMILY,
    _INTERFACE_KEYS_READ,
    Cod3sPlatformImportError,
    _parse_interface,
    parse_platform_export,
)

FIXTURES = Path(__file__).parent / "fixtures"

CELLS = [
    (DISCRETE_FAMILY, "input"),
    (DISCRETE_FAMILY, "output"),
    (CONTINUOUS_FAMILY, "input"),
    (CONTINUOUS_FAMILY, "output"),
]

#: One declaration per key a cell reads, chosen to SAY something (no neutral
#: value), so a key wrongly left out of the cell's allowlist is refused here.
#: A key added to the importer's table without a sample fails the coverage test
#: below rather than going untested.
SAMPLES = {
    (DISCRETE_FAMILY, "input"): {"input_logic": "and"},
    (DISCRETE_FAMILY, "output"): {
        "prod_cond": [["ctrl"]],
        "logic_inner_mode": "or",
        "negate": True,
        "flow_type": "tempo",
        "occ_enable": {"cls": "delay", "time": 3.0},
        "occ_disable": {"cls": "delay", "time": 1.0},
        "init_enable": True,
        "trigger_time_up": 2.0,
        "trigger_time_down": 1.0,
        "trigger_logic": "and",
    },
    (CONTINUOUS_FAMILY, "input"): {
        "demand_profile": {"cls": "constant", "value": 1.0},
        "var_in_default": 2.0,
    },
    (CONTINUOUS_FAMILY, "output"): {
        "production_profile": {"cls": "constant", "value": 2.0},
        "prod_cond": [["ctrl"]],
        "logic_inner_mode": "or",
        # The only value v1 accepts, which is also the neutral one.
        "allocation": "proportional",
    },
}


def _interface(family, direction, **keys):
    interface = {"name": "valve", "port_type": {"general": direction}, **keys}
    if family == CONTINUOUS_FAMILY:
        interface["flow_family"] = CONTINUOUS_FAMILY
    return interface


# ---------------------------------------------------------------------------
# The allowlist, both ways round
# ---------------------------------------------------------------------------


def test_every_key_a_cell_reads_has_a_sample():
    assert {cell: set(keys) for cell, keys in SAMPLES.items()} == {
        cell: set(keys) for cell, keys in _INTERFACE_KEYS_READ.items()
    }


@pytest.mark.parametrize("cell", CELLS, ids=lambda cell: "-".join(cell))
def test_every_key_a_cell_reads_is_accepted(cell):
    family, direction = cell
    for key, value in SAMPLES[cell].items():
        keys = {key: value}
        if key == "logic_inner_mode" and family == CONTINUOUS_FAMILY:
            # Alone it qualifies nothing, and is refused for that reason.
            keys["prod_cond"] = [["ctrl"]]
        _parse_interface(_interface(family, direction, **keys))


@pytest.mark.parametrize("cell", CELLS, ids=lambda cell: "-".join(cell))
def test_the_whole_serialised_platform_port_is_accepted(cell):
    # What the platform's ComponentInterface dumps on EVERY port, whatever its
    # cell: its identity, the two keys it does not hand to muscadet, and the
    # semantic fields at their defaults. Plus ``prod_init``, dead data that
    # released (immutable) libraries of the internal instance still carry.
    family, direction = cell
    _parse_interface(
        _interface(
            family,
            direction,
            metadata={"description": "a port"},
            component_authorized=["Tank"],
            prod_cond=None,
            input_logic=None,
            negate=False,
            logic_inner_mode="and",
            prod_init=True,
        )
    )


# ---------------------------------------------------------------------------
# A key no reader reads
# ---------------------------------------------------------------------------


class TestUnreadKeys:
    def test_the_measured_case_is_refused_naming_the_key_it_meant(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                _interface(CONTINUOUS_FAMILY, "output", prod_conds=[["ctrl"]])
            )
        message = str(excinfo.value)
        assert "'valve'" in message
        assert "'prod_conds'" in message
        assert "did you mean 'prod_cond'" in message
        # A suggestion is a hint, not a verdict: a key a newer platform added
        # may sit one letter away from one this cell reads.
        assert "upgrade muscadet" in message

    def test_a_near_miss_on_a_discrete_input_names_its_key(self):
        with pytest.raises(
            Cod3sPlatformImportError, match="did you mean 'input_logic'"
        ):
            _parse_interface(_interface(DISCRETE_FAMILY, "input", input_logik="and"))

    def test_a_near_miss_is_matched_against_its_own_cell(self):
        # ``production_profil`` is close to a key of the continuous OUTPUT; on
        # an input the suggestion would send the modeller to a second refusal.
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                _interface(
                    CONTINUOUS_FAMILY,
                    "input",
                    production_profil={"cls": "constant", "value": 1.0},
                )
            )
        assert "production_profile" not in str(excinfo.value).replace(
            "'production_profil'", ""
        )

    def test_an_unknown_key_is_refused_saying_what_to_do(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(_interface(CONTINUOUS_FAMILY, "output", valve_lag=2.0))
        message = str(excinfo.value)
        assert "'valve_lag'" in message
        assert "did you mean" not in message
        assert "upgrade muscadet" in message
        assert "more recent COD3S Platform" in message

    def test_every_offending_key_is_named_at_once(self):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(
                _interface(DISCRETE_FAMILY, "output", prod_conds=[["a"]], valve_lag=2.0)
            )
        message = str(excinfo.value)
        assert "did you mean 'prod_cond'" in message
        assert "'valve_lag'" in message

    @pytest.mark.parametrize("value", [None, [], {}], ids=["null", "list", "map"])
    @pytest.mark.parametrize("cell", CELLS, ids=lambda cell: "-".join(cell))
    def test_an_unknown_key_that_says_nothing_passes(self, cell, value):
        # A newer platform omits a default from what it serialises; one that
        # sends an unset field anyway still means what its absence means.
        family, direction = cell
        _parse_interface(_interface(family, direction, valve_lag=value))

    def test_refused_through_the_whole_import(self):
        # A real platform export, whose conditions are not empty: an empty one
        # renamed would say nothing and rightly pass.
        payload = json.loads((FIXTURES / "dil_v2_export.json").read_text())
        mutated = copy.deepcopy(payload)
        kb = mutated.get("kb_embedded") or mutated["kb"]
        interface = next(
            interface
            for template in kb["component_templates"].values()
            for interface in template.get("interfaces", {}).values()
            if interface.get("prod_cond")
        )
        interface["prod_conds"] = interface.pop("prod_cond")
        with pytest.raises(Cod3sPlatformImportError, match="prod_conds"):
            parse_platform_export(mutated)
        # The fixture itself still imports: the refusal is the typo's.
        parse_platform_export(payload)


# ---------------------------------------------------------------------------
# A key another cell reads
# ---------------------------------------------------------------------------


class TestKeysOfAnotherCell:
    @pytest.mark.parametrize(
        "family, direction, key, value, reader",
        [
            # The two directions of the continuous family, each way round.
            (
                CONTINUOUS_FAMILY,
                "input",
                "production_profile",
                {"cls": "constant", "value": 2.0},
                "continuous output",
            ),
            (
                CONTINUOUS_FAMILY,
                "output",
                "demand_profile",
                {"cls": "constant", "value": 2.0},
                "continuous input",
            ),
            (CONTINUOUS_FAMILY, "output", "var_in_default", 1.0, "continuous input"),
            (CONTINUOUS_FAMILY, "input", "allocation", "priority", "continuous output"),
            # The mirror gap the continuous direction guard left on the
            # discrete family: a condition on an input conditions nothing.
            (DISCRETE_FAMILY, "input", "prod_cond", [["a"]], "discrete output"),
            (DISCRETE_FAMILY, "input", "negate", True, "discrete output"),
            (DISCRETE_FAMILY, "input", "flow_type", "tempo", "discrete output"),
            (DISCRETE_FAMILY, "output", "input_logic", "and", "discrete input"),
            (DISCRETE_FAMILY, "input", "var_in_default", True, "continuous input"),
        ],
    )
    def test_refused_naming_where_it_is_read(
        self, family, direction, key, value, reader
    ):
        with pytest.raises(Cod3sPlatformImportError) as excinfo:
            _parse_interface(_interface(family, direction, **{key: value}))
        message = str(excinfo.value)
        assert repr(key) in message
        assert f"{family} {direction}".upper() in message.upper()
        assert reader in message
        assert "did you mean" not in message
