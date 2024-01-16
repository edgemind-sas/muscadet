import sys
import cod3s


project = cod3s.COD3SProject.from_yaml(
    file_path="project.yaml",
    cls_attr="COD3SProject",
)


viz_specs = project.get_system_viz()

print(viz_specs)

sys.exit(0)
+
