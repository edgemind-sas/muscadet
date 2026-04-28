import cod3s

from .obj_logic import LogicOr, LogicAnd
import re
import copy


class System(cod3s.PycSystem):

    def auto_connect(
        self,
        source,
        target,
        available_connect=False,
        logger=None,
    ):

        obj_source_list = [
            obj for obj in self.comp.keys() if re.search(f"^({source})$", obj)
        ]

        conn_list = []
        for src in obj_source_list:

            conn_list += [
                {
                    "source": src,
                    "target": obj,
                }
                for obj in self.comp.keys()
                if re.search(f"^({source})({target})$", src + obj)
            ]

        connections_created = []

        for conn in conn_list:
            # Test to ensure source is different from target
            # Could happen with regex
            if conn["source"] != conn["target"]:
                conn_created_cur = self.auto_connect_flows(
                    source=conn["source"],
                    target=conn["target"],
                    available_connect=available_connect,
                    logger=logger,
                )
                connections_created.extend(conn_created_cur)

        return connections_created

    def auto_connect_flows(
        self,
        source,
        target,
        available_connect=False,
        logger=None,
    ):
        """
        Connects flows between a source and a target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            available_connect (bool, optional): Whether to include an "_available" suffix in the flow name. Defaults to False.
            logger (logging.Logger, optional): Logger for debug messages. Defaults to None.

        Returns:
            list: A list of connections created.
        """

        connections_list = []

        available_suffix = "_available" if available_connect else ""

        for flow_out in self.comp[source].flows_out:

            if flow_out in self.comp[target].flows_in:
                flow_name = f"{flow_out}{available_suffix}"

                connection = self.connect_flow(
                    source=source,
                    target=target,
                    flow_name=flow_name,
                    flow_key=flow_out,
                    logger=logger,
                )
                if not (connection is None):
                    connections_list.append(connection)
        return connections_list

    def check_comp_attributes(self, comp_name, attr_cond_list):
        comp = self.comp[comp_name]

        return any(
            [
                all(
                    [
                        (
                            re.search(f"^{val}$", getattr(comp, attr))
                            if isinstance(val, str)
                            else getattr(comp, attr) == val
                        )
                        for attr, val in auth_cond.items()
                    ]
                )
                for auth_cond in attr_cond_list
            ]
        )

    def connect_flow(
        self,
        source,
        target,
        flow_name,
        out_suffix="_out",
        in_suffix="_in",
        check_authorization=True,
        flow_key=None,
        logger=None,
    ):
        """
        Connects a specific flow between a source and target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            flow_name (str): The flow message-box prefix used to build the
                source/target message boxes (i.e. ``{flow_name}{out_suffix}``
                and ``{flow_name}{in_suffix}``). May carry an ``_available``
                suffix when wiring the availability channel.
            out_suffix (str): Source message-box suffix.
            in_suffix (str): Target message-box suffix.
            check_authorization (bool): When True, validate connection against
                ``component_authorized`` patterns declared on the flow.
            flow_key (str, optional): Key used to look up the flow definition
                in ``flows_out``/``flows_in`` for the authorization check.
                Defaults to ``flow_name`` (backward-compatible). Must be set
                explicitly when ``flow_name`` carries a suffix that is not a
                key in the flow dicts (e.g. ``"f1_available"``).
            logger (logging.Logger, optional): Logger for debug messages.

        Returns:
            dict or None: The connection details if created, otherwise None.
        """
        if flow_key is None:
            flow_key = flow_name

        connection = None
        if self.comp[source].is_connected_to(target, flow_name):
            if not (logger is None):
                logger.debug(f"!!! {source} -- {flow_name} --> {target} already exists")
        else:

            if check_authorization:
                source_flow_comp_auth_pat = (
                    self.comp[source].flows_out[flow_key].component_authorized
                )
                check_source_auth = self.check_comp_attributes(
                    target, source_flow_comp_auth_pat
                )
                if not check_source_auth:
                    if logger is not None:
                        logger.debug(
                            f"!!! {source} -- {flow_name} --> {target} not authorized by {source}"
                        )
                    return None

                target_flow_comp_auth_pat = (
                    self.comp[target].flows_in[flow_key].component_authorized
                )
                check_target_auth = self.check_comp_attributes(
                    source, target_flow_comp_auth_pat
                )
                if not check_target_auth:
                    if logger is not None:
                        logger.debug(
                            f"!!! {source} -- {flow_name} --> {target} not authorized by {target}"
                        )
                    return None

            self.connect(
                source, f"{flow_name}{out_suffix}", target, f"{flow_name}{in_suffix}"
            )
            connection = {
                "source": source,
                "flow": flow_name,
                "target": target,
            }

            if not (logger is None):
                logger.debug(f"{source} -- {flow_name} --> {target}")
        return connection

    def connect_trigger(
        self,
        source,
        target,
        flow_name,
        logger=None,
    ):
        """
        Connects output flow from the source component to the trigger_in flow of the target component.

        Args:
            source (str): The source component.
            target (str): The target component.
            flow_name (str): The name of the flow to connect.
            logger (logging.Logger, optional): Logger for debug messages. Defaults to None.
        """
        self.connect_flow(
            source=source,
            target=target,
            flow_name=flow_name,
            out_suffix="_out",
            in_suffix="_trigger_in",
            check_authorization=False,
        )

    def clean_comp_flow_specs(self, comp_flow_specs):
        # Scan input components
        comp_flow_specs_clean = copy.deepcopy(comp_flow_specs)
        if isinstance(comp_flow_specs_clean, list):
            comp_flow_specs_clean = {k: ".*" for k in comp_flow_specs_clean}
        elif isinstance(comp_flow_specs_clean, dict):
            pass
        else:
            raise ValueError(
                f"Component/flow specification {type(comp_flow_specs_clean)} not supported"
            )
        return comp_flow_specs_clean

    def get_comp_flow_in_from_specs(self, comp_flow_specs):

        comp_flow_specs_clean = self.clean_comp_flow_specs(comp_flow_specs)

        flows_in = []
        comp_in = []
        for comp_pat, flow_pat in comp_flow_specs_clean.items():
            comp_list = [
                obj for obj in self.comp.keys() if re.search(f"^({comp_pat})$", obj)
            ]
            for comp_name in comp_list:
                flows_in_new = [
                    flow
                    for flow in self.comp[comp_name].flows_out
                    if re.search(f"^({flow_pat})$", flow)
                ]

                if flows_in_new:
                    flows_in.extend(flows_in_new)
                    comp_in.append(comp_name)

        return list(set(comp_in)), list(set(flows_in))

    def add_logic_or(self, name, comp_in_specs, on_available=False, **params):
        """ """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            for comp_in_name_cur in comp_in:
                comp_in_cur = self.comp[comp_in_name_cur]
                for key, val in comp_in_cur.metadata.items():
                    metadata.setdefault(key, val)

        self.add_component(
            cls="LogicOr",
            name=name,
            flows_in=flows_in,
            var_in_default=on_available,
            var_available_in_default=not on_available,
            metadata=metadata,
            **params,
        )

        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)

    def add_logic_and(self, name, comp_in_specs, on_available=False, **params):
        """ """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            for comp_in_name_cur in comp_in:
                comp_in_cur = self.comp[comp_in_name_cur]
                for key, val in comp_in_cur.metadata.items():
                    metadata.setdefault(key, val)

        self.add_component(
            cls="LogicAnd",
            name=name,
            flows_in=flows_in,
            var_in_default=on_available,
            var_available_in_default=not on_available,
            metadata=metadata,
            **params,
        )

        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)
