"""Opt-in CUDA smoke for SAO's actual compiled loss and optimizer update.

This is a synthetic policy-parameter test, not a Slime/Megatron training loop.
Set REEF_SAO_CUDA_SMOKE=1 to require CUDA; missing CUDA then fails, never skips.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("REEF_SAO_CUDA_SMOKE") != "1",
        reason="set REEF_SAO_CUDA_SMOKE=1 to require the real CUDA smoke",
    ),
]


@pytest.mark.parametrize("precision", ["float32", "bfloat16"])
def test_sao_cuda_update_and_checkpoint(precision: str, tmp_path: Path) -> None:
    """Check masked gradients, update direction, and checkpoint reload on CUDA."""
    import torch

    from recipes.sao.slime.objective import compute_sao_loss

    assert torch.cuda.is_available(), "CUDA is required when this smoke is explicitly enabled"
    if precision == "bfloat16":
        assert torch.cuda.is_bf16_supported(), "The selected GPU must support BF16"
        weight_dtype = torch.bfloat16
    else:
        weight_dtype = torch.float32

    # Independent log-probability parameters make the expected gradient explicit:
    # positive advantage increases log probability; negative advantage decreases it.
    policy_log_probs = torch.nn.Parameter(torch.full((4,), -2.0, device="cuda", dtype=weight_dtype))
    # Ratios are [1, 1, exp(-2), exp(2)]; the last two lie outside (0.7, 6).
    rollout_log_probs = torch.tensor([-2.0, -2.0, 0.0, -4.0], device="cuda", dtype=weight_dtype)
    advantages = torch.tensor([1.0, -1.0, 1.0, -1.0], device="cuda", dtype=weight_dtype)
    optimizer = torch.optim.SGD([policy_log_probs], lr=0.125)
    before_update = policy_log_probs.detach().clone()

    optimizer.zero_grad()
    losses, masked_tokens = compute_sao_loss(
        rollout_log_probs - policy_log_probs, policy_log_probs, advantages, 0.3, 5.0
    )
    loss = losses.float().mean()
    assert torch.isfinite(loss)
    loss.backward()
    torch.cuda.synchronize()

    assert policy_log_probs.grad is not None
    assert torch.isfinite(policy_log_probs.grad).all()
    torch.testing.assert_close(masked_tokens, torch.tensor([0.0, 0.0, 1.0, 1.0], device="cuda"))
    # This also detects accidentally differentiating through the calibration ratio.
    torch.testing.assert_close(
        policy_log_probs.grad,
        torch.tensor([-0.25, 0.25, 0.0, 0.0], device="cuda", dtype=weight_dtype),
        rtol=0,
        atol=0,
    )
    optimizer.step()
    assert policy_log_probs[0] > before_update[0]
    assert policy_log_probs[1] < before_update[1]
    torch.testing.assert_close(policy_log_probs[2:], before_update[2:], rtol=0, atol=0)

    checkpoint_path = tmp_path / "synthetic-policy.pt"
    torch.save({"policy_log_probs": policy_log_probs.detach()}, checkpoint_path)
    restored = torch.load(checkpoint_path, map_location="cuda", weights_only=True)
    torch.testing.assert_close(restored["policy_log_probs"], policy_log_probs, rtol=0, atol=0)
    print(
        f"device={torch.cuda.get_device_name(0)} precision={precision} "
        f"torch={torch.__version__} cuda={torch.version.cuda} "
        f"max_parameter_delta={(policy_log_probs - before_update).abs().max().item()}"
    )
