"""
Utility functions that are used as a part of OCP or OCS deployments
"""

import logging
import ipaddress
import os
import re
import tempfile
import yaml

import requests

from ocs_ci.framework import config
from ocs_ci.ocs import constants, ocp
from ocs_ci.ocs.exceptions import ExternalClusterDetailsException
from ocs_ci.ocs.resources.pod import get_operator_pods, delete_pods
from ocs_ci.utility import templating
from ocs_ci.utility.utils import (
    create_directory_path,
    exec_cmd,
    run_cmd,
    wait_for_machineconfigpool_status,
)

logger = logging.getLogger(__name__)


def get_ocp_ga_version(channel):
    """
    Retrieve the latest GA version for

    Args:
        channel (str): the OCP version channel to retrieve GA version for

    Returns:
        str: latest GA version for the provided channel.
            An empty string is returned if no version exists.


    """
    logger.debug("Retrieving GA version for channel: %s", channel)
    url = "https://api.openshift.com/api/upgrades_info/v1/graph"
    headers = {"Accept": "application/json"}
    payload = {"channel": f"stable-{channel}"}
    r = requests.get(url, headers=headers, params=payload)
    nodes = r.json()["nodes"]
    if nodes:
        versions = [node["version"] for node in nodes]
        versions.sort()
        ga_version = versions[-1]
        logger.debug("Found GA version: %s", ga_version)
        return ga_version
    logger.debug("No GA version found")
    return ""


def create_external_secret(ocs_version=None, apply=False):
    """
    Creates secret data for external cluster

    Args:
         ocs_version (str): OCS version
         apply (bool): True if want to use apply instead of create command

    """
    ocs_version = ocs_version or config.ENV_DATA["ocs_version"]
    secret_data = templating.load_yaml(constants.EXTERNAL_CLUSTER_SECRET_YAML)
    external_cluster_details = config.EXTERNAL_MODE.get("external_cluster_details", "")
    if not external_cluster_details:
        raise ExternalClusterDetailsException("No external cluster data found")
    secret_data["data"]["external_cluster_details"] = external_cluster_details
    if config.DEPLOYMENT.get("multi_storagecluster"):
        secret_data["metadata"][
            "namespace"
        ] = constants.OPENSHIFT_STORAGE_EXTENDED_NAMESPACE
    secret_data_yaml = tempfile.NamedTemporaryFile(
        mode="w+", prefix="external_cluster_secret", delete=False
    )
    templating.dump_data_to_temp_yaml(secret_data, secret_data_yaml.name)
    logger.info(f"Creating external cluster secret for OCS version: {ocs_version}")
    oc_type = "apply" if apply else "create"
    run_cmd(f"oc {oc_type} -f {secret_data_yaml.name}")


def get_cluster_prefix(cluster_name, special_rules):
    """
    Parse out the "prefix" of a cluster name. Note this is not the same thing as the
    CLUSTER_PREFIX in jenkins. In fact we will parse that value out. This  "cluster
    prefix" is used to check cloud providers to see if a particular user already has
    a cluster created. This is to stop people from using too many cloud resources at
    one time.

    Args:
        cluster_name (str): name of the cluster
        special_rules (dict): dictionary containing special prefix rules that allow
            clusters to remain alive longer than our default value

    Returns:
        str: cluster name prefix

    """
    prefix, _, tier = cluster_name.rpartition("-")
    for pattern in special_rules.keys():
        if bool(re.match(pattern, prefix, re.I)):
            logger.debug("%s starts with %s", cluster_name, pattern)
            prefix = re.sub(pattern, "", prefix)
            break
    # If `prefix` is an empty string we should assume that there was no hyphen
    # in the cluster name and that the value for `tier` is what we should use.
    prefix = prefix or tier
    # Remove potential leading hyphen
    if prefix.startswith("-"):
        prefix = prefix[1:]
    return prefix


