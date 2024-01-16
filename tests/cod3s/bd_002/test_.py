import pytest
import requests
import logging
import sys
sys.path.insert(0, "..")
from utils import start_cod3s_server, stop_cod3s_server
import pkg_resources
installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb  # noqa: F401

logger = logging.getLogger(__name__)


def test_project_info():
    # Test parameters
    base_url = "http://localhost:8000"
    endpoint = "project"

    # Expected results
    response_expected = {
        'cls': 'COD3SProject',
        'project_name': 'Test COD3S',
        'project_path': '.',
        'system_name': 'test',
        'system_filename': 'system.py',
        'system_class_name': 'MySystem',
        'viz_specs_filename': "viz_specs.yaml",
    }
    
    server = start_cod3s_server(
        url=base_url,
    )

    response = requests.get(f"{base_url}/{endpoint}")
    assert response.status_code == 200

    response_dict = response.json()

    assert response_dict == response_expected

    stop_cod3s_server(server)


def test_system_viz():
    # Test parameters
    base_url = "http://localhost:8000"
    endpoint = "system_viz"

    response_expected = {'components': [{'name': 'C1', 'class_name': 'Bloc', 'ports': {'flow_in': 'left', 'flow_out': 'right'}, 'style': {}}, {'name': 'C2', 'class_name': 'Bloc', 'ports': {'flow_in': 'left', 'flow_out': 'right'}, 'style': {}}, {'name': 'LI__C', 'class_name': 'LogicOr', 'ports': {}, 'style': {'fontname': 'monospace', 'fontsize': '10', 'shape': 'box', 'style': 'filled', 'fontcolor': '#1f416d'}}, {'name': 'LO__C', 'class_name': 'LogicOr', 'ports': {}, 'style': {'fontname': 'monospace', 'fontsize': '10', 'shape': 'box', 'style': 'filled', 'fontcolor': '#1f416d'}}, {'name': 'S', 'class_name': 'Source', 'ports': {'flow_out': 'right'}, 'style': {}}], 'connections': [{'comp_source': 'C1', 'port_source': 'flow_available_out', 'comp_target': 'LI__C', 'port_target': 'flow_available_in', 'style': {'color': '#FF416d'}}, {'comp_source': 'C1', 'port_source': 'flow_in', 'comp_target': 'S', 'port_target': 'flow_out', 'style': {'color': '#1f416d'}}, {'comp_source': 'C1', 'port_source': 'flow_out', 'comp_target': 'LO__C', 'port_target': 'flow_in', 'style': {'color': '#1f416d'}}, {'comp_source': 'C2', 'port_source': 'flow_available_out', 'comp_target': 'LI__C', 'port_target': 'flow_available_in', 'style': {'color': '#FF416d'}}, {'comp_source': 'C2', 'port_source': 'flow_in', 'comp_target': 'S', 'port_target': 'flow_out', 'style': {'color': '#1f416d'}}, {'comp_source': 'C2', 'port_source': 'flow_out', 'comp_target': 'LO__C', 'port_target': 'flow_in', 'style': {'color': '#1f416d'}}, {'comp_source': 'LI__C', 'port_source': 'flow_available_in', 'comp_target': 'C1', 'port_target': 'flow_available_out', 'style': {'color': '#FF416d'}}, {'comp_source': 'LI__C', 'port_source': 'flow_available_in', 'comp_target': 'C2', 'port_target': 'flow_available_out', 'style': {'color': '#FF416d'}}, {'comp_source': 'LO__C', 'port_source': 'flow_in', 'comp_target': 'C1', 'port_target': 'flow_out', 'style': {'color': '#1f416d'}}, {'comp_source': 'LO__C', 'port_source': 'flow_in', 'comp_target': 'C2', 'port_target': 'flow_out', 'style': {'color': '#1f416d'}}, {'comp_source': 'S', 'port_source': 'flow_out', 'comp_target': 'C1', 'port_target': 'flow_in', 'style': {'color': '#1f416d'}}, {'comp_source': 'S', 'port_source': 'flow_out', 'comp_target': 'C2', 'port_target': 'flow_in', 'style': {'color': '#1f416d'}}]}
    
    server = start_cod3s_server(
        url=base_url,
    )

    response = requests.get(f"{base_url}/{endpoint}")
    assert response.status_code == 200

    response_dict = response.json()
    
    assert response_dict == response_expected

    stop_cod3s_server(server)



