import pyctools
# import Pycatshoo as pyc
from .obj import ObjBase
from .obj_logic import LogicOr
import re
import copy
import logging
from pyvis.network import Network
import graphviz
import pkg_resources
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
# ipdb is a debugger (pip install ipdb)
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401


class System(pyctools.PycSystem):
    def __init__(self, name):
        super().__init__(name)

        self.comp = {}

    def add_component(self, **comp_specs):

        comp_name = comp_specs.get("name")
        if comp_name in self.comp:
            raise ValueError(f"Component {comp_name} already exists")
        else:

            # if "SPF" in comp_name:
            #     ipdb.set_trace()

            comp_new = ObjBase.from_dict(**comp_specs)
                    
        return comp_new

    def get_components(self, pattern="^.*$"):
        return {k: v for k, v in self.comp.items()
                if re.search(f"^({pattern})$", k)}

    # A INTEGRER ICI
    # @staticmethod
    # def prepare_connection(components_list,
    #                        comp_source,
    #                        mb_source,
    #                        comp_target,
    #                        mb_target):
        
    #     comp_source_list = [comp for comp in components_list
    #                         if re.search(comp_source, comp)]

    #     conn_list = []
    #     for comp_src in comp_source_list:
            
    #         conn_list += [{
    #             "comp_source": comp_src,
    #             "mb_source": mb_source,
    #             "comp_target": comp,
    #             "mb_target": mb_target,
    #         } for comp in components_list
    #                       if re.search(comp_source + comp_target,
    #                                    comp_src + comp)]

    #     return conn_list

            
    def auto_connect(self, source, target,
                     available_connect=False,
                     ):

        obj_source_list = [obj for obj in self.comp.keys()
                           if re.search(f"^({source})$", obj)]

        # if "?P" in target:
        #     ipdb.set_trace()  

        conn_list = []
        for src in obj_source_list:
            
            conn_list += [{
                "source": src,
                "target": obj,
            } for obj in self.comp.keys()
                          if re.search(f"^({source})({target})$",
                                       src + obj)]

        # if "?P" in target:
        #     ipdb.set_trace()  

        # print(target) 
        # if target == "PAS_01": 
        #     print([(src, obj) for obj in self.comp.keys() if re.search(f"^({source})({target})$", src + obj)])  
        #     ipdb.set_trace()  
        # if source == "ReseauSTD": *)
        #     ipdb.set_trace() *)
        for conn in conn_list:
            # Test to ensure source is different from target
            # Could happen with regex
            if conn["source"] != conn["target"]:
                self.auto_connect_flows(source=conn["source"],
                                        target=conn["target"],
                                        available_connect=available_connect,
                                        )
            
    def auto_connect_flows(self, source, target,
                           available_connect=False,
                           ):

        available_suffix = "_available" if available_connect else ""

        for flow_out in self.comp[source].flows_out:
            # if source == "ReseauSTD" and target == "PAS_ZA01_A": *)
            #     ipdb.set_trace() *)

            # if target == "PAS_01": 
            #     print(source, target)
            #     ipdb.set_trace()  

            if flow_out in self.comp[target].flows_in:
                try:
                    self.connect(source, f"{flow_out}{available_suffix}_out",
                                 target, f"{flow_out}{available_suffix}_in")
                except Exception as e:
                    if "existe" in str(e):
                        if not (logging is None):
                            logging.debug(f"!!! {source} -- {flow_out}{available_suffix} --> {target} already exists")
                    else:
                        raise e

                else:
                    if not (logging is None):
                        logging.debug(f"{source} -- {flow_out}{available_suffix} --> {target}")

    def clean_comp_flow_specs(self, comp_flow_specs):
        # Scan input components
        comp_flow_specs_clean = copy.deepcopy(comp_flow_specs)
        if isinstance(comp_flow_specs_clean, list):
            comp_flow_specs_clean = {k: ".*" for k in comp_flow_specs_clean}
        elif isinstance(comp_flow_specs_clean, dict):
            pass
        else:
            raise ValueError(f"Component/flow specification {type(comp_flow_specs_clean)} not supported")
        return comp_flow_specs_clean

    def get_comp_flow_in_from_specs(self, comp_flow_specs):

        comp_flow_specs_clean = \
            self.clean_comp_flow_specs(comp_flow_specs)
        
        flows_in = []
        comp_in = []
        for comp_pat, flow_pat in comp_flow_specs_clean.items():
            comp_list = [obj for obj in self.comp.keys()
                         if re.search(f"^({comp_pat})$", obj)]
            for comp_name in comp_list:
                flows_in_new = \
                    [flow for flow in self.comp[comp_name].flows_out
                     if re.search(f"^({flow_pat})$", flow)]

                if flows_in_new:
                    flows_in.extend(flows_in_new)
                    comp_in.append(comp_name)
        
        return list(set(comp_in)), list(set(flows_in))


    
    def add_logic_or(self, name, comp_in_specs, on_available=False, **params):
        """
        """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            comp_1 = self.comp[comp_in[0]]
            for key, val in comp_1.metadata.items():
                metadata.setdefault(key, val)

        self.add_component(cls="LogicOr",
                           name=name,
                           flows_in=flows_in,
                           var_in_default=on_available,
                           var_available_in_default=not on_available,
                           metadata=metadata,
                           **params)
        
        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)

    def add_logic_and(self, name, comp_in_specs, on_available=False, **params):
        """
        """
        comp_in, flows_in = self.get_comp_flow_in_from_specs(comp_in_specs)

        # Set metadata from input comp if needed
        metadata = params.pop("metadata", {})
        if comp_in:
            comp_1 = self.comp[comp_in[0]]
            for key, val in comp_1.metadata.items():
                metadata.setdefault(key, val)

        self.add_component(cls="LogicAnd",
                           name=name,
                           flows_in=flows_in,
                           var_in_default=on_available,
                           var_available_in_default=not on_available,
                           metadata=metadata,
                           **params)
        
        for comp in comp_in_specs:
            self.auto_connect(comp, name, available_connect=on_available)

    def get_system_graph_specs(self, config={}):

        config_copy = copy.deepcopy(config)
        
        components_dict = {}
        for comp_name, comp in self.comp.items():

            comp_specs = {
                "name": comp.basename(),
                "label": comp.basename(),
            }
            
            for comp_style_id, comp_style in config_copy.get("components", {}).items():

                comp_pattern = comp_style.get("pattern", None)

                # if comp_name.startswith("PAS"): *)
                #     ipdb.set_trace() *)

                if comp_pattern:
                    if re.search(comp_pattern, comp_name):
                        comp_specs.update(comp_style)

            components_dict[comp_name] = comp_specs
                #sys_graph.add_node(comp.basename(), **comp_style_cur)
                #sys_graph.node(comp.basename())

        flows_list = []
        for comp_name, comp in self.comp.items(): 
            for flow_name, flow in comp.flows_out.items(): 
                
                msg_box_out = comp.getMessageBox(f"{flow_name}_out") 

                for cnx in range(msg_box_out.cnctCount()): 
                    
                    comp_target = msg_box_out.cnct(cnx).parent() 

                    comp_source_name = comp_name
                    comp_target_name = comp_target.basename()

                    flow_specs = {
                        "source": comp_source_name,
                        "target": comp_target_name,
                        "flow_name": flow_name,
                    }

                    for flow_style_id, flow_style in config_copy.get("flows", {}).items():
                        comp_source_pattern = flow_style.get("source_pattern", ".*")
                        comp_target_pattern = flow_style.get("target_pattern", ".*")
                        flow_pattern = flow_style.get("flow_pattern", ".*")
                    
                        if re.search(comp_source_pattern, comp_source_name) \
                           and re.search(comp_target_pattern, comp_target_name) \
                           and re.search(flow_pattern, flow_name):
                            flow_specs.update(flow_style)

                    flows_list.append(flow_specs)

        return components_dict, flows_list
                    
        

    def generate_system_graph(self,
                              filename="system.html",
                              config={}):

        sys_graph = graphviz.Digraph(engine="neato")
        sys_graph.attr(
            label=self.name(),
            splines='true',
            overlap='false',
            concentrate="true",
        )
        sys_graph.edge_attr.update(
            arrowhead='dot',
            arrowsize='1',
        )

        sys_subgraphes_d = {}
        for sg, sg_specs in config.get("subgraphes", {}).items():
            
            sys_subgraphes_d[sg] = graphviz.Digraph(engine="neato")
            sys_subgraphes_d[sg].attr(
                label=sg_specs.get("name", sg),
                splines='true',
                overlap='false',
            )
        
        comp_specs_d, flow_specs_list = self.get_system_graph_specs(config=config)

        for flow_specs in flow_specs_list:
            
            flow_specs_cur = copy.deepcopy(flow_specs)

            # Check if the flow has to be ignored 
            if flow_specs_cur.pop("ignore", False):
                continue

            # Then check if source and target have to been drawn
            node_source_name = flow_specs_cur.pop('source')
            node_target_name = flow_specs_cur.pop('target')

            comp_specs_source = copy.deepcopy(comp_specs_d[node_source_name])
            comp_specs_target = copy.deepcopy(comp_specs_d[node_target_name])

            comp_source_ignore = comp_specs_source.pop("ignore", False)
            comp_target_ignore = comp_specs_target.pop("ignore", False)

            if comp_source_ignore and comp_target_ignore:
                continue
            else:
                comp_specs_source_name = comp_specs_source.pop("name")
                comp_specs_source_label = comp_specs_source.pop("label")

                source_sg = None
                for sg, sg_specs in config.get("subgraphes", {}).items():
                    comp_pattern = sg_specs.get("comp_pattern")
                    if comp_pattern and re.search(comp_pattern,
                                                  comp_specs_source_name):
                        sys_subgraphes_d[sg].node(comp_specs_source_name,
                                                  comp_specs_source_label,
                                                  **comp_specs_source)
                        source_sg = sg
                        break
                        
                if not source_sg:
                    sys_graph.node(comp_specs_source_name,
                                   comp_specs_source_label,
                                   **comp_specs_source)

                comp_specs_target_name = comp_specs_target.pop("name")
                comp_specs_target_label = comp_specs_target.pop("label")

                target_sg = None
                for sg, sg_specs in config.get("subgraphes", {}).items():
                    comp_pattern = sg_specs.get("comp_pattern")
                    if comp_pattern and re.search(comp_pattern,
                                                  comp_specs_target_name):
                        sys_subgraphes_d[sg].node(comp_specs_target_name,
                                                  comp_specs_target_label,
                                                  **comp_specs_target)
                        target_sg = sg
                        break
                        
                if not target_sg:
                    sys_graph.node(comp_specs_target_name,
                                   comp_specs_target_label,
                                   **comp_specs_target)

                # sys_graph.node(comp_specs_target.pop("name"), *)
                #                comp_specs_target.pop("label"), *)
                #                **comp_specs_target) *)

                # Draw edges
                if source_sg and target_sg and (source_sg == target_sg):
                    sys_subgraphes_d[source_sg].edge(
                        node_source_name,
                        node_target_name,
                        **flow_specs_cur,
                    )
                else:
                    sys_graph.edge(
                        node_source_name,
                        node_target_name,
                        **flow_specs_cur,
                    )

        [sys_graph.subgraph(sg) for sg in sys_subgraphes_d.values()]
        # Sauvegardez le graphe dans un fichier HTML
        #ipdb.set_trace()
        svg_data = sys_graph.pipe(format='svg').decode('utf-8')
        html_code = f"""
<!DOCTYPE html>
<html>
<head>
    <title>{self.name()}</title>
</head>
<body>
    {svg_data}
</body>
</html>
"""

        with open(filename, 'w') as f:
            f.write(html_code)
        #sys_graph.generate_html(name=filename)
