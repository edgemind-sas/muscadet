import sys
import os
import pkg_resources
import pathlib
import muscadet
import pyctools
import Pycatshoo as pyc

installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb


class ObjC(muscadet.ObjFlow):

    def add_flows(self, elec_logic="or", **kwargs):

        super().add_flows()
        self.add_flow_in(name="elec", logic=elec_logic, **kwargs)


class ObjS(muscadet.ObjFlow):

    def add_flows(self, elec_prod_default=False, elec_prod_on_trigger=False, **kwargs):

        super().add_flows(**kwargs)

        if elec_prod_on_trigger:
            self.add_flow_out_on_trigger(
                name="elec",
                var_prod_default=elec_prod_default,
            )
        else:
            self.add_flow_out(
                name="elec",
                var_prod_default=elec_prod_default,
            )
        

class MySystem(pyctools.PycSystem):
    def __init__(self, name):
        super().__init__(name)

        self.comp = {}
        
        self.comp["C1"] = ObjC("C1")
        self.comp["C2"] = ObjC("C2")
        self.comp["S1"] = ObjS("S1", elec_prod_default=True)
        self.comp["S2"] = ObjS("S2",
                               elec_prod_on_trigger=True)

        self.connect("S1", "elec_out",
                     "C1", "elec_in")
        self.connect("S1", "elec_out",
                     "C2", "elec_in")
        self.connect("S2", "elec_out",
                     "C1", "elec_in")
        self.connect("S1", "elec_out",
                     "S2", "elec_trigger_in")
        self.connect("S2", "elec_out",
                     "C2", "elec_in")

        self.comp["S1"].add_atm2states(
            name="frun_det",
            occ_law_12={"dist": "delay", "time": 4},
            cond_occ_12="elec_fed_out",
            effects_12=[(".*fed_available_out", False)],
            occ_law_21={"dist": "delay", "time": 2},
        )
        
        self.comp["S2"].add_atm2states(
            name="frun",
            occ_law_12={"dist": "delay", "time": 5},
            cond_occ_12="elec_fed_out",
            effects_12=[(".*fed_available_out", False)],
            occ_law_21={"dist": "delay", "time": 3},
        )


system = MySystem("S")

system.loadParameters("system_params.xml")

system.dumpParameters("system_params_dump.xml", True)


system.add_indicator_var(
    component="C1",
    var="elec_fed",
    stats=["mean", "stddev"],
    )
system.add_indicator_var(
    component="C2",
    var="elec_fed",
    stats=["mean", "stddev"],
    )
system.add_indicator_var(
    component="S1",
    var="elec_fed_out",
    stats=["mean", "stddev"],
    )
system.add_indicator_var(
    component="S2",
    var="elec_fed_out",
    stats=["mean", "stddev"],
    )



interactive_session = pyctools.PycInteractiveSession(system=system)


interactive_session.run_session()
print(interactive_session.report_status())
interactive_session.step_forward()
print(interactive_session.report_status())



system.simulate(
    nb_runs=1,
    schedule=[{"start": 0, "end": 10, "nvalues": 10}]
)

analyser = pyc.CAnalyser(system)

seq_path = os.path.join(".")
analyser.printFilteredSeq(100,
                          os.path.join(seq_path, "sequences.xml"),
                          os.path.join(seq_path, "PySeq.xsl"))

fig_indics = system.indic_px_line()

if fig_indics:
    fig_indics_filename = os.path.join(".", "indics.html")
    fig_indics.write_html(fig_indics_filename)



sys.exit(0)
