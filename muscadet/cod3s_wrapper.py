from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any, Union, Literal
import cod3s


class InterfaceMuscadetTemplate(cod3s.InterfaceTemplate):
    pass


class InterfaceFlowIn(InterfaceMuscadetTemplate):
    port_type: Literal["input"] = Field("input", description="Port type")
    logic: str = Field(
        "or", description="Flow input logic 'and' ; 'or' (default) ; 'k/n'"
    )


class InterfaceFlowOut(InterfaceMuscadetTemplate):
    port_type: Literal["output"] = Field("output", description="Port type")

    logic: list = Field(
        [],
        description="Flow out condition [(C11 <BoolOpeA> C12 <BoolOpeA> ... <BoolOpeA> C1_k1) <BoolOpeB> (C21 <BoolOpeA> ... <BoolOpeA> C2_k2) <BoolOpeB> ... <BoolOpeB> (Cn1 <BoolOpeA> ... <BoolOpeA> Cn_kn)] where both <BoolOpeA> and <BoolOpeB> are boolean operators set by attribute 'logic_inner_mode'",
    )
    logic_inner_mode: str = Field(
        "or",
        description="Flow output condition expression mode: 'or' means logic is evaluated like [(C11 or C12 or ... or C1_k1) and (C21 or ... C2_k2) and ... and (Cn1 or ... or Cn_kn)], 'and' means evaluation like [(C11 and C12 and ... and C1_k1) or (C21 and ... and C2_k2) or ... or (Cn1 and ... and Cn_kn)]",
    )

    negate: bool = Field(False, description="Indicates if the flow output is negated")


class ComponentMuscadetClass(cod3s.ComponentClass):
    interfaces: List[InterfaceMuscadetTemplate] = Field(
        [], description="List of component interfaces"
    )


class ObjFlowClass(ComponentMuscadetClass):
    class_name_bkd: Optional[Dict[str, str]] = Field(
        {"pycatshoo": "ObjFlow"},
        description="Class name used to instanciate the component with the backend analysis tool",
    )


class KBMuscadet(cod3s.KB):
    """
    A knowledge base (KB) contains a list of component classes.

    The knowledge base is the main container for storing and organizing
    component definitions that can be instantiated to build
    a model system. It provides a reusable catalog of components
    with their complete specifications.
    """

    component_classes: Optional[Dict[str, ComponentMuscadetClass]] = Field(
        {}, description="Dictionnary of component classes"
    )


class ObjFlowInstance(ObjFlowClass, cod3s.ComponentInstance):

    def to_bkd_pycatshoo(self):

        cls_name = self.class_name_bkd.get("pycatshoo")
        if not cls_name:
            raise ValueError("No Pycatshoo backend class is specified in the template.")

        # # Get the class from its name
        # if not hasattr(pyc, cls_name):
        #     raise ValueError(
        #         f"The class '{cls_name}' doesn't exist in the Pycatshoo package."
        #     )

        __import__("ipdb").set_trace()

        cls = getattr(pyc, cls_name)

        try:
            comp = cls(self.name, **self.init_parameters)
        except Exception as e:
            raise ValueError(
                f"Pycatshoo component instanciation failed: {e}. Please check if a PyCATSHOO system is instanciated ?"
            )

        self.check_bkd_pycatshoo()

        class_name = comp_specs.class_name or "ObjSGE"

        comp = self.add_component(
            cls=class_name,
            name=instance_name,
            comp_type=comp_specs.name,
            description=comp_specs.description,
            partial_init=True,
            **params,
        )

        for fin in comp_specs.flow_in:
            comp.add_flow(fin)

        for fout in comp_specs.flow_out:
            comp.add_flow(fout)

        comp.set_flows()
        # Instantiate the component with the initialization parameters
        return comp