def get_and_apply_icsp_from_catalog(image, apply=True, insecure=False):
    """
    Get ICSP from catalog image (if exists) and apply it on the cluster (if
    requested).

    Args:
        image (str): catalog image of ocs registry.
        apply (bool): controls if the ICSP should be applied or not
            (default: true)
        insecure (bool): If True, it allows push and pull operations to registries to be made over HTTP

    Returns:
        str: path to the icsp.yaml file or empty string, if icsp not available
            in the catalog image

    """

    icsp_file_location = "/icsp.yaml"
    icsp_file_dest_dir = os.path.join(
        config.ENV_DATA["cluster_path"], f"icsp-{config.RUN['run_id']}"
    )
    icsp_file_dest_location = os.path.join(icsp_file_dest_dir, "icsp.yaml")
    pull_secret_path = os.path.join(constants.DATA_DIR, "pull-secret")
    create_directory_path(icsp_file_dest_dir)
    cmd = (
        f"oc image extract --filter-by-os linux/amd64 --registry-config {pull_secret_path} "
        f"{image} --confirm "
        f"--path {icsp_file_location}:{icsp_file_dest_dir}"
    )
    if insecure:
        cmd = f"{cmd} --insecure"
    exec_cmd(cmd)
    if not os.path.exists(icsp_file_dest_location):
        return ""

    # make icsp name unique - append run_id
    with open(icsp_file_dest_location) as f:
        icsp_content = yaml.safe_load(f)
    icsp_content["metadata"]["name"] += f"-{config.RUN['run_id']}"
    with open(icsp_file_dest_location, "w") as f:
        yaml.dump(icsp_content, f)

    if apply and not config.DEPLOYMENT.get("disconnected"):
        exec_cmd(f"oc apply -f {icsp_file_dest_location}")
        managed_ibmcloud = (
            config.ENV_DATA["platform"] == constants.IBMCLOUD_PLATFORM
            and config.ENV_DATA["deployment_type"] == "managed"
        )
        if not managed_ibmcloud:
            wait_for_machineconfigpool_status("all")

    return icsp_file_dest_location


def get_ocp_release_image():
    """
    Get the url of ocp release image
    * from DEPLOYMENT["custom_ocp_image"] or
    * from openshift-install version command output

    Returns:
        str: Release image of the openshift installer

    """
    if not config.DEPLOYMENT.get("ocp_image"):
        if config.DEPLOYMENT.get("custom_ocp_image"):
            config.DEPLOYMENT["ocp_image"] = config.DEPLOYMENT.get("custom_ocp_image")
        else:
            config.DEPLOYMENT["ocp_image"] = get_ocp_release_image_from_installer()
    return config.DEPLOYMENT["ocp_image"]


def get_ocp_release_image_from_installer():
    """
    Retrieve release image using the openshift installer.

    Returns:
        str: Release image of the openshift installer

    """
    logger.info("Retrieving release image from openshift installer")
    installer_path = config.ENV_DATA["installer_path"]
    cmd = f"{installer_path} version"
    proc = exec_cmd(cmd)
    for line in proc.stdout.decode().split("\n"):
        if "release image" in line:
            return line.split(" ")[2].strip()


def get_ocp_release_image_from_running_cluster():
    """
    Return the OCP release image from ClusterVersion

    Returns:
         str: The OCP release image from ClusterVersion

    """

    ocp_cluster = ocp.OCP(
        kind="",
        resource_name="clusterversion",
    )
    return ocp_cluster.get()["items"][0]["status"]["desired"]["image"]


def get_coredns_container_image(release_image, pull_secret_path):
    """
    Obtain the CoreDNS container image from the OCP release image.

    Args:
        release_image (str): Release image from the openshift installer
        pull_secret_path (str): Path to the pull secret

    Returns:
        str: CoreDNS container image

    """
    logger.info("Obtaining the CoreDNS container image from the OCP release image")
    cmd = f"oc adm release info --image-for='coredns' {release_image} -a {pull_secret_path}"
    result = exec_cmd(cmd)
    return result.stdout.decode().strip()


