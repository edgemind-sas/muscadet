import sys
import os
import pkg_resources
import pathlib
import pyctools
import system
import Pycatshoo as pyc
import logging

installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb

# Create a custom formatter
formatter = logging.Formatter('[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
# Create a console handler with level DEBUG; it will print everything
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
# Set the formatter for the console handler
console_handler.setFormatter(formatter)
# Get a reference to the root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)  # Set the minimum log level that will be handled
# Add the console handler to the root logger
logger.addHandler(console_handler)




system_model = system.MySystem("S")

system_model.generate_system_graph(
    filename="system_graph.html",
    config={},
)


system_model.loadParameters("system_params.xml")

system_model.dumpParameters("system_params_dump.xml", True)


system_model.add_indicator_var(
 #   name="C1",
    component=".*",
    var=".*fed_out$",
    value_test=True,
    stats=["mean", "stddev"],
    )

#sys.exit(0)

# interactive_session = pyctools.PycInteractiveSession(system=system_model)

# interactive_session.run_session()
# print(interactive_session.report_status())
# #ipdb.set_trace()
# interactive_session.step_forward()
# print(interactive_session.report_status())
#ipdb.set_trace()
# interactive_session.step_forward()
# print(interactive_session.report_status())

#sys.exit(0)

# system_model.simulate(
#     nb_runs=10000000,
#     schedule=[{"start": 0, "end": 24, "nvalues": 23}]
# )
#ipdb.set_trace()

# system_model.addTarget("target",
#                        system_model.comp["C2"].flows_out["flow"].var_fed,
#                        "!=", 1)

# system_model.addTarget("target", "C2.flow_fed_out", "VAR")                                    
# system_model.setKeepFilteredSeqForInd(False)

logger.info("Start simulation")
# nb_runs=10000000 => ok 16 min
system_model.simulate(
    nb_runs=100000,
    schedule=[{"start": 0, "end": 20, "nvalues": 100}]
)
logger.info("End simulation")

#analyser = pyc.CAnalyser(system_model)
#analyser.keepFilteredSeq(True)

# seq_path = os.path.join(".")
# analyser.printFilteredSeq(100,
#                           os.path.join(seq_path, "sequences.xml"),
#                           os.path.join(seq_path, "PySeq.xsl"))

#ipdb.set_trace()


fig_indics = system_model.indic_px_line(
    facet_row="comp",
    color="attr",
)

if fig_indics:
    fig_indics_filename = os.path.join(".", "indics.html")
    fig_indics.write_html(fig_indics_filename)



sys.exit(0)
