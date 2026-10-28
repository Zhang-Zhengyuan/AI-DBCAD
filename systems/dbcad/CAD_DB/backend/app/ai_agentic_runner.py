"""Run the pinned official AgenticCADedit loop with native transport adapters.

The complete decision/execution loop remains in upstream BaseVLM._run_mcp.
This process only supplies message/provider transport and starts the native MCP
proxy. Provider credentials and model-generated Python are never loaded here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

from ai_agentic_mcp import rpc
from ai_messages import close_tool_messages


UPSTREAM_REVISION = "c084034cdcf462025a60ee70c5ba94577428e5ca"


def verify_upstream(path):
    path = Path(path).resolve(strict=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
                              text=True, check=True).stdout.strip()
    if revision != UPSTREAM_REVISION:
        raise ValueError("AgenticCADedit checkout does not match the pinned official revision")
    for extra in ([], ["--cached"]):
        subprocess.run(["git", "diff", *extra, "--exit-code", "--", "src/vlms/base_vlm.py",
                        "src/vlms/vllm_openai.py"], cwd=path, capture_output=True, check=True)
    return path


def flat_usage(usage):
    """Provider usage dicts may nest objects (e.g. completion_tokens_details);
    the upstream loop accumulates plain integers only."""
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": int(usage.get("prompt_tokens", 0)),
        "output_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    upstream = verify_upstream(spec["upstream"])
    sys.path.insert(0, str(upstream))
    from src.vlms.vllm_openai import VLM as OfficialVLM
    from src.vlms.base_vlm import BaseVLM, AssistantTurn, ToolCall

    class NativeVLM(OfficialVLM):
        FINISH_TOOL = {
            "type": "function", "function": {
                "name": "finish",
                "description": "Finish with a Chinese summary after inspecting/rendering and checking requirements. Set success=false if unsupported.",
                "parameters": {"type": "object", "properties": {
                    "summary": {"type": "string"}, "success": {"type": "boolean"}},
                    "required": ["summary"]},
            },
        }

        def __init__(self):
            # Do not construct the upstream OpenAI client: model requests are
            # accounted for and performed by the parent API process.
            BaseVLM.__init__(self, {"backend": "ffmpeg"}, cache=False)
            self.config = {"backend": "ffmpeg", "system_prompt": "Follow the native CAD tools and task requirements."}
            self.seed = spec.get("messages") or None
            self.exit_reason = "round_limit"
            self.summary = "本段调用已完成，预览和对话已保留"
            self.success = True

        def create_messages(self, inputs, sys=None):
            if self.seed is not None:
                messages, self.seed = close_tool_messages(self.seed), None
                # Upstream assembles [system, user(conversation_instruction, ...)] via
                # create_messages; a seeded job (image input or resume) bypasses that,
                # so re-inject the native API prompt exactly once per conversation.
                head = json.dumps(messages[:2], ensure_ascii=False)
                has_native = "AgenticCADedit ACIS-native CAD editing assistant" in head
                has_system = bool(messages) and messages[0].get("role") == "system"
                prefix = [] if has_system else [{"role": "system", "content": sys or ""}]
                if not has_native:
                    prefix.append({"role": "user", "content": spec["prompt"]
                                   + "\nInstruction and any reference inputs follow."})
                return prefix + messages
            return super().create_messages(inputs, sys=sys)

        def request_tool_turn(self, messages, tool_specs):
            response = rpc(spec["socket"], "model", {"messages": messages, "tools": tool_specs})
            message = response["choices"][0]["message"]
            calls = []
            for call in message.get("tool_calls") or []:
                arguments = call["function"].get("arguments", "{}")
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                name = call["function"]["name"]
                calls.append(ToolCall(id=call["id"], name=name, args=arguments))
                if name == "finish":
                    self.exit_reason = "finish"
                    self.summary = str(arguments.get("summary") or message.get("content") or "已完成本次预览")[:4000]
                    self.success = arguments.get("success", True) is True
            if not calls:
                self.exit_reason = "no_tool_call"
                content = message.get("content")
                if isinstance(content, list):
                    content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
                self.summary = (str(content or "").strip() or
                                "模型停止调用工具，尚未确认完成；预览和对话已保留")[:4000]
            return AssistantTurn(messages=[message], tool_calls=calls, usage=flat_usage(response.get("usage")))

    model = NativeVLM()
    result = asyncio.run(model._run_mcp(
        spec["mcp_script"], spec["instruction"], {}, spec["output_dir"],
        spec["prompt"], spec["max_calls"],
    ))
    rpc(spec["socket"], "complete", {
        "finished": model.exit_reason == "finish", "success": model.success,
        "exit_reason": model.exit_reason, "summary": model.summary,
        "messages": model._last_mcp_messages,
        "upstream_revision": UPSTREAM_REVISION, "result": result,
    })


if __name__ == "__main__":
    main(sys.argv[1])