def configure_virtual_host_style_acess_for_rgw(self):
    """
    Enable access buckets with DNS subdomain style (Virtual host style) for RGW
    """
    if not config.DEPLOYMENT.get("rgw_enable_virtual_host_style_access"):
        logger.info(
            "Skipping configuration of access buckets with DNS subdomain style (Virtual host style) for RGW "
            "because DEPLOYMENT.rgw_enable_virtual_host_style_access is set to false."
        )
        return
    if config.ENV_DATA.get("platform") not in constants.ON_PREM_PLATFORMS:
        logger.info(
            "Skipping configuration of access buckets with DNS subdomain style (Virtual host style) for RGW "
            f"because {config.ENV_DATA.get('platform')} platform is not between {constants.ON_PREM_PLATFORMS}"
        )
        return
    logger.info(
        "Configuring access buckets with DNS subdomain style (Virtual host style) for RGW"
    )

    release_image = get_ocp_release_image_from_running_cluster()
    pull_secret_path = os.path.join(constants.DATA_DIR, "pull-secret")
    coredns_image = get_coredns_container_image(release_image, pull_secret_path)
    coredns_deployment = templating.load_yaml(constants.COREDNS_DEPLOYMENT_YAML)
    coredns_deployment["spec"]["template"]["spec"]["containers"][0][
        "image"
    ] = coredns_image
    coredns_deployment_yaml = tempfile.NamedTemporaryFile(
        mode="w+", prefix="coredns_deployment", suffix=".yaml", delete=False
    )
    templating.dump_data_to_temp_yaml(coredns_deployment, coredns_deployment_yaml.name)

    logger.info("Creating ConfigMap for CoreDNS")
    exec_cmd(f"oc create -f {constants.COREDNS_CONFIGMAP_YAML}")
    logger.info("Creating CoreDNS Deployment")
    exec_cmd(f"oc create -f {coredns_deployment_yaml.name}")
    logger.info("Creating CoreDNS Service")
    exec_cmd(f"oc create -f {constants.COREDNS_SERVICE_YAML}")
    # get dns ip
    dns_ip = exec_cmd(
        f"oc get -n {config.ENV_DATA['cluster_namespace']} svc odf-dns -ojsonpath={{..clusterIP}}"
    ).stdout.decode()
    try:
        ipaddress.IPv4Address(dns_ip)
    except ipaddress.AddressValueError:
        logger.error("Failed to obtain IP of odf-dns Service")
        raise
    logger.info(
        f"Patching dns.operator/default to forward 'data.local' zone to {dns_ip}:53 (odf-dns Service)"
    )
    exec_cmd(
        "oc patch dns.operator/default --type=merge --patch '"
        '{"spec":{"servers":[{"forwardPlugin":{"upstreams":["'
        f"{dns_ip}:53"
        '"]},"name":"rook-dns","zones":["data.local"]'
        "}]}}'"
    )
    logger.info("Patching storagecluster/ocs-storagecluster to allow virtualHostnames")
    exec_cmd(
        "oc patch -n openshift-storage storagecluster/ocs-storagecluster --type=merge --patch '"
        '{"spec":{"managedResources":{"cephObjectStores":{"virtualHostnames":'
        '["rgw.data.local"]'
        "}}}}'"
    )
    # Restart rook-ceph-operator pod, not sure if this is required step or just workaround
    logger.info("Restarting rook-ceph-operator pod")
    rook_ceph_operator_pods = get_operator_pods()
    delete_pods(rook_ceph_operator_pods, wait=True)
    # wait for rook-ceph-operator pod starts
    pod_obj = ocp.OCP(
        kind=constants.POD, namespace=config.ENV_DATA["cluster_namespace"]
    )
    pod_obj.wait_for_resource(
        condition=constants.STATUS_RUNNING,
        selector=constants.OPERATOR_LABEL,
        timeout=300,
        sleep=5,
    )
    logger.info("Pod rook-ceph-operator were successfully restarted")
