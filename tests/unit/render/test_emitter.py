"""Tests for `host_config.render.emitter.render_for`.

Unit-level. Three classes of assertions:

- **Byte-equality with goldens** — the regression net. Any change to
  templates or emitter logic must update the goldens deliberately.
- **Determinism / canonicalization** — shuffling list-shaped intent
  fields (NIC order, bond member order) produces identical bytes.
- **Property-based** — for any valid HostIntent (sampled via Hypothesis
  from the validated factories with permutations), the rendered
  network-config parses cleanly as YAML and carries the expected
  top-level Netplan v2 shape. Round-tripping through a YAML loader is
  the closest practical proxy for "Netplan would accept this" in a
  unit test.
"""

from __future__ import annotations

import random
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from jinja2 import UndefinedError

from host_config.models.intent import HostIntent
from host_config.render import emitter as emitter_mod
from host_config.render.emitter import FileKind, render_for
from tests.unit.models.test_intent import (
    make_b200_intent,
    make_b300_intent,
    make_cpu_intent,
    make_h200_intent,
)

GOLDEN_ROOT = Path(__file__).parents[3] / "src" / "host_config" / "render" / "golden"

# A frozen instant used for any test that touches the `now` argument.
# Tests that don't care about `now` get the default wall clock — that's
# fine because no current template references it.
FROZEN_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Byte-equality with goldens — the regression net.
# ---------------------------------------------------------------------------


class TestGoldens:
    """`render_for(intent, kind)` is byte-equal to the on-disk golden file.

    Why this is the load-bearing test:
        Any template tweak — even a whitespace change — fails this
        test, forcing the author to regenerate the golden and visually
        diff it. That diff IS the review surface for renderer changes.

    Regenerating goldens (when the change is intentional):
        ``uv run python tests/unit/render/regen_goldens.py``  (M2-5
        provides this; until then, regenerate by hand via a REPL).
    """

    @pytest.mark.fast
    @pytest.mark.parametrize(
        ("role", "factory"),
        [
            ("cpu", make_cpu_intent),
            ("gpu-b300", make_b300_intent),
            ("gpu-b200", make_b200_intent),
            ("gpu-h200", make_h200_intent),
        ],
    )
    @pytest.mark.parametrize("kind", list(FileKind))
    def test_render_matches_golden(self, role: str, factory: object, kind: FileKind) -> None:
        """Each (role, file_kind) pair renders to the byte-identical golden."""
        intent: HostIntent = factory()  # type: ignore[operator]
        golden = (GOLDEN_ROOT / role / kind.value).read_bytes()
        actual = render_for(intent, kind)
        assert actual == golden, (
            f"renderer drifted from golden {role}/{kind.value}. "
            "If the change is intentional, regenerate the golden and review the diff."
        )


# ---------------------------------------------------------------------------
# Determinism — order-independence of upstream lists.
# ---------------------------------------------------------------------------


class TestDeterminism:
    """The emitter sorts list-shaped fields before rendering.

    Why:
        pynetbox makes no promises about iteration order; the loader
        relays whatever Netbox sends. The renderer canonicalizes so
        byte-equal goldens hold regardless of upstream order.
    """

    @pytest.mark.fast
    def test_ns_nic_order_does_not_affect_output(self) -> None:
        """Shuffling `ns_nics` produces identical bytes.

        Approach:
            Round-trip the intent through ``model_validate`` with the
            ns_nics list reversed; render; compare to the canonical
            render. They must match exactly.
        """
        intent = make_cpu_intent()
        dumped = intent.model_dump(mode="python")
        # Reverse the list to invert the natural sort order.
        dumped["ns_nics"] = list(reversed(dumped["ns_nics"]))
        shuffled = HostIntent.model_validate(dumped)

        canonical = render_for(intent, FileKind.NETWORK_CONFIG)
        rerendered = render_for(shuffled, FileKind.NETWORK_CONFIG)
        assert canonical == rerendered

    @pytest.mark.fast
    def test_roce_underlay_order_does_not_affect_output(self) -> None:
        """Shuffling `roce_underlays` produces identical bytes.

        Why:
            8 RoCE underlays times any permutation = 40320 permutations.
            We can't enumerate them; we shuffle with a fixed seed for
            reproducibility.
        """
        intent = make_b300_intent()
        dumped = deepcopy(intent.model_dump(mode="python"))
        rng = random.Random(42)  # noqa: S311 — non-crypto; deterministic shuffle for test
        rng.shuffle(dumped["roce_underlays"])
        shuffled = HostIntent.model_validate(dumped)

        canonical = render_for(intent, FileKind.NETWORK_CONFIG)
        rerendered = render_for(shuffled, FileKind.NETWORK_CONFIG)
        assert canonical == rerendered

    @pytest.mark.fast
    def test_bond_member_order_does_not_affect_output(self) -> None:
        """Shuffling `bond.members` produces identical bytes."""
        intent = make_cpu_intent()
        dumped = intent.model_dump(mode="python")
        dumped["bond"]["members"] = list(reversed(dumped["bond"]["members"]))
        shuffled = HostIntent.model_validate(dumped)
        assert render_for(intent, FileKind.NETWORK_CONFIG) == render_for(
            shuffled, FileKind.NETWORK_CONFIG
        )


