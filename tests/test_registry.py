"""The source registry is fail-closed and parses the pinned config."""

import textwrap

from headroom.sources import SourceRegistry, load_registry


def test_known_public_sources_load_and_are_publishable():
    reg = load_registry()
    spp = reg["spp_binding_constraints"]
    assert spp.bucket == "b"
    assert spp.publishable is True
    assert spp.source_version  # pinned, non-empty


def test_ceii_source_is_present_but_not_publishable():
    reg = load_registry()
    ceii = reg["ferc_form_715"]
    assert ceii.tier == "x"
    assert ceii.publishable is False  # quarantined


def test_compliance_defaults_fail_closed(tmp_path):
    # A source with NO compliance block must default to non-publishable.
    yaml_text = textwrap.dedent(
        """
        sources:
          - name: mystery
            bucket: b
            tier: 4
            source_version: v1
        """
    )
    p = tmp_path / "sources.yaml"
    p.write_text(yaml_text)
    reg = SourceRegistry.from_yaml(p)
    assert reg["mystery"].publishable is False


def test_unknown_source_lookup():
    reg = load_registry()
    assert reg.get("does_not_exist") is None
    assert "does_not_exist" not in reg
