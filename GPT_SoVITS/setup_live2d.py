from setuptools import setup
from Cython.Build import cythonize

setup(
    ext_modules = cythonize("live2d_1.pyx")
)
