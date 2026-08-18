"""PDMP manager ownership: one manager per system, created lazily, released on delete.

``muscadet.System`` owns a single PDMP manager. It is created on the first
continuous declaration and never before -- a system whose components declare
only discrete flows must not carry one at all, which is what keeps purely
discrete models exactly what they were before the continuous layer existed.

The module also pins down the property every later unit depends on: PyCATSHOO
does **not** freeze PDMP registration at component construction. ODE variables,
equation methods and watched transitions can all be registered from the pre-run
step -- long after every component was built and connected -- and the solver
honours them. That is asserted end to end here, on the interactive path.
"""

import Pycatshoo as pyc
import muscadet
import cod3s
import pytest

FILL_RATE_T1 = 2.0
FILL_RATE_T2 = 3.0
CLOCK_DELAY = 5.0


class DiscreteSrc(muscadet.ObjFlow):
    """Discrete-only producer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_out(name="x", var_prod_default=True)


class DiscreteSnk(muscadet.ObjFlow):
    """Discrete-only consumer."""

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_in(name="x", logic="or")


class TankOut(muscadet.ObjFlow):
    """Continuous producer holding a level integrated by the PDMP solver.

    The level variable is *declared* here, but deliberately **not** registered:
    registration happens from the system's pre-run step, which is the timing
    this module exists to validate.
    """

    def __init__(self, name, fill_rate=FILL_RATE_T1, **kwargs):
        super().__init__(name, **kwargs)
        self.fill_rate = fill_rate
        self.v_level = self.addVariable("level", pyc.TVarType.t_double, 0.0)

    def add_flows(self, **kwargs):
        super().add_flows(**kwargs)
        self.add_flow_continuous_out(name="q", var_fed_default=1.0)

    def compute_level(self):
        self.v_level.setDvdtODE(self.fill_rate)


class TankIn(TankOut):
    """Same, on the consuming side of the continuous flow."""

    def add_flows(self, **kwargs):
        muscadet.ObjFlow.add_flows(self, **kwargs)
        self.add_flow_continuous_in(name="q")


class PdmpSystem(muscadet.System):
    """Registers its components' equations from the pre-run step.

    Every registration below happens after the whole system was built and
    connected -- the position KTD14 puts the ordering computation at.
    """

    def prerun_step(self):
        self.prerun_registrations = []

        for order, comp in enumerate(self.get_components("T[0-9]+").values()):
            self.pdmp_add_ode_variable(comp.v_level)
            self.pdmp_add_equation_method("compute_level", comp, order)
            self.prerun_registrations.append((comp.basename(), order))

            for aut in comp.automata_d.values():
                for trans in aut.transitions:
                    # Passed as the cod3s wrapper, not the backend object:
                    # the helper is expected to unwrap it.
                    self.pdmp_add_watched_transition(trans)

        return super().prerun_step()


@pytest.fixture(scope="module")
def the_run():
    """Build the discrete system, then the continuous one, snapshotting both.

    PyCATSHOO forbids two live systems in one process, so the discrete-only
    system is fully exercised and deleted before the continuous one is built.
    """
    obs = {}

    # -- A purely discrete system never creates a manager -----------------
    dsys = muscadet.System(name="PdmpDiscreteOnly")
    dsys.add_component(name="SRC", cls="DiscreteSrc")
    dsys.add_component(name="SNK", cls="DiscreteSnk")
    dsys.auto_connect("SRC", "SNK")
    obs["discrete_manager_after_build"] = dsys.pdmp_manager

    dsys.isimu_start()
    obs["discrete_manager_after_prerun"] = dsys.pdmp_manager
    obs["discrete_prerun_count"] = dsys.prerun_count
    obs["discrete_fed"] = dsys.comp["SNK"].flows_in["x"].var_fed.value()
    dsys.isimu_stop()
    dsys.deleteSys()

    # -- A continuous system creates exactly one manager, and reuses it ---
    csys = PdmpSystem(name="PdmpContinuous")
    obs["cont_manager_before_any_component"] = csys.pdmp_manager

    csys.add_component(name="T1", cls="TankOut", fill_rate=FILL_RATE_T1)
    obs["cont_manager_after_first"] = csys.pdmp_manager

    csys.add_component(name="T2", cls="TankIn", fill_rate=FILL_RATE_T2)
    obs["cont_manager_after_second"] = csys.pdmp_manager

    # A discrete-only component added afterwards changes nothing
    csys.add_component(name="D1", cls="DiscreteSrc")
    obs["cont_manager_after_discrete"] = csys.pdmp_manager

    csys.auto_connect("T1", "T2")

    # A clock giving the interactive session something to step to, so the
    # solver actually integrates between two dates.
    csys.comp["T1"].add_atm2states(
        name="clock",
        st1="s0",
        st2="s1",
        occ_law_12={"cls": "delay", "time": CLOCK_DELAY},
        cond_occ_21=False,
    )

    obs["cont_manager_before_prerun"] = csys.pdmp_manager
    obs["cont_prerun_count_before"] = csys.prerun_count

    csys.isimu_start()
    obs["cont_registrations"] = list(csys.prerun_registrations)
    obs["cont_prerun_count_after_start"] = csys.prerun_count
    obs["cont_manager_after_prerun"] = csys.pdmp_manager
    obs["level_t1_at_start"] = csys.comp["T1"].v_level.value()
    obs["level_t2_at_start"] = csys.comp["T2"].v_level.value()

    csys.isimu_step_forward()
    obs["time_after_step"] = csys.currentTime()
    obs["level_t1_after_step"] = csys.comp["T1"].v_level.value()
    obs["level_t2_after_step"] = csys.comp["T2"].v_level.value()
    csys.isimu_stop()

    obs["system"] = csys
    return obs


def test_discrete_only_system_creates_no_manager(the_run):
    """No continuous declaration, no PDMP manager -- before or after the run."""
    assert the_run["discrete_manager_after_build"] is None
    assert the_run["discrete_manager_after_prerun"] is None
    assert the_run["discrete_prerun_count"] == 1
    assert the_run["discrete_fed"] is True


def test_first_continuous_declaration_creates_the_manager(the_run):
    """The manager appears with the first component carrying a continuous flow."""
    assert the_run["cont_manager_before_any_component"] is None
    assert the_run["cont_manager_after_first"] is not None
    assert the_run["cont_manager_after_first"].basename() == "muscadet_pdmp"


def test_further_declarations_reuse_the_same_manager(the_run):
    """One manager per system: the second continuous component reuses it."""
    first = the_run["cont_manager_after_first"]

    assert the_run["cont_manager_after_second"] is first
    assert the_run["cont_manager_after_discrete"] is first
    assert the_run["cont_manager_before_prerun"] is first
    assert the_run["cont_manager_after_prerun"] is first


def test_get_or_create_is_idempotent(the_run):
    """The accessor components reach through ``self.system()`` never rebuilds."""
    system = the_run["system"]

    assert system.get_or_create_pdmp_manager() is the_run["cont_manager_after_first"]
    assert system.get_or_create_pdmp_manager() is system.pdmp_manager


def test_registration_from_the_prerun_step_is_accepted(the_run):
    """PyCATSHOO does not freeze registration at component construction.

    The equations, ODE variables and watched transitions below were all
    registered from the pre-run step -- after every component existed and the
    graph was connected -- and the solver integrated them.
    """
    assert the_run["cont_registrations"] == [("T1", 0), ("T2", 1)]
    assert the_run["cont_prerun_count_before"] == 0
    assert the_run["cont_prerun_count_after_start"] == 1

    # Nothing has been integrated yet at t = 0
    assert the_run["level_t1_at_start"] == pytest.approx(0.0)
    assert the_run["level_t2_at_start"] == pytest.approx(0.0)

    # One step to the clock date: each level integrated at its own rate, so
    # both equation methods ran, on their own component's variable.
    assert the_run["time_after_step"] == pytest.approx(CLOCK_DELAY)
    assert the_run["level_t1_after_step"] == pytest.approx(
        FILL_RATE_T1 * CLOCK_DELAY, rel=1e-6
    )
    assert the_run["level_t2_after_step"] == pytest.approx(
        FILL_RATE_T2 * CLOCK_DELAY, rel=1e-6
    )


def test_equation_order_must_be_an_explicit_int(the_run):
    """A distinct integer order is mandatory: PyCATSHOO falls back to
    alphabetical equation-name order when two equations share one, so a
    derived evaluation order only holds if the caller allocates them."""
    system = the_run["system"]
    comp = system.comp["T1"]

    with pytest.raises(TypeError, match="must be an int"):
        system.pdmp_add_equation_method("compute_level", comp, 1.5)

    with pytest.raises(TypeError, match="must be an int"):
        system.pdmp_add_equation_method("compute_level", comp, None)


def test_registration_helpers_create_the_manager_on_demand(the_run):
    """Each helper is a valid first continuous declaration on its own."""
    system = the_run["system"]

    assert system.pdmp_add_ode_variable(system.comp["T2"].v_level) is (
        system.pdmp_manager
    )
    assert system.pdmp_add_explicit_variable(
        system.comp["T1"].flows_out["q"].var_fed
    ) is (system.pdmp_manager)


def test_delete(the_run):
    """Teardown releases the manager so a following module starts clean."""
    system = the_run["system"]

    assert system.pdmp_manager is not None
    system.deleteSys()
    assert system.pdmp_manager is None

    cod3s.terminate_session()
