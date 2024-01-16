import pkg_resources
import muscadet

installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb



class Source(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_out(
            name="flow",
        )

class Bloc(muscadet.ObjFlow):

    def add_flows(self, **kwargs):

        super().add_flows(**kwargs)

        self.add_flow_in(
            name="flow",
        )

        self.add_flow_out(
            name="flow",
            var_prod_cond=[
                "flow",
            ]
        )

