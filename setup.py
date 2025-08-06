"""MUSCADET Setup"""

from setuptools import setup, find_packages
import importlib.util

# Charger le module version
spec = importlib.util.spec_from_file_location("version", "cod3s/version.py")
version_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version_module)
VERSION = version_module.__version__

COD3S_VERSION = "@0.1.0"
COD3S_VERSION = ""

# Specify local cod3s path directly
COD3S_REQUIREMENT = (
    f"cod3s @ git+https://github.com/edgemind-sas/cod3s.git{COD3S_VERSION}"
)
setup(
    name="muscadet",
    version=VERSION,
    url="https://github.com/edgemind-sas/muscadet",
    author="Roland Donat",
    author_email="roland.donat@gmail.com, roland.donat@edgemind.net",
    maintainer="Roland Donat",
    maintainer_email="roland.donat@edgemind.net",
    keywords="Modelling",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.8",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    packages=find_packages(
        exclude=[
            "*.tests",
            "*.tests.*",
            "tests.*",
            "tests",
            "log",
            "log.*",
            "*.log",
            "*.log.*",
        ]
    ),
    description="Discrete stochastic flow system modelling library",
    license="MIT",
    platforms="ALL",
    python_requires=">=3.8",
    install_requires=[
        # f"cod3s @ git+https://github.com/edgemind-sas/cod3s.git{COD3S_VERSION}",
        COD3S_REQUIREMENT,
        "graphviz==0.20.1",
    ],
    zip_safe=False,
    # scripts=[
    #     'bin/<somescript',
    # ],
)
