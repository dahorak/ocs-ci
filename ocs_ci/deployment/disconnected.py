"""
This module contains functionality required for disconnected installation.
"""

import json
import logging
import os
import platform
import yaml

from ocs_ci.framework import config
from ocs_ci.ocs import constants
from ocs_ci.ocs.exceptions import (
    CommandFailed,
    NotFoundError,
    UnsupportedOSType,
)
from ocs_ci.utility.utils import (
    create_directory_path,
    download_file,
    exec_cmd,
    get_image_with_digest,
    get_latest_ds_olm_tag,
    get_url_content,
    login_to_mirror_registry,
    prepare_bin_dir,
    prepare_customized_pull_secret,
    wait_for_machineconfigpool_status,
)

logger = logging.getLogger(__name__)


def get_csv_from_image(bundle_image):
    """
    Extract clusterserviceversion.yaml file from operator bundle image.

    Args:
        bundle_image (str): OCS operator bundle image

    Returns:
        dict: loaded yaml from CSV file

    """
    manifests_dir = os.path.join(
        config.ENV_DATA["cluster_path"], constants.MANIFESTS_DIR
    )
    ocs_operator_csv_yaml = os.path.join(manifests_dir, constants.OCS_OPERATOR_CSV_YAML)
    create_directory_path(manifests_dir)

    with prepare_customized_pull_secret(bundle_image) as authfile_fo:
        exec_cmd(
            f"oc image extract --registry-config {authfile_fo.name} "
            f"{bundle_image} --confirm "
            f"--path /manifests/ocs-operator.clusterserviceversion.yaml:{manifests_dir}"
        )

    try:
        with open(ocs_operator_csv_yaml) as f:
            return yaml.safe_load(f)
    except FileNotFoundError as err:
        logger.error(f"File {ocs_operator_csv_yaml} does not exists ({err})")
        raise


