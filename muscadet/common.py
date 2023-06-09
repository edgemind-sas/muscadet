import Pycatshoo as pyc


def get_pyc_type(var_type):
    if var_type == 'bool':
        return (bool, pyc.TVarType.t_bool)
    elif var_type == 'int':
        return (int, pyc.TVarType.t_integer)
    elif var_type == 'float':
        return (float, pyc.TVarType.t_double)
    else:
        raise ValueError(
            f"Type {var_type} not supported by PyCATSHOO")
