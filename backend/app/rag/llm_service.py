import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_TOKENIZER = None
_MODEL = None
_MODEL_NAME = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def get_llm_model_name() -> str:
    return os.getenv("LLM_MODEL_NAME", "Qwen/Qwen2.5-0.5B-Instruct").strip()


def get_llm_device() -> str:
    try:
        import torch

        requested = os.getenv("LLM_DEVICE", "cpu").strip().lower()
        if requested == "cuda" and torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except Exception:
        return "cpu"


def is_llm_enabled() -> bool:
    return _env_bool("ENABLE_LLM_GENERATION", False)


def load_llm():
    """
    Lazy-load the local open-source LLM once per FastAPI process.

    Recommended local CPU model:
      Qwen/Qwen2.5-0.5B-Instruct
    """
    global _TOKENIZER, _MODEL, _MODEL_NAME

    model_name = get_llm_model_name()
    device = get_llm_device()

    if _TOKENIZER is not None and _MODEL is not None and _MODEL_NAME == model_name:
        return _TOKENIZER, _MODEL

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    logger.info("Loading local LLM model: %s on %s", model_name, device)

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)

    dtype = torch.float16 if device == "cuda" else torch.float32

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=dtype,
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    model.to(device)
    model.eval()

    _TOKENIZER = tokenizer
    _MODEL = model
    _MODEL_NAME = model_name

    logger.info("Local LLM model loaded successfully: %s", model_name)

    return _TOKENIZER, _MODEL


def generate_llm_text(prompt: str, *, max_new_tokens: Optional[int] = None, system_prompt: Optional[str] = None) -> Optional[str]:
    """
    Generate plain text from the local LLM.

    This function intentionally returns plain text, not JSON, because small local
    models are more reliable at writing a paragraph than producing strict JSON.
    """
    if not is_llm_enabled():
        logger.warning("LLM generation skipped because ENABLE_LLM_GENERATION is false.")
        return None

    try:
        import torch

        tokenizer, model = load_llm()
        device = get_llm_device()

        max_input_tokens = _env_int("LLM_MAX_INPUT_TOKENS", 1600)
        max_tokens = max_new_tokens or _env_int("LLM_MAX_NEW_TOKENS", 220)
        do_sample = _env_bool("LLM_DO_SAMPLE", False)

        default_system_prompt = (
            "You are an industrial process assistant. You explain sterilizer "
            "pressure-time RCA results in simple, clear English for operators. "
            "Do not output raw tables unless the user specifically asks for a table."
        )

        messages = [
            {
                "role": "system",
                "content": system_prompt or default_system_prompt,
            },
            {"role": "user", "content": prompt},
        ]

        if hasattr(tokenizer, "apply_chat_template"):
            chat_text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            chat_text = prompt

        inputs = tokenizer(
            chat_text,
            return_tensors="pt",
            truncation=True,
            max_length=max_input_tokens,
        ).to(device)

        generation_kwargs = {
            "max_new_tokens": max_tokens,
            "do_sample": do_sample,
            "pad_token_id": tokenizer.eos_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        }

        # Only pass sampling controls if sampling is enabled. This avoids the
        # Transformers warning about temperature/top_p being ignored.
        if do_sample:
            generation_kwargs["temperature"] = float(os.getenv("LLM_TEMPERATURE", "0.2"))
            generation_kwargs["top_p"] = float(os.getenv("LLM_TOP_P", "0.9"))

        with torch.no_grad():
            output_ids = model.generate(**inputs, **generation_kwargs)

        generated_ids = output_ids[0][inputs["input_ids"].shape[-1]:]
        output = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

        logger.info("LLM text generation finished. output_chars=%s", len(output or ""))
        return output or None

    except Exception as exc:
        logger.exception("LLM text generation failed: %s", exc)
        return None
