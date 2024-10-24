import ttnn
import torch

from entropix.config import LLAMA_1B_PARAMS
from entropix.torch_weights import XfmrWeights
from entropix.torch_model import (
    feed_forward, 
    rms_norm,
    apply_rotary_emb,
    attention,
    xfmr
)
from entropix.torch_main import precompute_freqs_cis
from entropix.ttnn.ttnn_model import (
    ttnn_feedforward, 
    ttnn_rms_norm,
    ttnn_attention,
    ttnn_xfmr
)
from entropix.ttnn.ttnn_stats import TTNNAttnStats
from entropix.ttnn.ttnn_weights import load_weights, convert_to_ttnn_xfmr_weights,TTNNXfmrWeights
from entropix.ttnn.llama_common import (
    compute_gather_cos_sin,
    get_rot_transformation_mat
)
from entropix.ttnn.ttnn_kvcache import TTNN_KVCache
from entropix.torch_kvcache import KVCache

head_dim = LLAMA_1B_PARAMS.head_dim
rope_theta = LLAMA_1B_PARAMS.rope_theta
use_scaled_rope = LLAMA_1B_PARAMS.use_scaled_rope
start_pos = 0
seq_len = 2048

def compare_tensor_accuracy(pred_tensor, true_tensor, name="Tensor"):
    """
    Generated by claude

    Compare predicted and true tensors using the most relevant metrics.
    
    Args:
        pred_tensor (torch.Tensor): Predicted tensor (e.g., TTNN output)
        true_tensor (torch.Tensor): Ground truth tensor
        name (str): Name of the tensor for reporting
    """
    # Relative error (most important for comparing neural network outputs)
    rel_error = torch.abs(pred_tensor - true_tensor) / (torch.abs(true_tensor) + 1e-8)
    
    # Cosine similarity (important for checking if patterns are preserved)
    cos_sim = torch.nn.functional.cosine_similarity(
        pred_tensor.view(-1), 
        true_tensor.view(-1),
        dim=0
    )
    
    print(f"\n=== {name} Accuracy ===")
    print(f"Max Relative Error: {rel_error.max().item():.6f}")
    print(f"Mean Relative Error: {rel_error.mean().item():.6f}")
    print(f"Cosine Similarity: {cos_sim.item():.6f}")
    
    # Optional: Print warning if accuracy might be problematic
    if rel_error.mean().item() > 0.01:  # more than 1% average error
        print("Warning: Mean relative error exceeds 1%")
    if cos_sim.item() < 0.99:  # less than 0.99 cosine similarity
        print("Warning: Low cosine similarity might indicate significant pattern differences")


def test_llama_rms(xfmr_weights: XfmrWeights, ttnn_xfmr_weights: TTNNXfmrWeights, device: ttnn.Device=None):
    x = torch.rand((1, 256, 2048), dtype=torch.bfloat16)
    ttnn_x = ttnn.from_torch(x, device=device, layout=ttnn.TILE_LAYOUT)

    #for i in range(LLAMA_1B_PARAMS.n_layers):
    for i in range(1):
        print(f"Layer: {i}")
        
        # Test TTNN
        out_ttnn = ttnn_rms_norm(ttnn_x, ttnn_xfmr_weights.layer_weights[i].ffn_norm)
        out_ttnn = ttnn.to_torch(out_ttnn)

        # Test Golden
        out_golden = rms_norm(x, xfmr_weights.layer_weights[i].ffn_norm)

        print(f"TTNN: {out_ttnn}")
        print(f"Golden: {out_golden}")

def test_llama_ffw(xfmr_weights: XfmrWeights, ttnn_xfmr_weights: TTNNXfmrWeights, device: ttnn.Device=None):
    x = torch.rand((1, 256, 2048), dtype=torch.bfloat16)
    ttnn_x = ttnn.from_torch(x, device=device, layout=ttnn.TILE_LAYOUT)

    for i in range(LLAMA_1B_PARAMS.n_layers):
        print(f"Layer: {i}")
        
        # Test TTNN
        out_ttnn = ttnn_feedforward(ttnn_x, ttnn_xfmr_weights.layer_weights[i])
        out_ttnn = ttnn.to_torch(out_ttnn)

        # Test Golden
        out_golden = feed_forward(x, xfmr_weights.layer_weights[i])

        print(f"TTNN: {out_ttnn}")
        print(f"Golden: {out_golden}")

# def test_llama_apply_rotary_embedding(device: ttnn.Device = None):
#     xq = torch.randn(1, 256, 32, 64, dtype=torch.float16)
#     xk = torch.randn(1, 256, 32, 64, dtype=torch.float16)
# 
#     rope_theta = LLAMA_1B_PARAMS.rope_theta
#     use_scaled_rope = LLAMA_1B_PARAMS.use_scaled_rope
# 
#     freq_cis = precompute_freqs_cis(64, 256, rope_theta, use_scaled_rope, dtype=torch.bfloat16)
# 
#     ttnn_xq = ttnn.from_torch(xq, device=device, layout=ttnn.TILE_LAYOUT)
#     ttnn_xk = ttnn.from_torch(xk, device=device, layout=ttnn.TILE_LAYOUT)
# 
#     trans_mat = get_rot_transformation_mat(head_dim, device=device)
#     cos, sin = compute_gather_cos_sin(head_dim, seq_len, torch.arange(0, 0 + 256), use_scaled_rope=use_scaled_rope, device=device)
# 
#     ttnn_q_out, ttnn_k_out = ttnn_apply_rotary_emb(ttnn_xq, ttnn_xk, cos, sin, trans_mat, device)
#     q_out, k_out = apply_rotary_emb(xq, xk, freq_cis, dtype=torch.bfloat16)
#     
#     print(f"TTNN q_out: {ttnn_q_out}")
#     print(f"Golden q_out: {q_out}")
#     print(f"TTNN k_out: {ttnn_k_out}")
#     print(f"Golden k_out: {k_out}")

