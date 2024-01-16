# conftest.py

import pytest
import subprocess
import requests
import time
import ipdb
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def wait_for_server_to_be_up(base_url, timeout=10, logger=None):
    if logger:
        logger.info(f"Waiting for the server to be up at {base_url}")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(base_url)
            if response.ok:
                if logger:
                    logger.info("Server is up and responding.")
                return
        except requests.ConnectionError:
            if logger:
                logger.debug("Server not up yet, retrying...")
            time.sleep(0.5)  # Sleep before the next attempt
    raise RuntimeError(f"Server did not start within {timeout} seconds.")


def start_cod3s_server(cod3s_args=[], url="http://localhost:8000", timeout=10, logger=None):
    command = ["cod3s-project"] + cod3s_args
    if logger:
        logger.info(f"Starting server with command: {' '.join(command)}")
    server = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    wait_for_server_to_be_up(base_url=url, timeout=timeout, logger=logger)
    
    return server


def stop_cod3s_server(server, logger=None):
    server.terminate()
    server.wait()
    if logger:
        logger.info("Server stopped.")
