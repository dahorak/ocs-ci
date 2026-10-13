import logging
import pytest

from ocs_ci.framework.testlib import (
    polarion_id,
)
from ocs_ci.framework.pytest_customization.marks import tier2

from ocs_ci.utility.utils import exec_cmd
from ocs_ci.framework.testlib import purple_squad


# from ocs_ci.framework import config

log = logging.getLogger(__name__)


@purple_squad
@tier2
def test_dahorak():
    """
    """
    log.info(" $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$ Dummy test case")
    assert True


@pytest.mark.parametrize(
    argnames=["test_param"],
    argvalues=[
        pytest.param(
            *["Variant-A"],
            marks=pytest.mark.polarion_id("VariantAAA"),
        ),
        pytest.param(
            *["Variant-B"],
            marks=pytest.mark.polarion_id("VariantBBB"),
        ),
        pytest.param(
            *["Variant-C"],
            marks=pytest.mark.polarion_id("VariantCCC"),
        ),
        pytest.param(
            *["Variant-D"],
            marks=pytest.mark.polarion_id("VariantDDD"),
        ),
    ],
)
@purple_squad
def test_dahorak_parametrized(test_param):
    """
    """
    log.info(f"Dummy parametrized test case with separated Polarion IDs - variant: {test_param}")
    assert True


@polarion_id("AAA")
@polarion_id("BBB")
@polarion_id("CCC")
@polarion_id("DDD")
@purple_squad
def test_dahorak_multiple_polarion_ids():
    """
    """
    log.info("Dummy test case with multiple Polarion ids")
    assert True
