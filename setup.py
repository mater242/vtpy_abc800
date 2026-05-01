from distutils.core import setup

setup(
    name="VTPy",
    version="1.0.18",
    description="Python abstraction layer for VT-100 terminals.",
    author="Jennifer Taylor",
    author_email="jen@superjentendo.com",
    url="https://github.com/mater242/vtpy_abc800",
    packages=["vtpy"],
    install_requires=[
        req for req in open("requirements.txt").read().split("\n") if len(req) > 0
    ],
    include_package_data=True,
)
