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


# Test without any CLI arguments
def test_check_api():
    base_url = "http://localhost:8000"
    server = start_cod3s_server(
        url=base_url,
    )

    response = requests.get(f"{base_url}/")
    assert response.status_code == 200

    stop_cod3s_server(server)


def test_check_api_with_args():
    base_url = "http://localhost:8000"
    server = start_cod3s_server(
        cod3s_args=["-j", "project.yaml"],
        url=base_url,
    )

    response = requests.get(f"{base_url}/")
    assert response.status_code == 200

    stop_cod3s_server(server) 


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
        'viz_specs_filename': None,
    }
    
    server = start_cod3s_server(
        url=base_url,
    )

    response = requests.get(f"{base_url}/{endpoint}")
    assert response.status_code == 200

    response_dict = response.json()

    assert response_dict == response_expected

    stop_cod3s_server(server)


def test_get_components():
    # Test parameters
    base_url = "http://localhost:8000"
    endpoint = "components"

    # Expected results
    response_expected = [{"cls":"Bloc","name":"C1"},{"cls":"Bloc","name":"C2"},{"cls":"LogicOr","name":"LI__C"},{"cls":"LogicOr","name":"LO__C"},{"cls":"Source","name":"S"}]
    
    server = start_cod3s_server(
        url=base_url,
        logger=logger,
    )

    response = requests.get(f"{base_url}/{endpoint}")
    assert response.status_code == 200

    response_dict = response.json()
    assert response_dict == response_expected

    stop_cod3s_server(server)


def test_add_indicator():
    """
    curl -X POST "http://localhost:8000/add_indicator/" \
         -H "Content-Type: application/json" \
         -d '{"component": ".*", "var": ".*fed_out$", "value_test": true, "stats": ["mean", "stddev"]}'
    """
    # Test parameters
    base_url = "http://localhost:8000"
    endpoint = "add_indicator"

    # Expected results
    
    server = start_cod3s_server(
        url=base_url,
        logger=logger,
    )

    response = requests.post(
        f"{base_url}/{endpoint}/",
        json={
            "component": ".*",
            "var": ".*fed_out$",
            "value_test": True,
            "stats": ["mean", "stddev"]
        }
    )
    assert response.status_code == 200

    response_dict = response.json()
    assert len(response_dict.get("indicators", [])) == 5

    stop_cod3s_server(server)


def test_simulate():
    """
    curl -X POST "http://localhost:8000/simulate/" \
         -H "Content-Type: application/json" \
         -d '{"nb_runs":100000, "schedule"=[{"start": 0, "end": 20, "nvalues": 100}]}'
    """
    # Test parameters
    base_url = "http://localhost:8000"
    endpoint = "simulate"

    # Expected results
    
    server = start_cod3s_server(
        url=base_url,
        logger=logger,
    )

    response = requests.post(
        f"{base_url}/{endpoint}/",
        json={
            "nb_runs": 100,
            "schedule": [{"start": 0, "end": 20,
                          "nvalues": 100}]
        }
    )
    assert response.status_code == 200

    response_dict = response.json()
    
    assert response_dict.get("process_time")

    stop_cod3s_server(server)

