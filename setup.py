# -*- coding: utf-8 -*-
try:
    from setuptools import setup, find_packages
except ImportError:
    from ez_setup import use_setuptools

    use_setuptools()
    from setuptools import setup, find_packages

setup(
    name="ocs-ci",
    version="4.14.0",
    description="OCS CI tests that run in jenkins and standalone mode using aws provider",
    author="OCS QE",
    author_email="ocs-ci@redhat.com",
    license="MIT",
    install_requires=[
        "apache-libcloud",
        "cryptography",
        "docopt",
        # https://pypi.org/project/gevent/ the latest version resolves problem for Mac M1 chips
        # This issue is caused by a program attempting to load an x86_64-only library from a native arm64 process.
        # More https://stackoverflow.com/questions/71443345/gevent-cant-be-installed-on-m1-mac-using-poetry
        "gevent",
        "reportportal-client",
        "requests",
        "paramiko",
        "pyyaml",
        "jinja2",
        "openshift",
        "boto3",
        "munch",
        "pytest",
        "pytest-logger",
        "pytest-html",
        "pytest-metadata",
        "bs4",
        "gspread",
        "google-auth-oauthlib",
        "oauth2client",
        "pytest_marker_bugzilla",
        "pyvmomi",
        "python-hcl2",
        "python-dateutil",
        "pytest-ordering",
        "funcy",
        "semantic-version",
        "jsonschema",
        "google-cloud-storage",
        "google-auth",
        "elasticsearch",
        "numpy",
        "pandas",
        "tabulate",
        "python-ipmi",
        "scipy",
        "PrettyTable",
        "azure-common",
        "azure-mgmt-compute",
        "azure-mgmt-network",
        "azure-mgmt-resource",
        "azure-storage-blob",
        "msrestazure",
        "python-novaclient",
        "python-cinderclient",
        "keystoneauth1",
        "range-key-dict",
        "GitPython",
        "selenium",
        "webdriver-manager",
        # greenlet 1.0.0 is broken on ppc64le
        # https://github.com/python-greenlet/greenlet/issues/230
        # by default program attempting to load an x86_64-only library from a native arm64 process
        # Beginning with gevent 20.12.0, 64-bit ARM binaries are distributed on PyPI for aarch64 manylinux2014
        # compatible systems. Resolves problem for m1 Mac chips
        "greenlet",
        "ovirt-engine-sdk-python",
        "junitparser",
        "flaky",
        "ocp-network-split",
        "pyopenssl",
        "pyparsing ",
        "mysql-connector-python",
        "pytest-repeat",
        "pexpect",
        # googleapis-common-protos 1.56.2 needs to have protobuf<4.0.0>=3.15.0
        "protobuf",
        "ping3",
        "psutil",
        "azure-identity",
        "azure-mgmt-storage",
    ],
    entry_points={
        "console_scripts": [
            "run-ci=ocs_ci.framework.main:main",
            "report-version=ocs_ci.ocs.version:main",
            "ci-cleanup=ocs_ci.cleanup.aws.cleanup:cluster_cleanup",
            "ci-pause=ocs_ci.pause.pause:cluster_pause",
            "aws-cleanup=ocs_ci.cleanup.aws.cleanup:aws_cleanup",
            "vsphere-cleanup=ocs_ci.cleanup.vsphere.cleanup:vsphere_cleanup",
            "ocs-build=ocs_ci.utility.ocs_build:main",
            "get-ssl-cert=ocs_ci.utility.ssl_certs:main",
        ],
    },
    zip_safe=True,
    include_package_data=True,
    packages=find_packages(exclude=["ez_setup"]),
)
