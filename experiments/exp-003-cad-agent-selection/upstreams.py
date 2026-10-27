"""Transport/observability adapters around the unmodified upstream agent loops."""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

PINS = {
    "agentic": "c084034cdcf462025a60ee70c5ba94577428e5ca",
    "itercad": "55998c4a4537573f67ca241dedc52ee2840652ca",
}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def verify_checkout(path, pin):
    path = Path(path).resolve()
    sha = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True).strip()
    if sha != pin or dirty:
        raise ValueError(f"Upstream must be clean and pinned: {path.name}; actual={sha}, dirty={bool(dirty)}")
    return sha


def make_transport(config, model, output, label):
    """No call occurs here; the root task owns authorization and the shared ledger.

    An optional local factory has signature factory(config, model, output, label).
    The built-in path reuses the repository's existing Budget and Transport.
    """
    custom = config.get("transport_factory")
    if custom:
        factory = module("exp003_private_transport", custom["path"])
        return getattr(factory, custom.get("function", "create_transport"))(config, model, output, label)
    root = Path(__file__).resolve().parents[2]
    provider = module("exp003_provider", root / "research/agenticcad_edit/provider.py")
    pricing = dict(model["pricing"])
    pricing["model"] = model["model"]
    budget = provider.Budget(Path(config["ledger"]), float(config["budget_cny"]), pricing)
    return provider.Transport(provider.load_credentials(config["credentials_file"]), budget, output, label)


def model_settings(config, model):
    source = Path(config["upstreams"]["agentic"]) / "src/config/edit_192_external.json"
    upstream = json.loads(source.read_text())["benchmark_models"]["qwen3.6-27b_cadquery-mcp"].copy()
    upstream.update(model=model["model"], backend="cv2", max_tokens=int(model.get("max_tokens", 8192)),
                    temperature=float(model.get("temperature", 0.6)), top_p=float(model.get("top_p", 0.95)))
    upstream["chat_template_kwargs"] = {"enable_thinking": bool(model.get("thinking", True))}
    return upstream


def _usage_record(transport):
    folder = getattr(transport, "output", None)
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if folder:
        for path in Path(folder).glob("call-*-response.json"):
            data = json.loads(path.read_text())
            for key in totals:
                totals[key] += (data.get("usage") or {}).get(key, 0) or 0
    return {"calls": int(getattr(transport, "counter", 0)), "usage": totals}


def _install_agentic(config, model, transport):
    path = Path(config["upstreams"]["agentic"]).resolve()
    sys.path.insert(0, str(path))
    from src.vlms.base_vlm import BaseVLM
    from src.vlms.vllm_openai import VLM
    settings = model_settings(config, model)

    class ApiAgent(VLM):
        def __init__(self):
            # Avoid constructing the upstream unbudgeted SDK client.
            BaseVLM.__init__(self, settings, cache=False)
            self.config = settings

        def _chat_create(self, messages, tools=None):
            kwargs = dict(model=settings["model"], messages=messages,
                          max_tokens=settings["max_tokens"], temperature=settings["temperature"],
                          top_p=settings["top_p"],
                          extra_body={"chat_template_kwargs": settings["chat_template_kwargs"]})
            if tools is not None:
                kwargs.update(tools=tools, tool_choice="auto")
            return transport.create(**kwargs)

    return ApiAgent()


