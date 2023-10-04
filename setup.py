"""MUSCADET Setup"""

from setuptools import setup, find_packages

VERSION = "0.0.5"

setup(name='muscadet',
      version=VERSION,
      url='https://github.com/edgemind-sas/muscadet',
      author='Roland Donat',
      author_email='roland.donat@gmail.com, roland.donat@edgemind.net',
      maintainer='Roland Donat',
      maintainer_email='roland.donat@edgemind.net',
      keywords='Modelling',
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: MIT License',
          'Operating System :: POSIX :: Linux',
          'Programming Language :: Python :: 3.8',
          'Topic :: Scientific/Engineering :: Artificial Intelligence'
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
              "*.log.*"
          ]
      ),
      description='Discrete stochastic flow system modelling library',
      license='MIT',
      platforms='ALL',
      python_requires='>=3.8',
      install_requires=[
          "pydantic>=1.10.2",
          "lxml",
          "colored",
          "pyvis",
          "graphviz",
      ],
      zip_safe=False,
      # scripts=[
      #     'bin/<somescript',
      # ],
      )
