import kb
import muscadet

class MySystem(muscadet.System):
    def __init__(self, name):
        super().__init__(name)

        self.add_component(cls="Source",
                           name="S")
        
        self.add_component(cls="Block",
                           name="C1")

        self.add_component(cls="Block",
                           name="C2")

        self.auto_connect("S", "C.")

        self.add_logic_or("LO__C", {"C.": ".*"},
                          )

        self.add_logic_or("LI__C", {"C.": ".*"},
                          on_available=True,
                          )

        param_s = dict(
            name="frun",
            failure_rate=1/5,
            repair_rate=1/3,
            failure_effects=[(".*_available_out", False)],
        )

        param_c1 = dict(
            name="frun",
            failure_time=7,
            repair_time=2,
            failure_effects=[(".*_available_out", False)],
        )

        param_c2 = dict(
            name="frun",
            failure_time=5,
            repair_time=3,
            failure_effects=[(".*_available_out", False)],
        )

        self.comp["S"].add_exp_failure_mode(**param_s)
        self.comp["C1"].add_delay_failure_mode(**param_c1)
        self.comp["C2"].add_delay_failure_mode(**param_c2)