def run_agentic(config, model, transport, folder, request):
    """Execute BaseVLM.cadquery_mcp unchanged, including its original tool loop."""
    import mcp
    agent = _install_agentic(config, model, transport)
    events = []
    original = mcp.ClientSession

    class ObservedSession(original):
        async def call_tool(self, name, arguments=None, **kwargs):
            started = time.monotonic()
            result = await super().call_tool(name, arguments=arguments, **kwargs)
            texts, images = [], []
            for i, content in enumerate(result.content):
                if content.type == "text":
                    texts.append(content.text)
                elif content.type == "image":
                    image_path = folder / f"tool-{len(events):04d}-{i}.png"
                    image_path.write_bytes(base64.b64decode(content.data))
                    images.append(image_path.name)
            events.append({"tool": name, "arguments": arguments or {}, "texts": texts,
                           "images": images, "is_error": result.isError,
                           "elapsed_s": time.monotonic() - started})
            dump(folder / "tool-events.json", events)
            return result

    mcp.ClientSession = ObservedSession
    try:
        task = dict(request.get("task", {}))
        if request.get("input_step"):
            task["brep_start_path_step"] = request["input_step"]
        if request.get("source_path"):
            # Source is request-side context. Only cq/result are guaranteed in the
            # official imported-STEP session; reference programs are never injected.
            source = Path(request["source_path"]).read_text()
            instruction = request["instruction"] + (
                "\nRequest-side source program is provided for context below. The current STEP is already "
                "loaded as result; only cq and result are guaranteed bindings in the live session.\n"
                f"```python\n{source}\n```")
        else:
            instruction = request["instruction"]
        result = agent.cadquery_mcp(
            instruction, task,
            str(Path(config["upstreams"]["agentic"]) / "src/harnesses/cadquery_mcp/server.py"),
            str(folder), int(model.get("agentic_max_calls", 40)),
        )
        transcript = getattr(agent, "_last_mcp_messages", [])
        dump(folder / "messages.json", transcript)
        finished = any(call.get("function", {}).get("name") == "finish"
                       for msg in transcript if isinstance(msg, dict) for call in msg.get("tool_calls", []))
        return {"exit": "finish" if finished else "upstream_stop", "upstream_result": result,
                "output_step": str(folder / "tmp.step"), "tool_count": len(events),
                "protocol": "official_mcp" if not request.get("source_path") else "official_mcp_with_source_context"}
    finally:
        mcp.ClientSession = original
        dump(folder / "tool-events.json", events)


def run_itercad(config, model, transport, folder, request):
    """Call process_itercad_edit unchanged; replace only its model transport."""
    upstream = Path(config["upstreams"]["itercad"]).resolve()
    sys.path.insert(0, str(upstream / "eval"))
    evaluator = module("exp003_itercad", upstream / "eval/evalution.py")
    source_path = Path(request["source_path"])
    extra_content = request.get("extra_content")

    class ApiClient:
        def __init__(self, cfg):
            self.system_prompt = cfg["system_prompt"]

        def chat(self, messages, **kwargs):
            outgoing = json.loads(json.dumps(messages))
            if extra_content:
                outgoing[1]["content"] = [{"type": "text", "text": outgoing[1]["content"]}] + extra_content
            response = transport.create(
                model=model["model"], messages=outgoing,
                max_tokens=int(model.get("max_tokens", 8192)),
                temperature=float(model.get("temperature", 0.6)), top_p=float(model.get("top_p", 0.95)),
                extra_body={"chat_template_kwargs": {"enable_thinking": bool(model.get("thinking", True))}},
            )
            content = response.choices[0].message.content
            return content or "", None

    evaluator.GeneratorClient = ApiClient
    sample = {"uid": request["id"], "input_code_path": str(source_path),
              "instruction_quantitative": request["instruction"]}
    # Never pass target_code_path, gt_stl_path, or target_stl_path to the generator.
    settings = dict(evaluator.DEFAULT_CONFIG)
    settings.update(temp_dir=str(folder / "tmp"), max_turns=int(model.get("itercad_max_calls", 5)),
                    pass_k=1, system_prompt=evaluator.SYSTEM_PROMPT)
    result = evaluator.process_itercad_edit(sample, settings)
    dump(folder / "itercad-trace.json", result)
    code = result.get("last_valid_code")
    output_path = folder / "tmp.step"
    code_path = folder / "generated.py"
    if code:
        code_path.write_text(code)
        ok, message, value = evaluator.execute_code(code)
        if ok:
            import cadquery as cq
            cq.exporters.export(value, str(output_path))
        else:
            dump(folder / "export-error.json", {"error": message})
    turns = result.get("all_turns_data", [])
    last = turns[-1] if turns else {}
    return {"exit": last.get("status", "empty"), "upstream_status": result.get("status"),
            "output_step": str(output_path), "source_path": str(code_path) if code else None,
            "final_turn_executable": bool(last.get("exec_success") or last.get("status") == "done_by_model"),
            "protocol": "official_source_edit" if not extra_content else "step_multimodal_edit_adapter",
            "turns": len(turns)}


def run_candidate(config, model, candidate, folder, request):
    verify_checkout(config["upstreams"][candidate], PINS[candidate])
    folder = Path(folder)
    transport = make_transport(config, model, folder / "api", request["label"])
    with (folder / "harness.log").open("w") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        if candidate == "agentic":
            result = run_agentic(config, model, transport, folder, request)
        elif candidate == "itercad":
            result = run_itercad(config, model, transport, folder, request)
        else:
            raise ValueError(f"Unknown complete framework {candidate}")
    result.update(_usage_record(transport))
    return result