def prepare_disconnected_ocs_deployment():
    """
    Prepare disconnected ocs deployment:
    - get related images from OCS operator bundle csv
    - mirror related images to mirror registry
    - create imageContentSourcePolicy for the mirrored images
    - disable the default OperatorSources

    Returns:
        str: OCS registry image prepared for disconnected installation (with
            sha256 digest)

    """

    logger.info("Prepare for disconnected OCS installation")
    if config.DEPLOYMENT.get("live_deployment"):

        try:
            opm_version = exec_cmd("opm version")
            logger.info(f"opm tool is available: {opm_version.stdout}")
        except CommandFailed:
            logger.info("installing opm tool")
            opm_owner_repo = "operator-framework/operator-registry"
            opm_release_tag = "latest"
            if opm_release_tag != "latest":
                opm_release_tag = f"tags/{opm_release_tag}"
            opm_releases_api_url = f"https://api.github.com/repos/{opm_owner_repo}/releases/{opm_release_tag}"
            if config.AUTH.get("github"):
                github_auth = (
                    config.AUTH["github"].get("username"),
                    config.AUTH["github"].get("token"),
                )
                logger.debug(f"Using github authentication (user: {github_auth[0]})")
            else:
                github_auth = None
                logger.warning(
                    "Github credentials are not provided in data/auth.yaml file. "
                    "You might encounter issues with accessing github api as it "
                    "have very strict rate limit for unauthenticated requests "
                    "(60 requests per hour). Please check docs/getting_started.md "
                    "file to find how to configure github authentication."
                )
            release_data = json.loads(
                get_url_content(opm_releases_api_url, auth=github_auth)
            )

            if platform.system() == "Darwin":
                opm_asset_name = "darwin-amd64-opm"
            elif platform.system() == "Linux":
                opm_asset_name = "linux-amd64-opm"
            else:
                raise UnsupportedOSType

            for asset in release_data["assets"]:
                if asset["name"] == opm_asset_name:
                    opm_download_url = asset["browser_download_url"]
                    break
            else:
                raise NotFoundError(
                    f"opm binary for selected type {opm_asset_name} was not found"
                )

            prepare_bin_dir()
            bin_dir = None
            bin_dir = os.path.expanduser(bin_dir or config.RUN["bin_dir"])
            download_file(opm_download_url, os.path.join(bin_dir, "opm"))
            cmd = f"chmod +x {os.path.join(bin_dir, 'opm')}"
            exec_cmd(cmd)

        raise NotImplementedError(
            "Disconnected installation from live is not implemented!"
        )
    if config.DEPLOYMENT.get("stage_rh_osbs"):
        raise NotImplementedError(
            "Disconnected installation from stage is not implemented!"
        )

    ocs_registry_image = config.DEPLOYMENT.get("ocs_registry_image", "")
    logger.debug(f"ocs-registry-image: {ocs_registry_image}")
    ocs_registry_image_and_tag = ocs_registry_image.split(":")
    ocs_registry_image = ocs_registry_image_and_tag[0]
    image_tag = (
        ocs_registry_image_and_tag[1] if len(ocs_registry_image_and_tag) == 2 else None
    )
    if not image_tag and config.REPORTING.get("us_ds") == "DS":
        image_tag = get_latest_ds_olm_tag(
            upgrade=False,
            latest_tag=config.DEPLOYMENT.get("default_latest_tag", "latest"),
        )
        ocs_registry_image = f"{config.DEPLOYMENT['default_ocs_registry_image'].split(':')[0]}:{image_tag}"
    bundle_image = f"{constants.OCS_OPERATOR_BUNDLE_IMAGE}:{image_tag}"
    logger.debug(f"ocs-operator-bundle image: {bundle_image}")

    csv_yaml = get_csv_from_image(bundle_image)
    ocs_operator_image = (
        csv_yaml.get("spec", {})
        .get("install", {})
        .get("spec", {})
        .get("deployments", [{}])[0]
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [{}])[0]
        .get("image")
    )
    logger.debug(f"ocs-operator-image: {ocs_operator_image}")

    # prepare list related images (bundle, registry and operator images and all
    # images from relatedImages section from csv)
    ocs_related_images = []
    ocs_related_images.append(get_image_with_digest(bundle_image))
    ocs_registry_image_with_digest = get_image_with_digest(ocs_registry_image)
    ocs_related_images.append(ocs_registry_image_with_digest)
    ocs_related_images.append(get_image_with_digest(ocs_operator_image))
    ocs_related_images += [
        image["image"] for image in csv_yaml.get("spec").get("relatedImages")
    ]
    logger.debug(f"OCS Related Images: {ocs_related_images}")

    mirror_registry = config.DEPLOYMENT["mirror_registry"]
    # prepare images mapping file for mirroring
    mapping_file_content = [
        f"{image}={mirror_registry}{image[image.index('/'):image.index('@')]}\n"
        for image in ocs_related_images
    ]
    logger.debug(f"Mapping file content: {mapping_file_content}")

    name = "ocs-images"
    mapping_file = os.path.join(config.ENV_DATA["cluster_path"], f"{name}-mapping.txt")
    # write mapping file to disk
    with open(mapping_file, "w") as f:
        f.writelines(mapping_file_content)

    # prepare ImageContentSourcePolicy for OCS images
    with open(constants.TEMPLATE_IMAGE_CONTENT_SOURCE_POLICY_YAML) as f:
        ocs_icsp = yaml.safe_load(f)

    ocs_icsp["metadata"]["name"] = name
    ocs_icsp["spec"]["repositoryDigestMirrors"] = []
    for image in ocs_related_images:
        ocs_icsp["spec"]["repositoryDigestMirrors"].append(
            {
                "mirrors": [
                    f"{mirror_registry}{image[image.index('/'):image.index('@')]}"
                ],
                "source": image[: image.index("@")],
            }
        )
    logger.debug(f"OCS imageContentSourcePolicy: {yaml.safe_dump(ocs_icsp)}")

    ocs_icsp_file = os.path.join(
        config.ENV_DATA["cluster_path"], f"{name}-imageContentSourcePolicy.yaml"
    )
    with open(ocs_icsp_file, "w+") as fs:
        yaml.safe_dump(ocs_icsp, fs)

    # create ImageContentSourcePolicy
    exec_cmd(f"oc apply -f {ocs_icsp_file}")

    # mirror images based on mapping file
    with prepare_customized_pull_secret(ocs_related_images) as authfile_fo:
        login_to_mirror_registry(authfile_fo.name)
        exec_cmd(
            f"oc image mirror --filter-by-os='.*' -f {mapping_file} --insecure "
            f"--registry-config={authfile_fo.name} --max-per-registry=2",
            timeout=3600,
        )

    # Disable the default OperatorSources
    exec_cmd(
        """oc patch OperatorHub cluster --type json """
        """-p '[{"op": "add", "path": "/spec/disableAllDefaultSources", "value": true}]'"""
    )

    # wait for newly created imageContentSourcePolicy is applied on all nodes
    wait_for_machineconfigpool_status("all")

    return ocs_registry_image_with_digest
