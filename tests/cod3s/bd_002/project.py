import sys
import pkg_resources
import cod3s
import system
import logging

installed_pkg = {pkg.key for pkg in pkg_resources.working_set}
if 'ipdb' in installed_pkg:
    import ipdb

# Create a custom formatter
formatter = logging.Formatter('[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
# Create a console handler with level DEBUG; it will print everything
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
# Set the formatter for the console handler
console_handler.setFormatter(formatter)
# Get a reference to the root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)  # Set the minimum log level that will be handled
# Add the console handler to the root logger
logger.addHandler(console_handler)


PROJECT = cod3s.COD3SProject.from_yaml(
    file_path="project.yaml",
    cls_attr="COD3SProject",
)

PROJECT.logger = logger

viz_specs = PROJECT.get_system_viz()

print(viz_specs)

sys.exit(0)
