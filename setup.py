import re
from pathlib import Path

from setuptools import find_packages, setup

INIT_FILE = Path("src/SymINDy/__init__.py")

init_data = INIT_FILE.read_text()

metadata = dict(re.findall(r"__([a-z]+)__ = [\"']([^\"']+)[\"']", init_data))

AUTHOR = metadata["author"]
VERSION = metadata["version"]

setup(
    name="SymINDy",
    version=VERSION,
    author=AUTHOR,
    packages=find_packages("src"),
    package_dir={"": "src"},
    include_package_data=True,
    python_requires=">= 3.7",
    entry_points={"console_scripts": ["SymINDy=SymINDy.__main__:cli"]},
)
