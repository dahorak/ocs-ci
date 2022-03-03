import logging
import os

from ocs_ci.utility.utils import exec_cmd

from ocs_ci.framework import config

log = logging.getLogger(__name__)


def test_multicluster_dahorak():
    log.info(f"Number of clusters: {config.nclusters}")
    for context in range(config.nclusters):
        # switch context
        config.switch_ctx(context)
        # print the cluster name of the actual cluster
        log.info(f"CLUSTER_NAME: {config.ENV_DATA['cluster_name']}")
        # run oc command against the actual cluster
        log.info(exec_cmd("oc version").stdout)

        log.info(os.environ["KUBECONFIG"])

    # access configuration of particular cluster (without switching context to it
    log.info(
        f"Cluster name of first cluster: {config.clusters[0].ENV_DATA['cluster_name']}"
    )
    if config.nclusters > 1:
        log.info(
            f"Cluster name of second cluster: {config.clusters[1].ENV_DATA['cluster_name']}"
        )
