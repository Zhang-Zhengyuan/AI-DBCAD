"""Close client-side finish calls without pretending an interrupted tool ran."""
import copy


def close_tool_messages(messages, *, allow_unknown=False):
    result, pending = [], {}

    def close():
        for identifier, name in pending.items():
            if name != "finish" and not allow_unknown:
                raise ValueError("Unresolved tool result; reconcile interrupted execution before resuming")
            result.append({"role": "tool", "tool_call_id": identifier, "content":
                "Client-side finish acknowledged; no geometry operation."
                if name == "finish" else
                "Execution was interrupted. Outcome UNKNOWN; no automatic replay. The new stage must inspect the authoritative current scene."})
        pending.clear()

    for message in copy.deepcopy(messages):
        if message.get("role") != "tool":
            close()
        else:
            pending.pop(message.get("tool_call_id"), None)
        result.append(message)
        if message.get("role") == "assistant":
            pending.update({call["id"]: call["function"]["name"] for call in message.get("tool_calls") or []})
    close()
    return result
