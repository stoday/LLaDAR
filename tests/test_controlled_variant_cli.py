"""CLI selection contract for dynamically planned controlled-variant dimensions."""

import pytest

from lladar.cli import build_parser


def test_create_dataset_cli_accepts_only_the_generic_controlled_variant_flags():
    parser = build_parser()

    selected = parser.parse_args([
        "create", "test-dataset", "--knowledge", "plans.md",
        "--controlled-variant-topics", "customer_context,language_context",
    ])

    assert (selected.controlled_variant_probes, selected.controlled_variant_topics) == (
        False,
        "customer_context,language_context",
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["create", "test-dataset", "--knowledge", "plans.md", "--demographic-topics", "age"])