# ---------------------------------------------------------------------------
# Signature contract — return type, encoding, kind-string acceptance.
# ---------------------------------------------------------------------------


class TestSignature:
    """Surface-level invariants of the public `render_for` API."""

    @pytest.mark.fast
    def test_returns_bytes(self) -> None:
        """The renderer returns `bytes`, not `str`."""
        assert isinstance(render_for(make_cpu_intent(), FileKind.META_DATA), bytes)

    @pytest.mark.fast
    def test_accepts_string_kind(self) -> None:
        """A bare ``"meta-data"`` string is accepted alongside the enum value."""
        as_enum = render_for(make_cpu_intent(), FileKind.META_DATA)
        as_str = render_for(make_cpu_intent(), "meta-data")
        assert as_enum == as_str

    @pytest.mark.fast
    def test_rejects_unknown_kind(self) -> None:
        """An unknown kind string raises ``ValueError`` (enum membership check)."""
        with pytest.raises(ValueError, match="not a valid"):
            render_for(make_cpu_intent(), "metadata")  # missing the hyphen

    @pytest.mark.fast
    def test_now_is_injectable(self) -> None:
        """The `now` argument is accepted; output is identical regardless of value.

        Why:
            No current template references `now`, so the rendered bytes
            are the same regardless of what we pass. This test pins
            *that* contract — when a future template starts using
            `now`, this test will need updating, and the update is
            cheap because the call sites are already plumbed.
        """
        intent = make_cpu_intent()
        baseline = render_for(intent, FileKind.META_DATA)
        with_now = render_for(intent, FileKind.META_DATA, now=FROZEN_NOW)
        assert baseline == with_now


# ---------------------------------------------------------------------------
# Property: rendered network-config parses as valid YAML with Netplan shape.
# ---------------------------------------------------------------------------


def _intents_strategy() -> st.SearchStrategy[HostIntent]:
    """Hypothesis strategy: one of the two role intents, possibly shuffled.

    Approach:
        We don't synthesize raw HostIntents from primitives — the cross-
        field invariants are too numerous to express cleanly in a
        Hypothesis strategy. Instead, we sample from the known-good
        factories and apply order permutations on list fields. That
        gives Hypothesis a tractable search space while still
        exercising the determinism path.
    """

    @st.composite
    def _build(draw: st.DrawFn) -> HostIntent:
        factory = draw(
            st.sampled_from(
                [make_cpu_intent, make_b300_intent, make_b200_intent, make_h200_intent]
            )
        )
        intent = factory()
        seed = draw(st.integers(min_value=0, max_value=10_000))
        rng = random.Random(seed)  # noqa: S311 — non-crypto; deterministic shuffle for test
        dumped = intent.model_dump(mode="python")
        rng.shuffle(dumped["ns_nics"])
        rng.shuffle(dumped["vlans"])
        rng.shuffle(dumped["roce_underlays"])
        rng.shuffle(dumped["ib_underlays"])
        rng.shuffle(dumped["bond"]["members"])
        return HostIntent.model_validate(dumped)

    return _build()


class TestPropertyValidYaml:
    """For any sampled intent, network-config parses as Netplan-shaped YAML."""

    @pytest.mark.fast
    @given(intent=_intents_strategy())
    @settings(max_examples=25, deadline=None)
    def test_network_config_parses_and_has_netplan_shape(self, intent: HostIntent) -> None:
        """Rendered network-config is valid YAML with a `network: version: 2` root.

        Why:
            "Netplan would accept this" is, in a unit test, best
            approximated by: the bytes are valid YAML, the root key is
            `network`, the version is 2, and ethernets/bonds/vlans are
            present with the expected names. Catches the regression
            class where a template tweak accidentally produces
            unindented blocks or duplicate keys.
        """
        out = render_for(intent, FileKind.NETWORK_CONFIG)
        parsed = yaml.safe_load(out)
        assert parsed["network"]["version"] == 2
        ethernets = parsed["network"]["ethernets"]
        # Both roles have nsa + nsb at minimum.
        assert {"nsa", "nsb"}.issubset(ethernets.keys())
        # Exactly one VLAN carries a default route.
        vlans = parsed["network"]["vlans"]
        gw_count = sum(1 for v in vlans.values() if "routes" in v)
        assert gw_count == 1


# ---------------------------------------------------------------------------
# Negative: strict undefined still bites at the emitter layer.
# ---------------------------------------------------------------------------


class TestStrictUndefinedThroughEmitter:
    """If the emitter ever stops passing a required field, the template raises.

    Why:
        Belt-and-braces. The template-layer test already covers
        StrictUndefined. This re-checks it through the emitter to
        prevent a regression where the emitter silently absorbs an
        UndefinedError (e.g., via a try/except that swallows).
    """

    @pytest.mark.fast
    def test_renderer_propagates_undefined_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If `_canonicalize` drops `bond`, the template raises UndefinedError."""
        original = emitter_mod._canonicalize

        def broken(intent: HostIntent) -> dict[str, object]:
            ctx = original(intent)
            del ctx["bond"]
            return ctx

        monkeypatch.setattr(emitter_mod, "_canonicalize", broken)

        with pytest.raises(UndefinedError):
            render_for(make_cpu_intent(), FileKind.NETWORK_CONFIG)