def test_llama_attention(xfmr_weights: XfmrWeights, ttnn_xfmr_weights: TTNNXfmrWeights, device: ttnn.Device = None):
    model_params = LLAMA_1B_PARAMS
    x = torch.randn((1, 256, 2048), dtype=torch.bfloat16)
    ttnn_kv_cache = TTNN_KVCache(
        shape=(1, model_params.n_local_kv_heads, 256, model_params.head_dim), 
        device=device
    )
    cos, sin = compute_gather_cos_sin(head_dim, seq_len, torch.arange(0, 0 + 256), use_scaled_rope=use_scaled_rope, device=device)
    trans_mat = get_rot_transformation_mat(head_dim, device=device)
    freq_cis = precompute_freqs_cis(64, 256, rope_theta, use_scaled_rope, dtype=torch.bfloat16)
    kvcache = KVCache.new(model_params.n_layers, 1, 2048, model_params.n_local_kv_heads, model_params.head_dim)
    ttnn_x = ttnn.from_torch(x, device=device, layout=ttnn.TILE_LAYOUT)
    
    ttnn_out, ttnn_kv_cache, ttnn_pre_scores = ttnn_attention(ttnn_x, ttnn_xfmr_weights.layer_weights[0], model_params, 0, 0, cos, sin, trans_mat, ttnn_kv_cache, device)
    out, kv_cache, pre_scores = attention(x, xfmr_weights.layer_weights[0], model_params, 0, 0, freq_cis, kvcache, attn_mask=None)

    print(f"TTNN: {ttnn_out}")
    print(f"Golden: {out}")

    diff = torch.abs(ttnn.to_torch(ttnn_out) - out)
    print(f"Diff: {diff}")

def test_llama_xfmr(xfmr_weights: XfmrWeights, ttnn_xfmr_weights: TTNNXfmrWeights, device: ttnn.Device = None):
    model_params = LLAMA_1B_PARAMS
    tokens = torch.randint(0, model_params.vocab_size, (1, 256))
    ttnn_tokens = ttnn.from_torch(tokens, dtype=ttnn.uint32, device=device)
    ttnn_kv_cache = TTNN_KVCache(
        shape=(1, model_params.n_local_kv_heads, 256, model_params.head_dim),
        device=device
    )
    kvcache = KVCache.new(model_params.n_layers, 1, 2048, model_params.n_local_kv_heads, model_params.head_dim)
    
    cur_pos = 0
    cos, sin = compute_gather_cos_sin(head_dim, seq_len, torch.arange(0, 0 + 256), use_scaled_rope=use_scaled_rope, device=device)
    freq_cis = precompute_freqs_cis(head_dim, 256, rope_theta, use_scaled_rope, dtype=torch.bfloat16)

    print("Running PyTorch implementation...")
    logits, kvcache, scores, stats = xfmr(xfmr_weights, model_params, tokens, cur_pos, freq_cis, kvcache)
    
    print("Running TTNN implementation...")
    ttnn_logits, ttnn_kv_cache, ttnn_scores, ttnn_stats = ttnn_xfmr(ttnn_xfmr_weights, model_params, ttnn_tokens, cos, sin, ttnn_kv_cache)
    
    ttnn_logits_torch = ttnn.to_torch(ttnn_logits)
    ttnn_scores_torch = ttnn_scores
    ttnn_entropy_torch = ttnn_stats.entropy
    torch_entropy = stats.entropy
    
    print("\nComparing results:")
    logits_diff = torch.abs(ttnn_logits_torch - logits)
    scores_diff = torch.abs(ttnn_scores_torch - scores)
    
    print(f"Max logits difference: {logits_diff.max().item()}")
    print(f"Mean logits difference: {logits_diff.mean().item()}")
    print(f"Max scores difference: {scores_diff.max().item()}")
    print(f"Mean scores difference: {scores_diff.mean().item()}")
    
    print("\nComparing attention stats:")
    entropy_diff = torch.abs(ttnn_entropy_torch - torch_entropy)
    print(f"Max entropy difference: {entropy_diff.max().item()}")
    print(f"Mean entropy difference: {entropy_diff.mean().item()}")

    compare_tensor_accuracy(ttnn_logits_torch, logits, "Logits")
    compare_tensor_accuracy(ttnn_scores_torch, scores, "Scores")

device = ttnn.open_device(device_id=0)

try:
    xfmr_weights = load_weights()
    ttnn_xfmr_weights = convert_to_ttnn_xfmr_weights(xfmr_weights, device)
    #test_llama_rms(xfmr_weights, ttnn_xfmr_weights, device=device)
    #test_llama_ffw(xfmr_weights, ttnn_xfmr_weights, device=device)
    #test_llama_apply_rotary_embedding(device=device)
    #test_llama_attention(xfmr_weights, ttnn_xfmr_weights, device=device)
    test_llama_xfmr(xfmr_weights, ttnn_xfmr_weights, device=device)
except Exception as e:
    raise e
finally:
    ttnn.close_device(device)