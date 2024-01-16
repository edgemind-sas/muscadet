import kb
import pkg_resources
import muscadet

installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb


class MySystem(muscadet.System):
    def __init__(self, name):
        super().__init__(name)

        #self.comp = {}

        self.add_component(cls="Source",
                           name="S")
        
        self.add_component(cls="Bloc",
                           name="C1")

        self.add_component(cls="Bloc",
                           name="C2")

        self.auto_connect("S", "C.")

        self.add_logic_or("LO__C", {"C.": ".*"},
 #                         negate=True,
                          )

        self.add_logic_or("LI__C", {"C.": ".*"},
                          on_available=True,
#                          negate=True,
                          )

        param_s = dict(
            name="frun",
            failure_time=18,
            repair_time=2,
            failure_effects=[(".*_available_out", False)],
        )

        param_c = dict(
            name="frun",
            failure_time=12,
            repair_time=2,
            failure_effects=[(".*_available_out", False)],
        )
        
        self.comp["S"].add_delay_failure_mode(**param_s)        
        self.comp["C1"].add_delay_failure_mode(**param_c)
        self.comp["C2"].add_delay_failure_mode(**param_c)

